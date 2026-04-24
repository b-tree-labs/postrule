# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Pinned-latency regression tests — guard against perf regressions.

Numeric ranges derived from `scripts/run_v1_benchmarks.py` on the
reference machine (Apple M5 / Python 3.13 / macOS 26, see
`docs/working/v1-audit-benchmarks.md` for the source numbers).
Thresholds are set to **~2× observed p99** so the tests fail on real
regressions but don't flap on CI noise.

Run with:
    pytest -m benchmark tests/test_latency_pinned.py -v

The tests are marked ``benchmark`` so default ``pytest`` runs skip
them (they take ~15-45 seconds end-to-end and the numeric ranges
assume a dev machine, not a shared CI runner).

Every test re-measures using ``time.perf_counter_ns`` with a warmup
then asserts the p99 against a pinned ceiling. If the cell fails, the
error message includes the measured p50/p95/p99 so CI logs tell you
which bucket regressed.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from dendra import LearnedSwitch, MLPrediction, Phase, SwitchConfig
from dendra.core import ClassificationRecord
from dendra.gates import McNemarGate
from dendra.models import ModelPrediction
from dendra.storage import (
    BoundedInMemoryStorage,
    FileStorage,
    SqliteStorage,
)

pytestmark = pytest.mark.benchmark


# ---------------------------------------------------------------------------
# Stubs + helpers — same shape as scripts/run_v1_benchmarks.py
# ---------------------------------------------------------------------------


def _rule_atis(text: str) -> str:
    t = text.lower()
    if "fly" in t or "flight" in t:
        return "flight"
    if "ticket" in t or "fare" in t or "cost" in t:
        return "airfare"
    if "airline" in t:
        return "airline"
    return "flight"


class _StubLLM:
    def classify(self, input, labels):
        return ModelPrediction(label="flight", confidence=0.95)


class _StubMLHead:
    def fit(self, records):
        pass

    def predict(self, input, labels):
        return MLPrediction(label="flight", confidence=0.92)

    def model_version(self):
        return "stub"


INPUT = "i want to fly from boston to denver"

_COUNTER = 0


def _unique_name(prefix: str) -> str:
    global _COUNTER
    _COUNTER += 1
    return f"{prefix}_{_COUNTER}"


def _measure(fn: Callable[[], None], *, n: int, warmup: int = 500) -> dict[str, float]:
    """Return p50/p95/p99 in nanoseconds + the full assertion dict.

    Kept in-module (rather than imported from the benchmark script) so
    the test suite has zero scripts/ dependency and can run in an
    isolated install of dendra (pip install dendra[dev]).
    """
    for _ in range(warmup):
        fn()
    samples = [0] * n
    perf = time.perf_counter_ns
    for i in range(n):
        t0 = perf()
        fn()
        samples[i] = perf() - t0
    samples.sort()
    return {
        "p50_ns": samples[int(n * 0.50)],
        "p95_ns": samples[int(n * 0.95)],
        "p99_ns": samples[int(n * 0.99)],
        "min_ns": samples[0],
        "max_ns": samples[-1],
    }


def _assert_p99_below(stats: dict[str, float], limit_ns: float, *, cell: str) -> None:
    """Fail with a descriptive message if p99 exceeds the limit."""
    assert stats["p99_ns"] < limit_ns, (
        f"Latency regression in {cell}: "
        f"p50={stats['p50_ns']/1000:.2f}µs  "
        f"p95={stats['p95_ns']/1000:.2f}µs  "
        f"p99={stats['p99_ns']/1000:.2f}µs  "
        f"(limit: {limit_ns/1000:.2f}µs). "
        "See docs/working/v1-audit-benchmarks.md for the baseline."
    )


# ---------------------------------------------------------------------------
# classify() p99 ceilings — derived from baseline × 2 (approx)
# ---------------------------------------------------------------------------
#
# Baseline (2026-04-24, Apple M5 / Python 3.13):
#   Phase.RULE, auto_record=False: p99=0.67µs
#   Phase.RULE, auto_record=True:  p99=2.42µs
#   Phase.MODEL_PRIMARY, auto_record=False: p99=1.00µs
#   Phase.ML_WITH_FALLBACK, auto_record=False: p99=1.00µs
#
# Ceilings: 3-5x baseline p99 to absorb CI noise (CI machines are
# slower than Apple M5 local). CI failures indicate a real perf bug.


def test_classify_phase_rule_auto_record_off():
    sw = LearnedSwitch(
        rule=_rule_atis,
        name=_unique_name("pin_rule_off"),
        author="bench",
        config=SwitchConfig(starting_phase=Phase.RULE, auto_record=False, auto_advance=False),
    )
    stats = _measure(lambda: sw.classify(INPUT), n=5000)
    # Observed p99: 0.67µs; ceiling 3µs (4.5× headroom for CI).
    _assert_p99_below(stats, 3_000, cell="classify.RULE.auto_record=False")


def test_classify_phase_rule_auto_record_default():
    sw = LearnedSwitch(
        rule=_rule_atis,
        name=_unique_name("pin_rule_default"),
        author="bench",
        config=SwitchConfig(starting_phase=Phase.RULE, auto_advance=False),
    )
    stats = _measure(lambda: sw.classify(INPUT), n=5000)
    # Observed p99: 2.42µs; ceiling 10µs (auto_record path includes
    # ClassificationRecord alloc + deque append — more sensitive to noise).
    _assert_p99_below(stats, 10_000, cell="classify.RULE.auto_record=True")


def test_classify_phase_model_primary():
    sw = LearnedSwitch(
        rule=_rule_atis,
        name=_unique_name("pin_mp"),
        author="bench",
        labels=["flight", "airfare", "airline"],
        config=SwitchConfig(
            starting_phase=Phase.MODEL_PRIMARY,
            auto_record=False,
            auto_advance=False,
        ),
        model=_StubLLM(),
    )
    stats = _measure(lambda: sw.classify(INPUT), n=5000)
    # Observed p99: 1.00µs (stub LLM); ceiling 5µs.
    _assert_p99_below(stats, 5_000, cell="classify.MODEL_PRIMARY.auto_record=False")


def test_classify_phase_ml_with_fallback():
    sw = LearnedSwitch(
        rule=_rule_atis,
        name=_unique_name("pin_mlwf"),
        author="bench",
        labels=["flight", "airfare", "airline"],
        config=SwitchConfig(
            starting_phase=Phase.ML_WITH_FALLBACK,
            auto_record=False,
            auto_advance=False,
        ),
        ml_head=_StubMLHead(),
    )
    stats = _measure(lambda: sw.classify(INPUT), n=5000)
    # Observed p99: 1.00µs (stub ML head); ceiling 5µs.
    _assert_p99_below(stats, 5_000, cell="classify.ML_WITH_FALLBACK.auto_record=False")


# ---------------------------------------------------------------------------
# record_verdict() ceilings
# ---------------------------------------------------------------------------
#
# Baseline:
#   BoundedInMemoryStorage: p99=1.96µs
#   FileStorage fsync=False: p99=3.73ms
#   FileStorage fsync=True:  p99=4.02ms
#   SqliteStorage NORMAL:    p99=1.89ms


def test_record_verdict_bounded_inmemory():
    sw = LearnedSwitch(
        rule=_rule_atis,
        name=_unique_name("pin_rv_bim"),
        author="bench",
        config=SwitchConfig(starting_phase=Phase.RULE, auto_record=False, auto_advance=False),
        storage=BoundedInMemoryStorage(),
    )
    stats = _measure(
        lambda: sw.record_verdict(input=INPUT, label="flight", outcome="unknown"),
        n=5000,
    )
    # Observed p99: 1.96µs; ceiling 10µs.
    _assert_p99_below(stats, 10_000, cell="record_verdict.BoundedInMemory")


def test_record_verdict_file_storage():
    with tempfile.TemporaryDirectory(prefix="dendra_test_lat_") as td:
        sw = LearnedSwitch(
            rule=_rule_atis,
            name=_unique_name("pin_rv_fs"),
            author="bench",
            config=SwitchConfig(starting_phase=Phase.RULE, auto_record=False, auto_advance=False),
            storage=FileStorage(Path(td) / "fs"),
        )
        stats = _measure(
            lambda: sw.record_verdict(input=INPUT, label="flight", outcome="unknown"),
            n=500,  # FileStorage is ms-scale — fewer iterations.
            warmup=50,
        )
    # Observed p99: 3.73ms; ceiling 15ms (disk cache / flock variance).
    _assert_p99_below(stats, 15_000_000, cell="record_verdict.FileStorage")


def test_record_verdict_sqlite_storage():
    with tempfile.TemporaryDirectory(prefix="dendra_test_lat_") as td:
        sw = LearnedSwitch(
            rule=_rule_atis,
            name=_unique_name("pin_rv_sq"),
            author="bench",
            config=SwitchConfig(starting_phase=Phase.RULE, auto_record=False, auto_advance=False),
            storage=SqliteStorage(Path(td) / "log.db"),
        )
        stats = _measure(
            lambda: sw.record_verdict(input=INPUT, label="flight", outcome="unknown"),
            n=500,
            warmup=50,
        )
    # Observed p99: 1.89ms; ceiling 10ms.
    _assert_p99_below(stats, 10_000_000, cell="record_verdict.SqliteStorage")


# ---------------------------------------------------------------------------
# advance() / gate ceilings
# ---------------------------------------------------------------------------
#
# Baseline:
#   advance() at 10k log (BIM storage, McNemarGate): p99=2.39ms
#   McNemarGate on 10k records direct: p99=2.42ms


def test_mcnemar_gate_on_10k_records():
    from dendra.core import Verdict as _V  # noqa: N813

    records: list[ClassificationRecord] = []
    for i in range(10_000):
        outcome = _V.CORRECT.value if i % 2 == 0 else _V.INCORRECT.value
        records.append(
            ClassificationRecord(
                timestamp=1_700_000_000.0 + i,
                input=INPUT,
                label="flight",
                outcome=outcome,
                source="rule",
                confidence=1.0,
                rule_output="flight" if i % 3 == 0 else "airfare",
                model_output="flight" if i % 4 != 0 else "airfare",
                model_confidence=0.9,
            )
        )
    gate = McNemarGate()
    # Gate eval is ms-scale; small n suffices.
    stats = _measure(
        lambda: gate.evaluate(records, Phase.RULE, Phase.MODEL_SHADOW),
        n=100,
        warmup=10,
    )
    # Observed p99: 2.42ms; ceiling 10ms.
    _assert_p99_below(stats, 10_000_000, cell="McNemarGate.log_size=10000")


# ---------------------------------------------------------------------------
# dispatch() overhead ceiling
# ---------------------------------------------------------------------------
#
# Baseline: dispatch.RULE.with_labeled_actions p99=1.29µs


def test_dispatch_overhead():
    def _action(x):
        return None

    sw = LearnedSwitch(
        rule=_rule_atis,
        name=_unique_name("pin_dispatch"),
        author="bench",
        labels={"flight": _action, "airfare": _action, "airline": _action},
        config=SwitchConfig(starting_phase=Phase.RULE, auto_record=False, auto_advance=False),
    )
    stats = _measure(lambda: sw.dispatch(INPUT), n=5000)
    # Observed p99: 1.29µs; ceiling 8µs (action invocation + perf_counter × 2).
    _assert_p99_below(stats, 8_000, cell="dispatch.RULE")


# ---------------------------------------------------------------------------
# Regression pins for v1 fix-sprint findings #28-31.
# ---------------------------------------------------------------------------
#
# These ceilings are set ABOVE the current (regressed) baselines so the
# tests pass today. Session 3 of the fix sprint drives the numbers down;
# when fixes land, tighten these ceilings to the new observed p99 × 2.
#
# Cross-ref: docs/working/v1-readiness.md § 2, findings 28-31.


def test_classify_auto_record_plus_persist_file_storage():
    """Finding #29 — auto_record=True + FileStorage = 1,537× slowdown.

    Every classify() on the "production" recommendation (persist=True)
    opens a new fd + flocks + appends + closes. p99 observed: 4.48 ms.
    This pins that ceiling so further regressions are caught; Session 3
    drops it by two orders of magnitude via fd-pool + batched append.
    """
    with tempfile.TemporaryDirectory(prefix="dendra_pin_arps_") as td:
        sw = LearnedSwitch(
            rule=_rule_atis,
            name=_unique_name("pin_ar_file"),
            author="bench",
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=True,
                auto_advance=False,
            ),
            storage=FileStorage(Path(td) / "fs"),
        )
        stats = _measure(lambda: sw.classify(INPUT), n=300, warmup=50)
    # Observed p99: 4.48ms; ceiling 15ms (regression-guard; see #29).
    _assert_p99_below(
        stats, 15_000_000, cell="classify.RULE.auto_record+persist"
    )


def test_record_verdict_auto_advance_interval_boundary():
    """Finding #30 — auto_advance p99 spike at interval boundary.

    interval=100 means every 100th record_verdict triggers advance(),
    which walks the whole log. p99 observed: 287.2 µs — 164× the p50.
    Session 3 caches the gate decision between intervals.
    """
    sw = LearnedSwitch(
        rule=_rule_atis,
        name=_unique_name("pin_aa_boundary"),
        author="bench",
        config=SwitchConfig(
            starting_phase=Phase.RULE,
            auto_record=False,
            auto_advance=True,
            auto_advance_interval=100,
        ),
        storage=BoundedInMemoryStorage(),
    )
    stats = _measure(
        lambda: sw.record_verdict(
            input=INPUT, label="flight", outcome="unknown"
        ),
        n=5000,
    )
    # Observed p99: 287µs; ceiling 1.5ms (regression-guard; see #30).
    _assert_p99_below(
        stats, 1_500_000, cell="record_verdict.auto_advance_interval=100"
    )


def test_composite_gate_all_of_vs_mcnemar_alone():
    """Finding #31 — CompositeGate re-walks pairs per sub-gate.

    Each sub-gate inside CompositeGate.all_of currently re-extracts
    paired-correctness from the record list. For [McNemar, Accuracy]
    that's a 1.9× cost over McNemar alone (4.30 ms vs 2.24 ms p50).
    Session 3 hoists pair-extraction to gate entry.
    """
    from dendra.core import Verdict as _V  # noqa: N813
    from dendra.gates import AccuracyMarginGate, CompositeGate

    records: list[ClassificationRecord] = []
    for i in range(10_000):
        outcome = _V.CORRECT.value if i % 2 == 0 else _V.INCORRECT.value
        records.append(
            ClassificationRecord(
                timestamp=1_700_000_000.0 + i,
                input=INPUT,
                label="flight",
                outcome=outcome,
                source="rule",
                confidence=1.0,
                rule_output="flight" if i % 3 == 0 else "airfare",
                model_output="flight" if i % 4 != 0 else "airfare",
                model_confidence=0.9,
            )
        )
    gate = CompositeGate.all_of([McNemarGate(), AccuracyMarginGate()])
    stats = _measure(
        lambda: gate.evaluate(records, Phase.RULE, Phase.MODEL_SHADOW),
        n=100,
        warmup=10,
    )
    # Observed p99: ~4.5ms; ceiling 20ms (regression-guard; see #31).
    _assert_p99_below(
        stats, 20_000_000, cell="CompositeGate.all_of[McNemar,Accuracy]"
    )
