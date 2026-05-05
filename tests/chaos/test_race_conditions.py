# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Race-condition chaos: every concurrent path the production hot-path
hits during normal operation. Each test pins one race; under CPython's
GIL most races require deliberate widening (sleeps, barriers) to be
reproducible.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from dendra import (
    BoundedInMemoryStorage,
    ClassificationRecord,
    FileStorage,
    LearnedSwitch,
    Phase,
    SwitchConfig,
    Verdict,
)
from dendra.gates import GateDecision
from dendra.lifters.evidence import lift_evidence


def _rec(label: str = "x") -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=time.time(),
        input="i",
        label=label,
        outcome=Verdict.CORRECT.value,
        source="rule",
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# Concurrent dispatch on the same switch
# ---------------------------------------------------------------------------


class TestConcurrentDispatch:
    def test_n_threads_dispatch_same_switch_no_lost_writes(self, tmp_path):
        """N threads concurrently dispatching: every record persists exactly once.

        Auto-record produces one outcome row per dispatch. With N=8 and
        100 calls per thread we expect exactly 800 records in storage.
        Lost or duplicated rows are the failure mode.
        """
        sw = LearnedSwitch(
            rule=lambda x: f"rule-{x % 3}",
            name="race_dispatch",
            author="t",
            labels=["rule-0", "rule-1", "rule-2"],
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=True,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=FileStorage(tmp_path / "s"),
        )

        N = 8
        K = 100
        errors: list[BaseException] = []

        def worker(tid: int) -> None:
            try:
                for i in range(K):
                    sw.dispatch(tid * K + i)
            except BaseException as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=N) as pool:
            list(pool.map(worker, range(N)))

        assert not errors, errors[0]
        records = sw._storage.load_records(sw.name)
        # Every input must appear at least once. Dedupe-via-input gives
        # us the count we expected, regardless of the per-record format.
        seen_inputs = {r.input for r in records}
        expected = set(range(N * K))
        missing = expected - seen_inputs
        assert not missing, f"{len(missing)} records lost to write race"
        assert len(records) == N * K, (
            f"expected {N * K} records; got {len(records)} "
            f"(dup count={len(records) - len(seen_inputs)})"
        )


# ---------------------------------------------------------------------------
# advance() racing dispatch
# ---------------------------------------------------------------------------


class _SlowEchoModel:
    """Model that sleeps inside classify so advance() has a real window
    to race against the in-flight dispatch.
    """

    def classify(self, input, labels):
        from dendra.models import ModelPrediction

        time.sleep(0.005)
        return ModelPrediction(label="model", confidence=0.99)


class TestAdvanceVsDispatch:
    def test_advance_during_dispatch_does_not_corrupt(self, tmp_path):
        """advance() called mid-dispatch: dispatch completes against the
        phase it observed; subsequent dispatches see the new phase.

        The test classifies in MODEL_PRIMARY (where the slow model has
        time to be interrupted), then flips to ML_SHADOW from a sibling
        thread. The in-flight call must not crash; the next call sees
        the new phase.
        """
        # We need an ML head for ML_SHADOW. Use a stub that always
        # returns a deterministic label.
        from dendra.ml import MLPrediction

        class StubMLHead:
            def fit(self, records):
                pass

            def predict(self, input, labels):
                return MLPrediction(label="ml", confidence=0.9)

            def model_version(self):
                return "stub"

        sw = LearnedSwitch(
            rule=lambda x: "rule",
            name="advance_vs_dispatch",
            author="t",
            labels=["rule", "model", "ml"],
            config=SwitchConfig(
                starting_phase=Phase.MODEL_PRIMARY,
                phase_limit=Phase.ML_SHADOW,
                confidence_threshold=0.0,
                auto_record=False,
                auto_advance=False,
                auto_demote=False,
            ),
            model=_SlowEchoModel(),
            ml_head=StubMLHead(),
            storage=BoundedInMemoryStorage(),
        )

        # Force the gate to advance unconditionally on the next call.
        sw.config.gate = type(
            "AlwaysAdvance",
            (),
            {
                "evaluate": lambda self, recs, cur, tgt: GateDecision(
                    target_better=True, rationale="forced"
                )
            },
        )()

        result_box: list = []
        error_box: list[BaseException] = []

        def dispatcher() -> None:
            try:
                # Slow model gives the advancer a window to flip the phase.
                result_box.append(sw.dispatch("hello"))
            except BaseException as e:
                error_box.append(e)

        def advancer() -> None:
            time.sleep(0.001)  # let dispatcher enter classify
            sw.advance()

        t1 = threading.Thread(target=dispatcher)
        t2 = threading.Thread(target=advancer)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not error_box, error_box[0]
        # The in-flight dispatch finished cleanly with SOME label.
        assert result_box and result_box[0].label is not None
        # The next dispatch must see the new phase.
        assert sw.phase() is Phase.ML_SHADOW
        next_result = sw.dispatch("again")
        assert next_result.phase is Phase.ML_SHADOW


# ---------------------------------------------------------------------------
# Auto-advance gate firing during dispatch
# ---------------------------------------------------------------------------


class TestAutoAdvanceDuringDispatch:
    def test_auto_advance_completes_cleanly(self, tmp_path):
        """auto_advance fires from inside record_verdict mid-traffic.

        We drive 1000 record_verdict calls with auto_advance_interval=10
        and a forced-advance gate. No exceptions; phase ends past start.
        """
        sw = LearnedSwitch(
            rule=lambda x: "rule",
            name="auto_advance_chaos",
            author="t",
            labels=["rule", "model", "ml"],
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=False,
                auto_advance=True,
                auto_advance_interval=10,
                auto_demote=False,
                phase_cooldown_records=0,
            ),
            storage=BoundedInMemoryStorage(),
        )

        # Force advance to fire on every check so the loop exercises the
        # advance-during-record_verdict path many times.
        sw.config.gate = type(
            "Always",
            (),
            {
                "evaluate": lambda self, recs, cur, tgt: GateDecision(
                    target_better=True, rationale="forced"
                )
            },
        )()

        N = 16
        K = 50

        def worker(tid: int) -> None:
            for i in range(K):
                sw.record_verdict(
                    input=f"{tid}-{i}",
                    label="rule",
                    outcome=Verdict.CORRECT.value,
                    source="rule",
                    confidence=1.0,
                )

        with ThreadPoolExecutor(max_workers=N) as pool:
            list(pool.map(worker, range(N)))

        # Phase advanced from RULE; no specific final phase asserted (the
        # cooldown + gate interaction is not deterministic), only that we
        # got SOMEWHERE without crashing.
        assert sw.phase() is not Phase.RULE


# ---------------------------------------------------------------------------
# Batched flush vs sync write race
# ---------------------------------------------------------------------------


class TestBatchedVsSyncWriteRace:
    def test_batched_storage_no_lost_writes_under_mixed_load(self, tmp_path):
        """All appends through a batched FileStorage must persist.

        Writers thrash from many threads; close() drains. After close,
        load_records must see every record.
        """
        store = FileStorage(tmp_path / "store", batching=True, batch_size=8, flush_interval_ms=5)
        N = 8
        K = 125

        def worker(tid: int) -> None:
            for i in range(K):
                store.append_record("s", _rec(f"t{tid}-r{i}"))

        try:
            with ThreadPoolExecutor(max_workers=N) as pool:
                list(pool.map(worker, range(N)))
        finally:
            store.close()

        # Reopen and verify durability.
        store2 = FileStorage(tmp_path / "store")
        records = store2.load_records("s")
        labels = {r.label for r in records}
        expected = {f"t{t}-r{i}" for t in range(N) for i in range(K)}
        missing = expected - labels
        assert not missing, f"{len(missing)} records lost across batched-flush race"


# ---------------------------------------------------------------------------
# Lifter races
# ---------------------------------------------------------------------------


class TestLifterConcurrentLifts:
    def test_two_threads_lift_same_source(self):
        """Concurrent lift_evidence on the same source: both succeed identically.

        The lifters operate on AST locally; they should be reentrant. The
        bug shape would be a shared mutable cache silently corrupting
        the output across threads.
        """
        src = """
def cls(x):
    if x > 0:
        return 'pos'
    return 'neg'
"""
        N = 16
        results: list[str] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker():
            try:
                out = lift_evidence(src, "cls")
                with lock:
                    results.append(out)
            except BaseException as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, errors[0]
        assert len(results) == N
        # All N outputs must be byte-identical (no shared mutable state).
        first = results[0]
        assert all(r == first for r in results), "lifter output diverged across threads"


# ---------------------------------------------------------------------------
# Switch lock re-entrancy under stress
# ---------------------------------------------------------------------------


class TestRecordVerdictAdvanceRecursion:
    def test_advance_called_inside_record_verdict_does_not_deadlock(self, tmp_path):
        """auto_advance triggers advance() from inside record_verdict's locked region.

        The switch uses RLock for exactly this reason. Confirm no deadlock
        when an inner advance() tries to take the same lock.
        """
        sw = LearnedSwitch(
            rule=lambda x: "rule",
            name="recursion_chaos",
            author="t",
            labels=["rule"],
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=False,
                auto_advance=True,
                auto_advance_interval=1,  # fire on every call
                auto_demote=False,
                phase_cooldown_records=0,
            ),
            storage=BoundedInMemoryStorage(),
        )

        # Force advance to fire (target_better=True).
        sw.config.gate = type(
            "Always",
            (),
            {
                "evaluate": lambda self, recs, cur, tgt: GateDecision(
                    target_better=True, rationale="forced"
                )
            },
        )()

        # 50 calls: every one triggers an inner advance(). The RLock must
        # allow re-entry; otherwise we deadlock and pytest-timeout fires.
        for i in range(50):
            sw.record_verdict(
                input=i,
                label="rule",
                outcome=Verdict.CORRECT.value,
                source="rule",
            )
