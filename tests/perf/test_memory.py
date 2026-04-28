# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Memory perf tests — leak detection over sustained dispatch loops.

Uses ``tracemalloc`` for allocation tracking (more deterministic than
RSS) and ``/dev/fd`` for file-descriptor leak detection (POSIX; the
suite is macOS / Linux only by construction — Dendra's Windows
support has its own perf envelope).
"""

from __future__ import annotations

import os
import time
import tracemalloc
from pathlib import Path

import pytest

from dendra import (
    BoundedInMemoryStorage,
    ClassificationRecord,
    FileStorage,
    LearnedSwitch,
    Phase,
    SwitchConfig,
)
from tests.perf.conftest import perf_test  # noqa: TID252

pytestmark = pytest.mark.perf


def _open_fd_count() -> int | None:
    """Return open file-descriptor count on POSIX, or ``None`` elsewhere.

    Uses ``/dev/fd`` (macOS) / ``/proc/self/fd`` (Linux). Returns
    ``None`` on Windows so the test can be skipped cleanly there.
    """
    for path in ("/dev/fd", "/proc/self/fd"):
        if os.path.isdir(path):
            try:
                return len(os.listdir(path))
            except OSError:
                return None
    return None


# ---------------------------------------------------------------------------
# 1. 10k dispatches with BoundedInMemoryStorage — peak <= 5MB after 1k warmup.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.30)
def test_memory_10k_dispatches_bounded(perf_record):
    """Assert: peak alloc growth <= 5MB after 1k warmup, over 10k dispatches.

    BoundedInMemoryStorage caps at 10_000 records so the deque is
    full after warmup; subsequent dispatches push the deque, evicting
    one each time. Steady-state growth should be near-zero.
    """
    storage = BoundedInMemoryStorage(max_records=1000)
    sw = LearnedSwitch(
        rule=lambda x: "a" if "a" in x else "b",
        name="perf_mem_10k",
        author="perf",
        config=SwitchConfig(starting_phase=Phase.RULE, auto_advance=False),
        storage=storage,
    )

    tracemalloc.start()
    try:
        for _ in range(1000):
            sw.dispatch("hello")
        tracemalloc.reset_peak()
        baseline_current, _ = tracemalloc.get_traced_memory()
        for _ in range(10_000):
            sw.dispatch("hello")
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    growth = peak - baseline_current
    perf_record(
        "memory_10k_dispatches_bounded_growth_bytes",
        {
            "median": float(growth),
            "p95": float(growth),
            "peak_bytes": float(peak),
            "current_bytes": float(current),
            "baseline_bytes": float(baseline_current),
            "n": 10_000.0,
        },
        target=5 * 1024 * 1024.0,  # 5MB
    )
    assert growth <= 5 * 1024 * 1024, (
        f"10k dispatches grew tracemalloc peak by {growth / 1024 / 1024:.2f}MB; "
        "ceiling 5MB."
    )


# ---------------------------------------------------------------------------
# 2. 100k dispatches — same probe, 50MB ceiling. Marked slow.
# ---------------------------------------------------------------------------


@pytest.mark.slow
@perf_test(tolerance=0.30)
def test_memory_100k_dispatches_bounded(perf_record):
    """Assert: peak alloc growth <= 50MB over 100k dispatches.

    Slow probe: ~3-8s on Apple Silicon. The 100k volume catches
    leaks that the 10k probe is too short to surface.
    """
    storage = BoundedInMemoryStorage(max_records=10_000)
    sw = LearnedSwitch(
        rule=lambda x: "a" if "a" in x else "b",
        name="perf_mem_100k",
        author="perf",
        config=SwitchConfig(starting_phase=Phase.RULE, auto_advance=False),
        storage=storage,
    )

    tracemalloc.start()
    try:
        for _ in range(10_000):
            sw.dispatch("hello")
        tracemalloc.reset_peak()
        baseline_current, _ = tracemalloc.get_traced_memory()
        for _ in range(100_000):
            sw.dispatch("hello")
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    growth = peak - baseline_current
    perf_record(
        "memory_100k_dispatches_bounded_growth_bytes",
        {
            "median": float(growth),
            "p95": float(growth),
            "peak_bytes": float(peak),
            "current_bytes": float(current),
            "baseline_bytes": float(baseline_current),
            "n": 100_000.0,
        },
        target=50 * 1024 * 1024.0,
    )
    assert growth <= 50 * 1024 * 1024, (
        f"100k dispatches grew tracemalloc peak by {growth / 1024 / 1024:.2f}MB; "
        "ceiling 50MB."
    )


# ---------------------------------------------------------------------------
# 3. File-handle leak detection — 1000 FileStorage open/close cycles.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.30)
def test_filestorage_no_fd_leak(perf_record, tmp_path: Path):
    """Assert: open fd count returns to baseline within 10 after 1000 cycles.

    Skipped on platforms where ``/dev/fd`` and ``/proc/self/fd`` are
    both unavailable (i.e. Windows).
    """
    baseline_fds = _open_fd_count()
    if baseline_fds is None:
        pytest.skip("fd-count probe unavailable on this platform")

    rec = ClassificationRecord(
        timestamp=time.time(),
        input="hello",
        label="a",
        outcome="unknown",
        source="rule",
        confidence=1.0,
    )

    for i in range(1000):
        fs = FileStorage(tmp_path / f"fs_{i % 4}", batching=False, fsync=False)
        fs.append_record("s1", rec)
        fs.close()

    final_fds = _open_fd_count()
    assert final_fds is not None
    delta = final_fds - baseline_fds
    perf_record(
        "filestorage_fd_delta_after_1000_cycles",
        {
            "median": float(delta),
            "p95": float(delta),
            "baseline_fds": float(baseline_fds),
            "final_fds": float(final_fds),
            "n": 1000.0,
        },
        target=10.0,
    )
    assert delta <= 10, (
        f"FileStorage 1000 open/close cycles leaked {delta} fds "
        f"(baseline={baseline_fds}, final={final_fds}); ceiling 10."
    )
