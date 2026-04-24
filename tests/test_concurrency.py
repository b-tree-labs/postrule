# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Red-bar concurrency tests — F1-F4 from v1-readiness.md.

These tests fail on the current code. Session 2 of the v1 fix-sprint
fixes them. Each class documents the exact bug it exposes and the
path that fixes it. When a test flips green, that bug is closed.

Bugs covered:

- **F1 shadow-stash cross-contamination** — single-slot instance
  fields (``_last_shadow`` / ``_last_ml`` / ``_last_rule_output`` /
  ``_last_action``) are stomped by thread-interleaved
  classify / record_verdict, silently attaching one request's
  shadow observations to another request's verdict record.
- **F2 breaker race** — check-then-set of ``_circuit_tripped`` in
  ``_classify_impl`` phase ML_PRIMARY lacks a lock; N concurrent
  failures all call the broken ML head instead of the first tripping
  the breaker and the rest short-circuiting to the rule.
- **F4a ResilientStorage partial-drain duplicates** —
  ``_try_recover`` clears the fallback per-switch only after the
  whole switch's records are drained; if the primary fails mid-list,
  the next drain replays records the primary already holds.
- **F4b ResilientStorage silent fallback eviction** — fallback is a
  bounded FIFO; records evicted at cap are counted as
  ``degraded_writes`` but disappear from the recoverable set.

Not covered here (see v1-readiness.md §2 finding #16): the
``_records_since_advance_check`` read-modify-write race and
``config.starting_phase`` mid-classify mutation. Both are real
thread-safety gaps that a single ``threading.RLock`` on
``LearnedSwitch`` closes, but under CPython's GIL neither produces a
reliable user-observable failure in a test harness — they remain
defensive fixes rather than red-bar ones.

These tests intentionally stress timing. When they flap on slow CI,
bump the iteration counts; don't weaken the invariants.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from dendra import (
    BoundedInMemoryStorage,
    ClassificationRecord,
    LearnedSwitch,
    MLPrediction,
    Phase,
    ResilientStorage,
    StorageBase,
    SwitchConfig,
    Verdict,
)
from dendra.models import ModelPrediction

pytestmark = [
    pytest.mark.concurrency,
    # ResilientStorage emits UserWarnings on enter/exit of degraded mode.
    pytest.mark.filterwarnings("ignore::UserWarning"),
]


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------


class _EchoModel:
    """ModelClassifier that echoes back a per-input label and confidence.

    Used to prove shadow-stash integrity: every classify call produces
    a distinctive ``model_output`` derivable from the input. If
    ``record_verdict`` later attaches a different input's shadow to
    this record, the assertion fails.

    The microsleep inside classify is deliberate — it encourages the
    scheduler to interleave threads in the window between the model
    call and the worker's record_verdict, widening the race so the
    single-slot ``_last_shadow`` bug surfaces under CPython's GIL.
    """

    def classify(self, input, labels):
        time.sleep(0.0001)  # 100µs — gives the scheduler room to swap.
        return ModelPrediction(
            label=f"shadow-{input}",
            confidence=0.5 + (hash(input) % 100) / 1000.0,
        )




class _FlakyMLHead:
    """ML head that always raises. Counts calls for breaker-race detection."""

    def __init__(self, barrier: threading.Barrier | None = None) -> None:
        self.calls: int = 0
        self._lock = threading.Lock()
        self._barrier = barrier

    def fit(self, records):
        pass

    def predict(self, input, labels):
        with self._lock:
            self.calls += 1
        if self._barrier is not None:
            # Make all N threads converge inside predict() before any
            # of them gets to raise + trip — turns the race into a
            # repeatable phenomenon instead of a probabilistic flap.
            self._barrier.wait(timeout=5.0)
        raise RuntimeError("ml head is down")

    def model_version(self):
        return "flaky"


def _rule(x):
    return f"rule-{x}"


# ---------------------------------------------------------------------------
# F1 — shadow-stash cross-contamination
# ---------------------------------------------------------------------------


class TestShadowStashIntegrity:
    def test_concurrent_classify_plus_record_verdict_do_not_cross_contaminate(self):
        """Each record's model_output must match its own input's shadow.

        F1: today ``_last_shadow`` is a single instance slot. When
        thread A runs classify(a) then record_verdict(a), a
        concurrent thread B running classify(b) between A's classify
        and A's record_verdict will have overwritten _last_shadow
        with (shadow-b, ...), and A's record gets B's shadow data.
        """
        sw = LearnedSwitch(
            rule=_rule,
            name="test_f1_shadow",
            author="test",
            config=SwitchConfig(
                starting_phase=Phase.MODEL_PRIMARY,
                confidence_threshold=0.0,  # always accept the model
                auto_record=False,
                auto_advance=False,
            ),
            model=_EchoModel(),
            storage=BoundedInMemoryStorage(),
        )

        N = 100

        def worker(token: int) -> None:
            sw.classify(token)
            # Yield — encourages another thread's classify to land
            # between this thread's classify and its record_verdict.
            time.sleep(0)
            sw.record_verdict(
                input=token,
                label=f"rule-{token}",
                outcome=Verdict.CORRECT.value,
                source="model",
            )

        with ThreadPoolExecutor(max_workers=32) as pool:
            list(pool.map(worker, range(N)))

        records = sw.storage.load_records(sw.name)
        # Every record must have its OWN shadow attached.
        contaminated = [
            r for r in records
            if r.model_output != f"shadow-{r.input}"
        ]
        assert not contaminated, (
            f"{len(contaminated)}/{len(records)} records have shadow_output "
            f"that does not match their own input. Example: "
            f"input={contaminated[0].input!r} "
            f"model_output={contaminated[0].model_output!r}"
        )


# ---------------------------------------------------------------------------
# F2 — breaker race
# ---------------------------------------------------------------------------


class TestBreakerTripIsAtomic:
    def test_concurrent_failures_trip_breaker_exactly_once(self):
        """First failing classify should trip the breaker; the rest fall back.

        F2: today the ``if self._circuit_tripped`` / ``self._circuit_tripped
        = True`` pair in ``_classify_impl`` ML_PRIMARY branch is not
        protected by a lock. N concurrent threads each observe the
        breaker untripped, each call the broken ML head, each then
        set tripped=True. The promise "one failure opens the circuit"
        is silently broken.
        """
        N = 32
        barrier = threading.Barrier(N)
        head = _FlakyMLHead(barrier=barrier)

        sw = LearnedSwitch(
            rule=_rule,
            name="test_f2_breaker",
            author="test",
            config=SwitchConfig(
                starting_phase=Phase.ML_PRIMARY,
                auto_record=False,
                auto_advance=False,
            ),
            ml_head=head,
            storage=BoundedInMemoryStorage(),
        )

        def worker(x: int) -> str:
            return sw.classify(x).source

        with ThreadPoolExecutor(max_workers=N) as pool:
            sources = list(pool.map(worker, range(N)))

        # Every call falls through to the rule (good — the breaker protects).
        assert all(s == "rule_fallback" for s in sources), sources

        # But the ML head should only be called ONCE: the first to trip it.
        # Today this is N, which means "breaker race" is silently turning the
        # outage into an N-request storm on the broken dependency.
        assert head.calls == 1, (
            f"Expected ML head called exactly 1× (first failure trips the "
            f"breaker, rest short-circuit). Got {head.calls} calls — the "
            f"breaker did not serialize."
        )


# ---------------------------------------------------------------------------
# F4 — ResilientStorage partial drain + silent fallback eviction
# ---------------------------------------------------------------------------


class _ToggleStorage(StorageBase):
    """Primary that can be failed / healed and counts appends by switch.

    Supports a ``fail_after`` mode: heal the primary, then fail on the
    Nth subsequent append from the drain path. Lets us reproduce the
    "partial drain mid-switch" bug deterministically.
    """

    def __init__(self) -> None:
        self.fail: bool = False
        self.fail_after: int | None = None
        self._since_heal: int = 0
        self._log: dict[str, list[ClassificationRecord]] = {}

    def append_record(self, switch_name, record):
        if self.fail:
            raise OSError("primary down")
        self._since_heal += 1
        if self.fail_after is not None and self._since_heal > self.fail_after:
            raise OSError("primary flapped mid-drain")
        self._log.setdefault(switch_name, []).append(record)

    def load_records(self, switch_name):
        return list(self._log.get(switch_name, []))

    def heal(self) -> None:
        self.fail = False
        self._since_heal = 0


def _rec(label: str = "x") -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=time.time(),
        input="i",
        label=label,
        outcome=Verdict.CORRECT.value,
        source="rule",
        confidence=1.0,
    )


class TestResilientStoragePartialDrain:
    def test_mid_drain_failure_does_not_duplicate_on_next_drain(self):
        """When the primary fails mid-replay, the next replay must not duplicate.

        F4 (partial drain): ``_try_recover`` writes all of switch A's
        fallback records into primary, then calls
        ``_clear_fallback_for(A)`` only if no exception fired during
        the replay. But the per-record loop can succeed for 5 of 10
        records, then fail; the exception aborts the drain with 5
        records in primary AND 10 in fallback. The next probe
        replays all 10, producing 5 duplicates in primary.
        """
        primary = _ToggleStorage()
        r = ResilientStorage(primary, recovery_probe_every=5)

        # Fail the primary and queue 10 records into fallback.
        primary.fail = True
        for i in range(10):
            r.append_record("s", _rec(f"r{i}"))

        # Heal primary but rig it to fail after it has accepted 5
        # replayed records. This produces a partial-drain failure.
        primary.heal()
        primary.fail_after = 5

        r.drain()  # first drain: 5 land in primary, then blowup.

        # Clear the fail_after and drain again.
        primary.fail_after = None
        r.drain()

        labels = [rec.label for rec in primary.load_records("s")]
        assert len(labels) == 10, (
            f"Expected 10 records in primary after heal+drain cycle; "
            f"got {len(labels)}: {labels}. Partial-drain left "
            f"fallback intact and the second drain re-appended "
            f"records the primary already had."
        )
        # And must be in append order, no dupes.
        assert labels == [f"r{i}" for i in range(10)], (
            f"Expected labels r0..r9; got {labels} — duplicate records "
            f"from the partial-drain race."
        )

    def test_silent_fallback_eviction_is_surfaced(self):
        """degraded_writes must not double-count records that evict silently.

        F4 (silent eviction): while degraded, every fallback append
        increments ``_degraded_writes`` — including ones the bounded
        fallback FIFO-evicts. Result: ``degraded_writes`` claims N
        records are preserved in the audit chain, but fewer than N
        are actually recoverable on drain.
        """
        primary = _ToggleStorage()
        primary.fail = True
        # Fallback capped at 3 records; we'll write 10.
        fallback = BoundedInMemoryStorage(max_records=3)
        r = ResilientStorage(primary, fallback=fallback)

        for i in range(10):
            r.append_record("s", _rec(f"r{i}"))

        # Today: degraded_writes == 10, fallback holds 3.
        # The contract we want: either (a) degraded_writes reflects
        # what survived, or (b) a separate ``evicted`` counter surfaces
        # the drop so the audit chain isn't silently lying.
        surviving = len(r.fallback.load_records("s"))
        assert surviving == 3, f"fallback cap is 3, got {surviving}"

        # The failing invariant: either a counter must exist that
        # reports 7 evictions, OR degraded_writes must equal the
        # 3 surviving records. Today neither is true.
        evicted_attr = getattr(r, "degraded_evictions", None)
        assert (
            r.degraded_writes == surviving
            or (evicted_attr is not None and evicted_attr == 10 - surviving)
        ), (
            f"Audit-chain lie detected. degraded_writes={r.degraded_writes}, "
            f"surviving in fallback={surviving}, "
            f"degraded_evictions={evicted_attr!r}. The counters must "
            f"either agree with reality or explicitly surface evictions."
        )
