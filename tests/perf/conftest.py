# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Perf-regression harness for the ``-m perf`` suite.

Goals
-----

* Catch latency / throughput / memory regressions before users do.
* Stay zero-dep: stdlib + ``tracemalloc`` + ``time.perf_counter`` only.
* Keep baselines committed to the repo so cross-machine drift is bounded
  by ``tolerance``, not by who happened to run the tests last.

Usage
-----

::

    @pytest.mark.perf
    @perf_test(tolerance=0.30)  # optional, default 0.20 (20% slowdown)
    def test_dispatch_overhead(perf_record):
        sw = LearnedSwitch(...)
        sw.dispatch("warmup")  # any pre-state setup
        stats = measure(lambda: sw.dispatch("hello"), n=5000, warmup=500)
        perf_record("dispatch_rule", stats)

    @pytest.mark.perf
    def test_throughput(perf_record):
        ops_per_sec = measure_throughput(lambda: append(rec), seconds=0.5)
        perf_record("storage_throughput", {"median": ops_per_sec, "p95": ops_per_sec},
                    higher_is_better=True)

The harness compares the ``median`` value (or whatever the test marks
as the primary metric) to the JSON baseline at
``tests/perf/baselines/<test_name>.json`` and FAILS the test if the
current run drifts outside ``tolerance``.

First-time runs auto-record the baseline. Re-record on intentional
changes via ``pytest tests/perf/ -m perf --update-baselines``.

Sandbox interaction
-------------------

The repo-wide ``tests/conftest.py`` blocks writes outside ``tmp_path``.
Baselines live under ``tests/perf/baselines/`` (a real path on disk),
so this conftest opts every perf test out of the external-write guard.
We need to write the baseline file when ``--update-baselines`` is
passed, and we never need general filesystem access from a perf test.
The opt-out is scoped to perf tests only.
"""

from __future__ import annotations

import json
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

# Directory where committed baselines live. Resolved relative to this
# conftest, NOT the cwd, so it works regardless of where pytest was
# invoked from.
_BASELINE_DIR = Path(__file__).parent / "baselines"

# Default tolerance: a current measurement may be up to 20% worse than
# baseline before the test fails. Lifted via @perf_test(tolerance=...).
_DEFAULT_TOLERANCE = 0.20


# ---------------------------------------------------------------------------
# CLI option: --update-baselines
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Wire ``--update-baselines`` into pytest.

    With the flag set, the harness rewrites the baseline JSON for every
    test that calls ``perf_record``. Without it, mismatches outside
    tolerance fail the test.
    """
    group = parser.getgroup("perf")
    group.addoption(
        "--update-baselines",
        action="store_true",
        default=False,
        help=(
            "Rewrite tests/perf/baselines/*.json from the current run. "
            "Use after an intentional perf change."
        ),
    )


@pytest.fixture(scope="session")
def _update_baselines(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-baselines"))


# ---------------------------------------------------------------------------
# Sandbox opt-out so the harness can write baselines.
# ---------------------------------------------------------------------------
#
# The repo-wide conftest's ``_block_external_writes`` guard checks
# ``request.fixturenames`` for ``external_writes_allowed``. Perf tests
# need to write baselines under tests/perf/baselines/, which is outside
# tmp_path. We unconditionally include the opt-out here so authors do
# not have to remember it on every perf test. The trade-off is that
# perf tests skip the external-write guard — they are not a sandbox
# integrity test, they are a benchmark.


@pytest.fixture(autouse=True)
def _perf_external_writes_allowed(external_writes_allowed: object) -> None:
    """Force-enable ``external_writes_allowed`` for every perf test.

    The fixture is a no-op on its own; depending on it makes the
    parent ``_block_external_writes`` see the fixture name in
    ``request.fixturenames`` and skip its monkeypatch.
    """
    return None


# ---------------------------------------------------------------------------
# perf_test decorator — per-test tolerance override.
# ---------------------------------------------------------------------------


def perf_test(*, tolerance: float = _DEFAULT_TOLERANCE) -> Callable[[Callable], Callable]:
    """Stamp a perf test with a per-test tolerance override.

    The default 20% works for stable hot paths. Micro-benchmarks
    measuring sub-microsecond ops should use a wider tolerance (30%
    is a sensible floor) because OS scheduler jitter dominates.
    """

    if not 0.0 <= tolerance <= 5.0:
        raise ValueError(f"tolerance must be in [0, 5]; got {tolerance!r}")

    def decorate(fn: Callable) -> Callable:
        fn.__perf_tolerance__ = tolerance  # type: ignore[attr-defined]
        return fn

    return decorate


def _tolerance_for(item: pytest.Item) -> float:
    """Return the tolerance configured on the test function, default 0.20."""
    fn = getattr(item, "function", None)
    return float(getattr(fn, "__perf_tolerance__", _DEFAULT_TOLERANCE))


# ---------------------------------------------------------------------------
# Measurement primitives — small, single-purpose, stdlib-only.
# ---------------------------------------------------------------------------


@dataclass
class _RecordedMetric:
    name: str
    stats: dict[str, float]
    higher_is_better: bool = False
    target: float | None = None  # spec-level target ceiling (or floor for HIB)
    unit: str = "ns"


@dataclass
class _PerfRunState:
    """Per-test scratch space accumulated by ``perf_record``.

    Held on the ``request.node`` so pytest_runtest_makereport can read
    the metrics out for the histogram + diagnostic message.
    """

    metrics: list[_RecordedMetric] = field(default_factory=list)


def measure(
    fn: Callable[[], Any],
    *,
    n: int,
    warmup: int = 100,
) -> dict[str, float]:
    """Time ``fn`` ``n`` times after ``warmup`` warmup iterations.

    Returns ``{"median": <ns>, "p95": <ns>, "min": <ns>, "max": <ns>,
    "n": n}`` — the only stats the regression check looks at.

    Uses ``time.perf_counter_ns`` for tightest possible resolution.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    for _ in range(max(0, warmup)):
        fn()
    samples = [0] * n
    perf = time.perf_counter_ns
    for i in range(n):
        t0 = perf()
        fn()
        samples[i] = perf() - t0
    samples.sort()
    return {
        "median": float(samples[n // 2]),
        "p95": float(samples[int(n * 0.95)]),
        "min": float(samples[0]),
        "max": float(samples[-1]),
        "n": float(n),
    }


def measure_throughput(
    op: Callable[[], Any],
    *,
    seconds: float = 0.5,
    warmup_ops: int = 100,
) -> dict[str, float]:
    """Run ``op`` repeatedly for ``seconds`` and return ops/second.

    Returns ``{"median": ops_per_sec, "p95": ops_per_sec, ...}`` —
    median and p95 are identical for a throughput probe; the JSON
    schema is shared across latency/throughput tests so the baseline
    code can stay one branch.
    """
    for _ in range(max(0, warmup_ops)):
        op()
    perf = time.perf_counter
    deadline = perf() + seconds
    count = 0
    while perf() < deadline:
        op()
        count += 1
    elapsed = perf() - (deadline - seconds)
    rate = count / elapsed if elapsed > 0 else 0.0
    return {
        "median": float(rate),
        "p95": float(rate),
        "n": float(count),
        "elapsed_s": float(elapsed),
    }


def measure_memory(
    fn: Callable[[], Any],
    *,
    iterations: int,
    warmup: int = 100,
) -> dict[str, float]:
    """Run ``fn`` ``iterations`` times under ``tracemalloc`` after warmup.

    Returns ``{"peak_bytes": ..., "current_bytes": ..., "growth_bytes":
    peak - baseline-after-warmup}``.

    The relevant number for leak detection is ``growth_bytes``: how
    much additional peak the timed window sustained beyond the
    post-warmup steady state.
    """
    tracemalloc.start()
    try:
        for _ in range(max(0, warmup)):
            fn()
        # Snapshot post-warmup so growth is measured against steady state,
        # not against process start.
        baseline_current, _baseline_peak = tracemalloc.get_traced_memory()
        # Reset peak so the timed window has a clean ceiling.
        tracemalloc.reset_peak()
        for _ in range(iterations):
            fn()
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        "median": float(peak - baseline_current),
        "p95": float(peak - baseline_current),
        "peak_bytes": float(peak),
        "current_bytes": float(current),
        "growth_bytes": float(peak - baseline_current),
        "n": float(iterations),
    }


# ---------------------------------------------------------------------------
# Baseline I/O + comparison
# ---------------------------------------------------------------------------


def _baseline_path(metric_name: str) -> Path:
    return _BASELINE_DIR / f"{metric_name}.json"


def _load_baseline(metric_name: str) -> dict[str, float] | None:
    p = _baseline_path(metric_name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_baseline(metric_name: str, stats: dict[str, float]) -> None:
    _BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    p = _baseline_path(metric_name)
    payload = {k: stats[k] for k in stats if isinstance(stats[k], (int, float))}
    p.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _compare(
    *,
    metric_name: str,
    current: dict[str, float],
    baseline: dict[str, float],
    tolerance: float,
    higher_is_better: bool,
) -> tuple[bool, str]:
    """Return ``(passed, message)``."""
    cur_med = current["median"]
    base_med = baseline.get("median")
    if base_med is None or base_med <= 0:
        # Treat as missing baseline; auto-record on the next pass.
        return True, f"{metric_name}: baseline lacks 'median', will auto-record."

    if higher_is_better:
        # current must be at least baseline * (1 - tolerance)
        floor = base_med * (1.0 - tolerance)
        if cur_med < floor:
            slowdown_pct = (1.0 - cur_med / base_med) * 100.0
            return False, (
                f"{metric_name} regressed by {slowdown_pct:.1f}% "
                f"(current={cur_med:.2f} < baseline={base_med:.2f} × "
                f"(1-{tolerance:.2f})={floor:.2f}). "
                "Run with --update-baselines if intentional."
            )
        return True, ""

    ceiling = base_med * (1.0 + tolerance)
    if cur_med > ceiling:
        slowdown_pct = (cur_med / base_med - 1.0) * 100.0
        return False, (
            f"{metric_name} regressed by {slowdown_pct:.1f}% "
            f"(current={cur_med:.2f} > baseline={base_med:.2f} × "
            f"(1+{tolerance:.2f})={ceiling:.2f}). "
            "Run with --update-baselines if intentional."
        )
    return True, ""


# ---------------------------------------------------------------------------
# perf_record fixture — what test bodies actually call.
# ---------------------------------------------------------------------------


@pytest.fixture
def perf_record(
    request: pytest.FixtureRequest,
    _update_baselines: bool,
) -> Callable[..., None]:
    """Return a recorder bound to the current test's tolerance + baseline.

    The recorder takes a metric name and a stats dict (typically the
    return value of :func:`measure` / :func:`measure_throughput` /
    :func:`measure_memory`). It writes the baseline on first run or
    when ``--update-baselines`` is set; otherwise it asserts the
    median sits inside the tolerance window.
    """
    state = _PerfRunState()
    request.node.__perf_state__ = state  # picked up by reporter hook

    tolerance = _tolerance_for(request.node)

    def record(
        metric_name: str,
        stats: dict[str, float],
        *,
        higher_is_better: bool = False,
        target: float | None = None,
        unit: str = "ns",
    ) -> None:
        state.metrics.append(
            _RecordedMetric(
                name=metric_name,
                stats=dict(stats),
                higher_is_better=higher_is_better,
                target=target,
                unit=unit,
            )
        )

        baseline = _load_baseline(metric_name)
        # On --update-baselines or no baseline, record and pass.
        if _update_baselines or baseline is None:
            _write_baseline(metric_name, stats)
            return

        ok, msg = _compare(
            metric_name=metric_name,
            current=stats,
            baseline=baseline,
            tolerance=tolerance,
            higher_is_better=higher_is_better,
        )
        if not ok:
            pytest.fail(msg, pytrace=False)

    return record


# ---------------------------------------------------------------------------
# Histogram reporter — print measured-vs-target overhead at session end.
# ---------------------------------------------------------------------------


_REPORTER_BUCKETS: list[_RecordedMetric] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    outcome = yield
    if call.when != "call":
        return outcome
    state = getattr(item, "__perf_state__", None)
    if state is not None:
        _REPORTER_BUCKETS.extend(state.metrics)
    return outcome


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ARG001
    if not _REPORTER_BUCKETS:
        return
    tw = terminalreporter
    tw.section("perf metrics — measured vs target")
    tw.line(f"{'metric':<55} {'measured':>14} {'target':>14} {'ratio':>10}")
    tw.line("-" * 95)
    for m in _REPORTER_BUCKETS:
        med = m.stats.get("median", float("nan"))
        target = m.target
        if target is None or target <= 0:
            ratio = ""
            target_s = "n/a"
        else:
            ratio = f"{med / target:.2f}x"
            target_s = f"{target:.2f}"
        tw.line(f"{m.name:<55} {med:>14.2f} {target_s:>14} {ratio:>10}")
