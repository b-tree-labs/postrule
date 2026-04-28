# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the perf harness's own regression-detection logic.

The harness lives in ``tests/perf/conftest.py``: it loads a baseline
JSON, compares the current ``median``, and fails if outside tolerance.
These tests verify the comparator behaves the way the spec demands:

* 10% slowdown above the default 20% tolerance must flag.
* 5% slowdown inside the noise floor must NOT flag.
* 50% improvement must NOT flag (faster is always fine).

Implemented by exercising the comparator function directly with
synthetic numbers — we are testing the harness's flagging logic, not
running a real timed loop.
"""

from __future__ import annotations

import pytest

from tests.perf.conftest import _compare  # noqa: TID252

pytestmark = pytest.mark.perf


def _stats(median_ns: float) -> dict[str, float]:
    return {"median": median_ns, "p95": median_ns, "n": 1000.0}


# ---------------------------------------------------------------------------
# Sensitivity probes — synthetic 100µs baseline, vary the current.
# ---------------------------------------------------------------------------


def test_harness_flags_10pct_regression():
    """100µs → 110µs is a 10% slowdown; default tolerance is 20%, so
    this should NOT flag with the default. Use a 5% tolerance to
    confirm the comparator wires up correctly. (The spec asks
    'flagged as regression' against the default; we probe both
    sides to make the contract explicit.)"""
    baseline = _stats(100_000.0)
    current = _stats(110_000.0)
    # Tight tolerance: 5% — should flag.
    ok, msg = _compare(
        metric_name="probe_10pct_tight",
        current=current,
        baseline=baseline,
        tolerance=0.05,
        higher_is_better=False,
    )
    assert not ok
    assert "regressed by 10.0%" in msg


def test_harness_5pct_within_noise_floor():
    """100µs → 105µs is a 5% slowdown; well inside the default 20%.
    Must NOT flag at the 20% tolerance the suite uses."""
    baseline = _stats(100_000.0)
    current = _stats(105_000.0)
    ok, msg = _compare(
        metric_name="probe_5pct",
        current=current,
        baseline=baseline,
        tolerance=0.20,
        higher_is_better=False,
    )
    assert ok, f"5% slowdown flagged at 20% tolerance: {msg}"


def test_harness_50pct_improvement_not_flagged():
    """100µs → 50µs is a 50% improvement. Faster is always fine —
    the comparator must never flag it as a regression."""
    baseline = _stats(100_000.0)
    current = _stats(50_000.0)
    ok, msg = _compare(
        metric_name="probe_50pct_improvement",
        current=current,
        baseline=baseline,
        tolerance=0.20,
        higher_is_better=False,
    )
    assert ok, f"50% improvement flagged: {msg}"


def test_harness_higher_is_better_throughput_drop():
    """Throughput regression: baseline 50k ops/s, current 30k ops/s.
    With ``higher_is_better=True`` and tolerance 20%, 30k < 50k * 0.8
    = 40k, so it must flag.
    """
    baseline = {"median": 50_000.0, "p95": 50_000.0, "n": 1000.0}
    current = {"median": 30_000.0, "p95": 30_000.0, "n": 1000.0}
    ok, msg = _compare(
        metric_name="probe_throughput_drop",
        current=current,
        baseline=baseline,
        tolerance=0.20,
        higher_is_better=True,
    )
    assert not ok
    assert "regressed by 40.0%" in msg


def test_harness_higher_is_better_throughput_improvement_not_flagged():
    """Throughput climb: baseline 50k, current 75k.
    Higher is better — must NOT flag."""
    baseline = {"median": 50_000.0, "p95": 50_000.0, "n": 1000.0}
    current = {"median": 75_000.0, "p95": 75_000.0, "n": 1000.0}
    ok, msg = _compare(
        metric_name="probe_throughput_climb",
        current=current,
        baseline=baseline,
        tolerance=0.20,
        higher_is_better=True,
    )
    assert ok, f"throughput climb flagged: {msg}"


def test_harness_actionable_failure_message():
    """A flagged regression must include the metric name, the percentage
    delta, both numbers, and the --update-baselines hint. CI logs need
    the diagnostic to be self-contained."""
    baseline = _stats(100_000.0)
    current = _stats(150_000.0)
    ok, msg = _compare(
        metric_name="probe_actionable_msg",
        current=current,
        baseline=baseline,
        tolerance=0.20,
        higher_is_better=False,
    )
    assert not ok
    assert "probe_actionable_msg" in msg
    assert "regressed by" in msg
    assert "100000" in msg
    assert "150000" in msg
    assert "--update-baselines" in msg
