# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Hot-path perf tests — dispatch / classify / storage write.

Targets are the authoritative numbers from the v1 perf spec. Each test
records its measured median + p95 to ``baselines/<name>.json``. The
reporter at session end prints a measured-vs-target histogram so we
know the framework's tax across phases at a glance.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from dendra import (
    BoundedInMemoryStorage,
    ClassificationRecord,
    FileStorage,
    LearnedSwitch,
    MLPrediction,
    Phase,
    SwitchConfig,
)
from dendra.models import ModelPrediction
from tests.perf.conftest import measure, perf_test  # noqa: TID252

pytestmark = pytest.mark.perf


# ---------------------------------------------------------------------------
# Stubs — return instantly so we measure framework overhead, not models.
# ---------------------------------------------------------------------------


class _StubLM:
    """ModelClassifier that returns a fixed prediction with no I/O."""

    def classify(self, input, labels):  # noqa: A002
        return ModelPrediction(label="a", confidence=0.95)


class _StubMLHead:
    """MLHead that returns a fixed prediction with no I/O."""

    def fit(self, records):  # pragma: no cover - perf path doesn't fit
        pass

    def predict(self, input, labels):  # noqa: A002
        return MLPrediction(label="a", confidence=0.92)

    def model_version(self):
        return "stub"


def _rule(x: str) -> str:
    return "a" if "a" in x else "b"


_INPUT = "abcdef"


# Per-spec: hot-path micro-benchmarks have a 30% tolerance because
# sub-microsecond ops are dominated by scheduler jitter.
_HOT_PATH_TOLERANCE = 0.30


# ---------------------------------------------------------------------------
# 1. Raw Python call baseline — for the histogram comparison.
# ---------------------------------------------------------------------------


@perf_test(tolerance=_HOT_PATH_TOLERANCE)
def test_raw_python_call_baseline(perf_record):
    """Baseline: how long does a bare Python function call take?

    Establishes the floor against which dispatch / classify overhead
    is reported in the histogram.
    """
    stats = measure(lambda: _rule(_INPUT), n=5000, warmup=500)
    perf_record(
        "raw_python_call",
        stats,
        target=200.0,  # ns
    )


# ---------------------------------------------------------------------------
# 2. dispatch() at Phase.RULE
# ---------------------------------------------------------------------------


@perf_test(tolerance=_HOT_PATH_TOLERANCE)
def test_dispatch_phase_rule(perf_record):
    """Target: under 50µs median, under 200µs p95."""
    sw = LearnedSwitch(
        rule=_rule,
        name="perf_disp_rule",
        author="perf",
        config=SwitchConfig(
            starting_phase=Phase.RULE,
            auto_record=False,
            auto_advance=False,
        ),
    )
    stats = measure(lambda: sw.dispatch(_INPUT), n=5000, warmup=500)
    perf_record(
        "dispatch_phase_rule",
        stats,
        target=50_000.0,  # 50µs in ns
    )
    # Spec ceiling check (separate from baseline regression).
    assert stats["median"] < 50_000, (
        f"dispatch.RULE median {stats['median']:.0f}ns exceeds 50µs target."
    )
    assert stats["p95"] < 200_000, (
        f"dispatch.RULE p95 {stats['p95']:.0f}ns exceeds 200µs target."
    )


# ---------------------------------------------------------------------------
# 3. classify() at Phase.RULE
# ---------------------------------------------------------------------------


@perf_test(tolerance=_HOT_PATH_TOLERANCE)
def test_classify_phase_rule(perf_record):
    """Target: under 50µs median, under 200µs p95."""
    sw = LearnedSwitch(
        rule=_rule,
        name="perf_class_rule",
        author="perf",
        config=SwitchConfig(
            starting_phase=Phase.RULE,
            auto_record=False,
            auto_advance=False,
        ),
    )
    stats = measure(lambda: sw.classify(_INPUT), n=5000, warmup=500)
    perf_record(
        "classify_phase_rule",
        stats,
        target=50_000.0,
    )
    assert stats["median"] < 50_000, (
        f"classify.RULE median {stats['median']:.0f}ns exceeds 50µs target."
    )
    assert stats["p95"] < 200_000, (
        f"classify.RULE p95 {stats['p95']:.0f}ns exceeds 200µs target."
    )


# ---------------------------------------------------------------------------
# 4. dispatch() at Phase.MODEL_PRIMARY with a stub model
# ---------------------------------------------------------------------------


@perf_test(tolerance=_HOT_PATH_TOLERANCE)
def test_dispatch_phase_model_primary(perf_record):
    """Stub LM returns instantly. Measures framework overhead, not the model."""
    sw = LearnedSwitch(
        rule=_rule,
        name="perf_disp_model",
        author="perf",
        labels=["a", "b"],
        config=SwitchConfig(
            starting_phase=Phase.MODEL_PRIMARY,
            auto_record=False,
            auto_advance=False,
        ),
        model=_StubLM(),
    )
    stats = measure(lambda: sw.dispatch(_INPUT), n=5000, warmup=500)
    perf_record(
        "dispatch_phase_model_primary",
        stats,
        target=50_000.0,
    )


# ---------------------------------------------------------------------------
# 5. dispatch() at Phase.ML_PRIMARY with a stub ML head
# ---------------------------------------------------------------------------


@perf_test(tolerance=_HOT_PATH_TOLERANCE)
def test_dispatch_phase_ml_primary(perf_record):
    """ML_PRIMARY is gated by safety_critical; we use ML_WITH_FALLBACK
    for the framework-overhead probe (same hot-path code, just
    keeps the rule on standby in case the ML head explodes)."""
    sw = LearnedSwitch(
        rule=_rule,
        name="perf_disp_ml",
        author="perf",
        labels=["a", "b"],
        config=SwitchConfig(
            starting_phase=Phase.ML_WITH_FALLBACK,
            auto_record=False,
            auto_advance=False,
        ),
        ml_head=_StubMLHead(),
    )
    stats = measure(lambda: sw.dispatch(_INPUT), n=5000, warmup=500)
    perf_record(
        "dispatch_phase_ml_primary",
        stats,
        target=50_000.0,
    )


# ---------------------------------------------------------------------------
# 6. async adispatch() vs sync dispatch() — target: under 2x sync.
# ---------------------------------------------------------------------------


@perf_test(tolerance=_HOT_PATH_TOLERANCE)
def test_adispatch_overhead_vs_sync(perf_record):
    """Async path delegates to ``asyncio.to_thread`` — measure
    the round-trip overhead of one thread hop."""
    sw = LearnedSwitch(
        rule=_rule,
        name="perf_adisp",
        author="perf",
        config=SwitchConfig(
            starting_phase=Phase.RULE,
            auto_record=False,
            auto_advance=False,
        ),
    )

    async def _one() -> None:
        await sw.adispatch(_INPUT)

    def _run() -> None:
        asyncio.run(_one())

    stats = measure(_run, n=200, warmup=20)
    perf_record(
        "adispatch_overhead",
        stats,
        target=2_000_000.0,  # 2ms — bounded but not micro; one event-loop spin-up
    )


# ---------------------------------------------------------------------------
# 7. Storage write latency — BoundedInMemoryStorage
# ---------------------------------------------------------------------------


@perf_test(tolerance=_HOT_PATH_TOLERANCE)
def test_storage_write_bounded_inmemory(perf_record):
    """Target: under 10µs."""
    storage = BoundedInMemoryStorage()
    rec = ClassificationRecord(
        timestamp=time.time(),
        input="hello",
        label="a",
        outcome="unknown",
        source="rule",
        confidence=1.0,
    )
    stats = measure(lambda: storage.append_record("s1", rec), n=5000, warmup=500)
    perf_record(
        "storage_write_bounded_inmemory",
        stats,
        target=10_000.0,  # 10µs
    )
    assert stats["median"] < 10_000, (
        f"BoundedInMemoryStorage median {stats['median']:.0f}ns exceeds 10µs."
    )


# ---------------------------------------------------------------------------
# 8. Storage write latency — FileStorage(no batch, no fsync)
# ---------------------------------------------------------------------------


@perf_test(tolerance=_HOT_PATH_TOLERANCE)
def test_storage_write_filestorage_unbatched(perf_record, tmp_path: Path):
    """Target: under 200µs.

    Flock + write + (no fsync) per call. Per the spec, this is a
    measure of "single-record latency" with the cheapest durable
    backend Dendra ships.
    """
    fs = FileStorage(tmp_path / "fs", batching=False, fsync=False)
    try:
        rec = ClassificationRecord(
            timestamp=time.time(),
            input="hello",
            label="a",
            outcome="unknown",
            source="rule",
            confidence=1.0,
        )
        # 500-warmup is too aggressive for ms-scale paths; use 100.
        stats = measure(lambda: fs.append_record("s1", rec), n=500, warmup=50)
    finally:
        fs.close()
    perf_record(
        "storage_write_filestorage_unbatched",
        stats,
        target=200_000.0,  # 200µs
    )
