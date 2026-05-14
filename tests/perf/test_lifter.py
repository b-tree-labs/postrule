# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Lifter perf tests — auto-lift work isn't free.

Measures branch lifter, evidence lifter, and Phase 5 hazard analyzer
on synthetic 100- and 1000-line functions, plus ``postrule analyze``
on the entire ``examples/`` directory.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import pytest

from postrule.analyzer import analyze, analyze_function_source
from postrule.lifters.branch import lift_branches
from postrule.lifters.evidence import lift_evidence
from tests.perf.conftest import perf_test  # noqa: TID252

pytestmark = pytest.mark.perf


# Some lifter probes recurse into deep elif chains; bump above the
# default 1000 so the 1000-line probe doesn't blow up.
_RECURSION_LIMIT = 5000


def _build_branch_func(n_branches: int, name: str = "triage") -> str:
    """Build a function with ``n_branches`` elif arms.

    Each arm is 2 lines (predicate + return); the function header and
    else-default add 3 more, so the total line count is roughly
    ``2 * n_branches + 3``.
    """
    lines = [f"def {name}(ticket):"]
    for i in range(n_branches):
        prefix = "if" if i == 0 else "elif"
        lines.append(f"    {prefix} ticket.severity == 'level_{i}':")
        lines.append(f"        return 'label_{i}'")
    lines.append("    else:")
    lines.append("        return 'default'")
    return "\n".join(lines)


def _build_evidence_func(n_branches: int, name: str = "gate") -> str:
    """Build an evidence-shaped function reading from a module-global dict.

    Same shape as :func:`_build_branch_func` but each predicate consults
    ``FEATURE_FLAGS["flag_<i>"]`` so the evidence lifter has hidden
    state to lift.
    """
    lines = [f"def {name}(text):"]
    for i in range(n_branches):
        prefix = "if" if i == 0 else "elif"
        lines.append(f"    {prefix} FEATURE_FLAGS['flag_{i}']:")
        lines.append(f"        return 'on_{i}'")
    lines.append("    else:")
    lines.append("        return 'off'")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1. lift_evidence on 100-line / 1000-line.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.30)
def test_lift_evidence_100_line(perf_record):
    src = _build_evidence_func(48)  # ~100 lines
    # Warmup to load AST + caches.
    for _ in range(3):
        lift_evidence(src, "gate")
    samples = []
    for _ in range(20):
        t0 = time.perf_counter_ns()
        lift_evidence(src, "gate")
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    median = float(statistics.median(samples))
    p95 = float(samples[int(len(samples) * 0.95) - 1])
    perf_record(
        "lift_evidence_100_line",
        {
            "median": median,
            "p95": p95,
            "min": float(samples[0]),
            "max": float(samples[-1]),
            "n": float(len(samples)),
        },
        target=50_000_000.0,  # 50ms
    )
    assert median < 50_000_000, (
        f"lift_evidence 100-line median {median / 1e6:.1f}ms exceeds 50ms target."
    )


@perf_test(tolerance=0.30)
def test_lift_evidence_1000_line(perf_record):
    sys.setrecursionlimit(_RECURSION_LIMIT)
    src = _build_evidence_func(498)  # ~1000 lines
    for _ in range(2):
        lift_evidence(src, "gate")
    samples = []
    for _ in range(5):
        t0 = time.perf_counter_ns()
        lift_evidence(src, "gate")
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    median = float(statistics.median(samples))
    p95 = float(samples[-1])
    perf_record(
        "lift_evidence_1000_line",
        {
            "median": median,
            "p95": p95,
            "min": float(samples[0]),
            "max": float(samples[-1]),
            "n": float(len(samples)),
        },
        target=500_000_000.0,  # 500ms
    )
    assert median < 500_000_000, (
        f"lift_evidence 1000-line median {median / 1e6:.1f}ms exceeds 500ms target."
    )


# ---------------------------------------------------------------------------
# 2. lift_branches on 100-line / 1000-line.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.30)
def test_lift_branches_100_line(perf_record):
    src = _build_branch_func(48)
    for _ in range(3):
        lift_branches(src, "triage")
    samples = []
    for _ in range(20):
        t0 = time.perf_counter_ns()
        lift_branches(src, "triage")
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    median = float(statistics.median(samples))
    p95 = float(samples[int(len(samples) * 0.95) - 1])
    perf_record(
        "lift_branches_100_line",
        {
            "median": median,
            "p95": p95,
            "min": float(samples[0]),
            "max": float(samples[-1]),
            "n": float(len(samples)),
        },
        target=50_000_000.0,
    )
    assert median < 50_000_000, (
        f"lift_branches 100-line median {median / 1e6:.1f}ms exceeds 50ms target."
    )


@perf_test(tolerance=0.30)
def test_lift_branches_1000_line(perf_record):
    sys.setrecursionlimit(_RECURSION_LIMIT)
    src = _build_branch_func(498)
    for _ in range(2):
        lift_branches(src, "triage")
    samples = []
    for _ in range(5):
        t0 = time.perf_counter_ns()
        lift_branches(src, "triage")
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    median = float(statistics.median(samples))
    p95 = float(samples[-1])
    perf_record(
        "lift_branches_1000_line",
        {
            "median": median,
            "p95": p95,
            "min": float(samples[0]),
            "max": float(samples[-1]),
            "n": float(len(samples)),
        },
        target=500_000_000.0,
    )
    assert median < 500_000_000, (
        f"lift_branches 1000-line median {median / 1e6:.1f}ms exceeds 500ms target."
    )


# ---------------------------------------------------------------------------
# 3. analyze_function_source (Phase 5 hazards) on a 1000-line function.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.30)
def test_analyze_function_source_1000_line(perf_record):
    sys.setrecursionlimit(_RECURSION_LIMIT)
    src = _build_branch_func(498)
    for _ in range(2):
        analyze_function_source(src, "triage")
    samples = []
    for _ in range(5):
        t0 = time.perf_counter_ns()
        analyze_function_source(src, "triage")
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    median = float(statistics.median(samples))
    p95 = float(samples[-1])
    perf_record(
        "analyze_function_source_1000_line",
        {
            "median": median,
            "p95": p95,
            "min": float(samples[0]),
            "max": float(samples[-1]),
            "n": float(len(samples)),
        },
        target=100_000_000.0,  # 100ms
    )
    assert median < 100_000_000, (
        f"analyze_function_source 1000-line median {median / 1e6:.1f}ms exceeds 100ms target."
    )


# ---------------------------------------------------------------------------
# 4. ``postrule analyze`` on the examples/ directory.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.30)
def test_analyze_examples_directory(perf_record):
    """Target: under 2s.

    Calls the in-process :func:`postrule.analyzer.analyze` API directly
    rather than shelling out to the CLI, so the measurement excludes
    interpreter cold-start. CLI-level latency is covered by
    :func:`test_import_postrule_cold` plus this number.
    """
    examples = Path(__file__).resolve().parent.parent.parent / "examples"
    assert examples.is_dir(), f"expected examples/ at {examples}"

    # Warmup
    for _ in range(2):
        analyze(str(examples))

    samples = []
    for _ in range(3):
        t0 = time.perf_counter_ns()
        analyze(str(examples))
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    median = float(statistics.median(samples))
    perf_record(
        "analyze_examples_directory",
        {
            "median": median,
            "p95": float(samples[-1]),
            "min": float(samples[0]),
            "max": float(samples[-1]),
            "n": float(len(samples)),
        },
        target=2_000_000_000.0,  # 2s
    )
    assert median < 2_000_000_000, (
        f"analyze examples/ median {median / 1e6:.0f}ms exceeds 2s target."
    )
