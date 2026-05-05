# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Memory-pressure chaos: large inputs, many labels, RSS drift.

These are heuristic; the limits are conservative so CI stays green on
modest hardware.
"""

from __future__ import annotations

import gc
import time
import tracemalloc

import pytest

from dendra import (
    BoundedInMemoryStorage,
    LearnedSwitch,
    Phase,
    SwitchConfig,
)

# ---------------------------------------------------------------------------
# Big inputs
# ---------------------------------------------------------------------------


class TestLargeInputs:
    def test_one_megabyte_input_completes(self):
        """1 MB input must classify without OOM."""
        sw = LearnedSwitch(
            rule=lambda x: "rule",
            name="big_input",
            author="t",
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=False,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=BoundedInMemoryStorage(),
        )
        big = "x" * (1024 * 1024)  # 1 MiB
        result = sw.classify(big)
        assert result.label == "rule"

    @pytest.mark.parametrize("size_kb", [1, 10, 100, 500])
    def test_inputs_at_various_sizes_classify(self, size_kb):
        """Sweep input sizes from 1 KB to 500 KB; latency stays bounded."""
        sw = LearnedSwitch(
            rule=lambda x: "rule",
            name=f"big_input_{size_kb}",
            author="t",
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=False,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=BoundedInMemoryStorage(),
        )
        payload = "x" * (size_kb * 1024)
        t0 = time.monotonic()
        sw.classify(payload)
        elapsed = time.monotonic() - t0
        # Even 500 KB must classify under 100 ms , the rule is identity.
        assert elapsed < 0.1, f"{size_kb} KB classify took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Many labels
# ---------------------------------------------------------------------------


class TestManyLabels:
    def test_one_thousand_distinct_labels(self):
        """A switch with 1000 labels still dispatches in reasonable time.

        Label lookup must be O(1) (dict-based), not O(N) (linear scan).
        v1-readiness §2 finding #23.
        """
        labels = [f"label_{i}" for i in range(1000)]

        def rule(x):
            return labels[x % 1000]

        sw = LearnedSwitch(
            rule=rule,
            name="big_labels",
            author="t",
            labels=labels,
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=False,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=BoundedInMemoryStorage(),
        )

        t0 = time.monotonic()
        for i in range(500):
            sw.dispatch(i)
        elapsed = time.monotonic() - t0
        # 500 dispatches over 1000-label switch; with O(1) label lookup
        # this should be well under 1s.
        assert elapsed < 1.0, f"label lookup is not O(1); {elapsed:.3f}s for 500 calls"


# ---------------------------------------------------------------------------
# RSS / memory drift over many dispatches
# ---------------------------------------------------------------------------


class TestRSSDrift:
    @pytest.mark.slow
    def test_no_unbounded_memory_growth_in_dispatch_loop(self):
        """100k dispatches in a tight loop must not accumulate memory.

        BoundedInMemoryStorage caps at 10k records; auto_record=False so
        we don't even hit storage. The only growable structures should
        be transient (each ClassificationResult should die).

        Tolerance: 30 MB growth over 100k iterations. Real growth would
        be hundreds of MB (one allocation per call sticking).
        """
        sw = LearnedSwitch(
            rule=lambda x: "rule",
            name="drift_check",
            author="t",
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=False,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=BoundedInMemoryStorage(max_records=100),
        )

        # Warmup so the trace doesn't catch first-call allocations
        # (lazy-imports inside dendra hit on the first call).
        for i in range(1000):
            sw.classify(i)
        gc.collect()

        tracemalloc.start()
        snap_before = tracemalloc.take_snapshot()

        for i in range(100_000):
            sw.classify(i)

        gc.collect()
        snap_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        diff = snap_after.compare_to(snap_before, "filename")
        total_diff_bytes = sum(s.size_diff for s in diff)
        # 30 MB cap. If we leak per-call, this test will explode (well
        # over 100 MB on 100k iterations).
        assert total_diff_bytes < 30 * 1024 * 1024, (
            f"memory grew by {total_diff_bytes / (1024 * 1024):.1f} MB; "
            f"likely unbounded growth in classify()"
        )


# ---------------------------------------------------------------------------
# Bounded storage cap actually bounds
# ---------------------------------------------------------------------------


class TestBoundedStorageCap:
    def test_bounded_storage_does_not_grow_past_cap(self):
        """BoundedInMemoryStorage with cap=N never holds more than N records.

        The default backend is bounded for exactly the OOM scenario in
        SwitchConfig's docstring. Confirm the cap survives a 50x overflow.
        """
        sw = LearnedSwitch(
            rule=lambda x: "rule",
            name="cap_check",
            author="t",
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=True,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=BoundedInMemoryStorage(max_records=100),
        )

        for i in range(5000):
            sw.classify(i)

        records = sw._storage.load_records(sw.name)
        assert len(records) <= 100, f"cap broken: {len(records)} > 100"
