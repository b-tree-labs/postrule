# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#32 PR 3 — opportunistic background flusher.

PR 1 introduced the durable Outbox; PR 2 wired it into the emitter so
every accepted verdict persists to SQLite and pending rows replay on
the next process start. This PR adds the missing piece for long-running
processes that experience transient network outages: a periodic
background drain that re-pushes failed rows back into the sender queue
when the network comes back up.

Without this, a verdict that fails (mark_attempt'd in the outbox) sits
there until the process restarts. With this, a Cloud Run revision that
hits a 30-second connectivity blip recovers automatically.

Contract pinned by these tests:

  1. ``flush_pending()`` — public method that pulls pending outbox rows
     and pushes them into the in-memory queue, skipping rows already
     in flight. Safe to call manually (tests, operator tooling) or
     from the daemon thread.

  2. No-outbox parity: ``flush_pending()`` is a no-op when the emitter
     has no outbox configured.

  3. The daemon thread calls ``flush_pending()`` every
     ``flush_interval_seconds`` (default 30s). Tests use a short
     interval to keep latency low.

  4. ``stop_event`` shuts the flusher down cleanly.
"""

from __future__ import annotations

import time
from pathlib import Path

from postrule.cloud.outbox import Outbox
from postrule.cloud.verdict_telemetry import CloudVerdictEmitter


class _ToggleableSender:
    """Sender that flips between fail and succeed on demand — simulates
    a transient network outage."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail = True

    def post(self, payload: dict) -> bool:
        self.calls.append(dict(payload))
        return not self.fail


def _make_emitter(
    sender: _ToggleableSender,
    outbox: Outbox | None,
    *,
    start_thread: bool = True,
    start_flusher: bool = False,
    flush_interval_seconds: float = 30.0,
) -> CloudVerdictEmitter:
    return CloudVerdictEmitter(
        api_url="http://localhost:8787",
        bearer_token="prul_test",  # pragma: allowlist secret
        sender=sender,
        rate_limit_per_second=10_000,
        rate_limit_burst=10_000,
        queue_capacity=64,
        outbox=outbox,
        start_thread=start_thread,
        start_flusher=start_flusher,
        flush_interval_seconds=flush_interval_seconds,
    )


def _outcome(switch: str = "t") -> dict:
    return {"switch": switch, "outcome": "correct", "phase": "P0"}


# ---------------------------------------------------------------------------
# flush_pending() — the manual drain hook
# ---------------------------------------------------------------------------


class TestFlushPendingManual:
    def test_re_pushes_failed_rows_when_queue_is_empty(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        sender = _ToggleableSender()
        sender.fail = True

        # Phase 1 — network is down. Emit 3 verdicts; sender fails them
        # all; rows stay in outbox.
        em = _make_emitter(sender, ob)
        try:
            for i in range(3):
                em.emit("outcome", _outcome(f"s{i}"))
            em.flush(5.0)
            assert ob.pending_count() == 3
            assert sender.calls and all(p["switch_name"].startswith("s") for p in sender.calls)
            calls_phase_1 = len(sender.calls)

            # Phase 2 — network is back. Operator calls flush_pending()
            # explicitly; rows should re-push and drain.
            sender.fail = False
            em.flush_pending()
            em.flush(5.0)
            assert ob.pending_count() == 0
            # Sender saw each row at least one more time after the
            # network came back.
            assert len(sender.calls) > calls_phase_1
        finally:
            em.close(timeout=0.1)
            ob.close()

    def test_skips_rows_already_in_flight(self, tmp_path: Path) -> None:
        # Sender thread off — we want full control over what's "in flight."
        ob = Outbox(path=tmp_path / "ob.sqlite")
        sender = _ToggleableSender()
        em = _make_emitter(sender, ob, start_thread=False)
        try:
            em.emit("outcome", _outcome("s0"))
            # Row is in the in-memory queue AND in the outbox. Without
            # the skip-in-flight guard, flush_pending would push it
            # AGAIN, causing the sender to see it twice.
            queue_before = em._queue.qsize()
            em.flush_pending()
            queue_after = em._queue.qsize()
            assert queue_after == queue_before  # no double-enqueue
        finally:
            em.close(timeout=0.1)
            ob.close()

    def test_noop_when_outbox_is_none(self) -> None:
        # Defensive: flush_pending() on an outbox-less emitter must not
        # raise. Useful in test helpers that flip outbox on/off without
        # branching the call site.
        sender = _ToggleableSender()
        em = _make_emitter(sender, outbox=None)
        try:
            em.flush_pending()  # would raise if not guarded
        finally:
            em.close(timeout=0.1)


# ---------------------------------------------------------------------------
# Background daemon thread
# ---------------------------------------------------------------------------


class TestBackgroundFlusherThread:
    def test_periodic_tick_drains_outbox_when_network_recovers(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        sender = _ToggleableSender()
        sender.fail = True

        em = _make_emitter(
            sender,
            ob,
            start_flusher=True,
            flush_interval_seconds=0.05,
        )
        try:
            # Network down — emit; sender fails; rows stay.
            for i in range(2):
                em.emit("outcome", _outcome(f"s{i}"))
            em.flush(2.0)
            assert ob.pending_count() == 2

            # Bring the network back; wait a few flusher ticks.
            sender.fail = False
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and ob.pending_count() > 0:
                time.sleep(0.05)

            assert ob.pending_count() == 0
        finally:
            em.close(timeout=0.5)
            ob.close()

    def test_stop_event_stops_flusher_cleanly(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        sender = _ToggleableSender()
        em = _make_emitter(
            sender,
            ob,
            start_flusher=True,
            flush_interval_seconds=0.05,
        )
        # close() sets the stop event + joins both daemon threads.
        em.close(timeout=2.0)
        # If the flusher didn't stop, we'd still see calls after close().
        before = len(sender.calls)
        time.sleep(0.2)
        after = len(sender.calls)
        assert after == before
        ob.close()
