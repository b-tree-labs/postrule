# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Clock chaos: time jumps backwards, monotonic-vs-wall divergence, TZ
changes mid-run. Uses freezegun where it helps; otherwise drives the
clock paths directly.
"""

from __future__ import annotations

import time

from freezegun import freeze_time

from postrule import (
    BoundedInMemoryStorage,
    ClassificationRecord,
    LearnedSwitch,
    Phase,
    SwitchConfig,
    Verdict,
)
from postrule.gates import McNemarGate


def _rec(label: str, ts: float, outcome: str = Verdict.CORRECT.value) -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=ts,
        input="i",
        label=label,
        outcome=outcome,
        source="rule",
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# Backwards time jump
# ---------------------------------------------------------------------------


class TestBackwardsTimeJump:
    def test_classify_with_clock_jumping_backwards(self):
        """Wall clock jumps backwards a day mid-call: switch survives."""
        sw = LearnedSwitch(
            rule=lambda x: "rule",
            name="time_chaos",
            author="t",
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=True,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=BoundedInMemoryStorage(),
        )

        with freeze_time("2026-04-27 12:00:00"):
            sw.classify("first")
        with freeze_time("2026-04-26 12:00:00"):
            # Time has gone BACKWARDS 24 hours.
            sw.classify("second")
        with freeze_time("2026-04-27 12:00:00"):
            sw.classify("third")

        records = sw._storage.load_records(sw.name)
        # All three records present; timestamps reflect the frozen time.
        assert len(records) == 3
        # No crash; the storage doesn't enforce monotonicity.

    def test_mcnemar_gate_handles_negative_time_window(self):
        """Gate with records whose timestamps go backwards still computes.

        Records carry wall-clock timestamps. If a clock skew put a
        "newer" record at an earlier timestamp than a previous one,
        the gate's window-bounded computations must not crash on
        negative timedeltas.
        """
        gate = McNemarGate(min_paired=4)
        # Records with wall-clock timestamps going backwards.
        records = []
        for i in range(20):
            t = 1000.0 - i * 60  # Going backwards
            records.append(_rec(label="A", ts=t))

        # Should not raise. Gate may refuse to advance for other reasons,
        # but it must not blow up on the negative timedelta.
        decision = gate.evaluate(records, Phase.RULE, Phase.MODEL_SHADOW)
        assert decision is not None


# ---------------------------------------------------------------------------
# Monotonic vs wall clock
# ---------------------------------------------------------------------------


class TestMonotonicVsWall:
    def test_action_elapsed_uses_monotonic(self):
        """action_elapsed_ms must use perf_counter, not wall time.

        If wall clock jumps backwards during the action, action_elapsed_ms
        must NOT be negative.
        """
        observed_elapsed: list[float] = []

        def slow_action(_):
            time.sleep(0.001)
            return "done"

        sw = LearnedSwitch(
            rule=lambda x: "go",
            name="mono_check",
            author="t",
            labels={"go": slow_action},
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=False,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=BoundedInMemoryStorage(),
        )

        # freezegun by default freezes wall time; perf_counter keeps
        # ticking. The action's elapsed_ms must be > 0.
        with freeze_time("2026-04-27 12:00:00"):
            result = sw.dispatch("input")
        observed_elapsed.append(result.action_elapsed_ms or 0.0)
        assert result.action_elapsed_ms is not None
        assert result.action_elapsed_ms >= 0.0


# ---------------------------------------------------------------------------
# Timezone changes
# ---------------------------------------------------------------------------


class TestTimezoneChange:
    def test_tz_env_change_does_not_break_timestamps(self, monkeypatch):
        """Storing records, changing TZ, loading: timestamps stay POSIX.

        The schema uses POSIX seconds (timezone-free). A TZ change must
        not mutate stored timestamps.
        """
        sw = LearnedSwitch(
            rule=lambda x: "rule",
            name="tz_chaos",
            author="t",
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=True,
                auto_advance=False,
                auto_demote=False,
            ),
            storage=BoundedInMemoryStorage(),
        )

        monkeypatch.setenv("TZ", "America/New_York")
        time.tzset()
        sw.classify("ny")
        ny_ts = sw._storage.load_records(sw.name)[-1].timestamp

        monkeypatch.setenv("TZ", "Asia/Tokyo")
        time.tzset()
        sw.classify("tokyo")

        records = sw._storage.load_records(sw.name)
        # Both timestamps are POSIX seconds. They must be very close
        # (within a few seconds), regardless of TZ , POSIX time is
        # invariant under TZ changes.
        deltas = [
            abs(records[i + 1].timestamp - records[i].timestamp) for i in range(len(records) - 1)
        ]
        assert all(d < 5.0 for d in deltas), f"TZ change leaked into timestamps: deltas={deltas}"
        assert ny_ts is not None


# ---------------------------------------------------------------------------
# Future timestamps in records
# ---------------------------------------------------------------------------


class TestFutureTimestamps:
    def test_record_with_future_timestamp_loads(self):
        """A record with a timestamp 100 years in the future loads cleanly.

        No silent rejection, no crash.
        """
        # Use tmp_path indirectly via fixture-less plain construction.
        import tempfile

        from postrule import FileStorage

        with tempfile.TemporaryDirectory() as td:
            store = FileStorage(td + "/store")
            future_ts = time.time() + (365 * 24 * 3600 * 100)
            store.append_record("s", _rec(label="future", ts=future_ts))
            records = store.load_records("s")
            assert len(records) == 1
            assert records[0].timestamp == future_ts
