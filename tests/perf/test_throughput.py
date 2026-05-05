# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Throughput perf tests — sustained ops/sec under steady state."""

from __future__ import annotations

import asyncio
import threading
import time
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
from tests.perf.conftest import measure_throughput, perf_test  # noqa: TID252

pytestmark = pytest.mark.perf


def _make_record() -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=time.time(),
        input="hello",
        label="a",
        outcome="unknown",
        source="rule",
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# 1. BoundedInMemoryStorage — target > 50k/s single-threaded.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.20)
def test_throughput_bounded_inmemory(perf_record):
    storage = BoundedInMemoryStorage()
    rec = _make_record()
    stats = measure_throughput(
        lambda: storage.append_record("s1", rec),
        seconds=0.5,
        warmup_ops=500,
    )
    perf_record(
        "throughput_bounded_inmemory",
        stats,
        higher_is_better=True,
        target=50_000.0,
        unit="ops/s",
    )
    assert stats["median"] > 50_000, (
        f"BoundedInMemoryStorage at {stats['median']:.0f} ops/s; target >50k."
    )


# ---------------------------------------------------------------------------
# 2. FileStorage(batching=True) — target > 10k/s single-threaded.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.50)
def test_throughput_filestorage_batching(perf_record, tmp_path: Path):
    fs = FileStorage(tmp_path / "fs", batching=True, batch_size=512, flush_interval_ms=20)
    try:
        rec = _make_record()
        stats = measure_throughput(
            lambda: fs.append_record("s1", rec),
            seconds=0.5,
            warmup_ops=500,
        )
    finally:
        fs.close()
    perf_record(
        "throughput_filestorage_batching",
        stats,
        higher_is_better=True,
        target=10_000.0,
        unit="ops/s",
    )
    assert stats["median"] > 10_000, (
        f"FileStorage(batching=True) at {stats['median']:.0f} ops/s; target >10k."
    )


# ---------------------------------------------------------------------------
# 3. Concurrent FileStorage writes — N=4 threads, target > 20k/s aggregate.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.60)
def test_throughput_filestorage_concurrent_4threads(perf_record, tmp_path: Path):
    fs = FileStorage(tmp_path / "fs", batching=True, batch_size=512, flush_interval_ms=20)
    rec = _make_record()
    n_threads = 4
    duration_s = 0.5
    counts = [0] * n_threads
    stop_event = threading.Event()

    def worker(idx: int) -> None:
        local_count = 0
        while not stop_event.is_set():
            fs.append_record(f"s{idx}", rec)
            local_count += 1
        counts[idx] = local_count

    # Warmup
    for _ in range(500):
        fs.append_record("warmup", rec)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n_threads)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    time.sleep(duration_s)
    stop_event.set()
    for t in threads:
        t.join(timeout=2.0)
    elapsed = time.perf_counter() - t0
    fs.close()

    total = sum(counts)
    rate = total / elapsed
    stats = {
        "median": float(rate),
        "p95": float(rate),
        "n": float(total),
        "elapsed_s": float(elapsed),
        "n_threads": float(n_threads),
    }
    # v1.1 (issue #136): the historical bottleneck on this benchmark
    # was per-call ``Path.resolve()`` storms (~95 lstat syscalls per
    # append) inside ``_switch_dir``, plus per-call ``.lock`` open/
    # close cycles. Both are now cached for the life of the storage,
    # with one ``os.lstat`` per ``append_record`` to defeat TOCTOU
    # symlink-swap attacks (redteam test_toctou_symlink_swap). With
    # ``batching=True``, this measures the enqueue path; it now
    # sustains 150k+ ops/s on Apple Silicon. The 100k target is the
    # design ceiling for the security-validated hot path.
    perf_record(
        "throughput_filestorage_concurrent_4threads",
        stats,
        higher_is_better=True,
        target=100_000.0,
        unit="ops/s",
    )
    assert rate > 50_000, (
        f"FileStorage 4-thread concurrent at {rate:.0f} ops/s; "
        "v1.1 hard floor 50k (was ~7k pre-cache; see issue #136)."
    )


# ---------------------------------------------------------------------------
# 3b. Concurrent FileStorage UNBATCHED writes — N=4 threads, regression test
# for the issue-#136 path-resolve + lock-fd open/close hot-path costs.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.40)
def test_throughput_filestorage_unbatched_4threads(perf_record, tmp_path: Path):
    """Regression test for issue #136 — unbatched 4-thread durable writes.

    Pre-fix this workload sustained ~1-2k ops/s on Apple Silicon
    because every ``append_record`` re-resolved the per-switch path
    (``Path.resolve`` -> realpath -> repeated lstat) and re-opened
    the ``.lock`` file. Post-fix the path resolution is cached and
    the lock fd is held open across calls. This test asserts the
    floor stays well above the legacy ceiling so a future regression
    that re-introduces either cost flips the test red.
    """
    fs = FileStorage(tmp_path / "fs", batching=False, fsync=False)
    rec = _make_record()
    n_threads = 4
    duration_s = 0.5
    counts = [0] * n_threads
    stop_event = threading.Event()

    def worker(idx: int) -> None:
        local_count = 0
        while not stop_event.is_set():
            fs.append_record(f"s{idx}", rec)
            local_count += 1
        counts[idx] = local_count

    # Warmup so the first-call path-validation + dir-mkdir cost is amortized.
    for i in range(n_threads):
        for _ in range(50):
            fs.append_record(f"s{i}", rec)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n_threads)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    time.sleep(duration_s)
    stop_event.set()
    for t in threads:
        t.join(timeout=2.0)
    elapsed = time.perf_counter() - t0
    fs.close()

    total = sum(counts)
    rate = total / elapsed
    stats = {
        "median": float(rate),
        "p95": float(rate),
        "n": float(total),
        "elapsed_s": float(elapsed),
        "n_threads": float(n_threads),
    }
    perf_record(
        "throughput_filestorage_unbatched_4threads",
        stats,
        higher_is_better=True,
        target=20_000.0,
        unit="ops/s",
    )
    # Hard floor: ~5x the pre-fix ceiling. macOS APFS + Apple Silicon
    # comfortably hits ~25-35k ops/s with the path-cache + lock-fd-cache
    # patches; a floor of 10k absorbs scheduler jitter and CI variance.
    assert rate > 10_000, (
        f"FileStorage unbatched 4-thread at {rate:.0f} ops/s; floor 10k "
        "(was ~1-2k pre-fix; see issue #136)."
    )


# ---------------------------------------------------------------------------
# 4. Async dispatch throughput — N=100 coroutines, target > 5k/s.
# ---------------------------------------------------------------------------


@perf_test(tolerance=0.20)
def test_throughput_adispatch_100_coroutines(perf_record):
    sw = LearnedSwitch(
        rule=lambda x: "a" if "a" in x else "b",
        name="perf_throughput_async",
        author="perf",
        config=SwitchConfig(
            starting_phase=Phase.RULE,
            auto_record=False,
            auto_advance=False,
        ),
    )

    async def driver(per_coro: int) -> int:
        async def one() -> int:
            for _ in range(per_coro):
                await sw.adispatch("hello")
            return per_coro

        results = await asyncio.gather(*(one() for _ in range(100)))
        return sum(results)

    # Warmup
    asyncio.run(driver(2))

    per_coro = 10
    t0 = time.perf_counter()
    total = asyncio.run(driver(per_coro))
    elapsed = time.perf_counter() - t0
    rate = total / elapsed
    stats = {
        "median": float(rate),
        "p95": float(rate),
        "n": float(total),
        "elapsed_s": float(elapsed),
        "n_coroutines": 100.0,
    }
    perf_record(
        "throughput_adispatch_100_coroutines",
        stats,
        higher_is_better=True,
        target=5_000.0,
        unit="ops/s",
    )
    assert rate > 5_000, f"adispatch 100-coroutine throughput {rate:.0f} ops/s; target >5k."
