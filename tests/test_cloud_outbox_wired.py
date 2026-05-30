# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#32 PR 2 — wire the durable Outbox in as the queue backing for
CloudVerdictEmitter.

PR 1 introduced the Outbox primitive (`postrule.cloud.outbox.Outbox`).
This PR plumbs it through the emitter so every accepted verdict is
persisted to SQLite before the in-memory queue sees it, and replayed
on the next process start.

Behavior pinned by these tests:

  1. Default (outbox=None): existing in-memory-only behavior unchanged.
  2. With outbox:
       a. enqueue → outbox.enqueue() first, then the in-memory queue
          carries the row id alongside the payload.
       b. Successful send → outbox.ack(row_id).
       c. Failed send  → outbox.mark_attempt(row_id) (row stays for
          retry; mainline retry policy is PR 3's background flusher).
  3. Replay on init: an Outbox file with pending rows hands them
     straight back to the emitter's in-memory queue at construction,
     so a fresh process picks up where the previous one left off.

PR 3 will add the opportunistic background flusher that periodically
re-drains the outbox while the network is reachable. In this PR a
failed POST simply leaves the row pending; the next ``flush()`` /
process restart picks it up.
"""

from __future__ import annotations

import time
from pathlib import Path

from postrule.cloud.outbox import Outbox
from postrule.cloud.verdict_telemetry import CloudVerdictEmitter


class _RecordingSender:
    """Capture every payload; replay-controlled success."""

    def __init__(self, *, succeed: bool = True) -> None:
        self.calls: list[dict] = []
        self._succeed = succeed

    def post(self, payload: dict) -> bool:
        self.calls.append(payload)
        return self._succeed


def _make_outbox(tmp_path: Path) -> Outbox:
    return Outbox(path=tmp_path / "ob.sqlite")


def _make_emitter(
    sender: _RecordingSender,
    outbox: Outbox | None = None,
    *,
    start_thread: bool = True,
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
    )


def _outcome_event(switch: str = "t") -> dict:
    return {"switch": switch, "outcome": "correct", "phase": "P0"}


# ---------------------------------------------------------------------------
# Outbox=None preserves today's behavior
# ---------------------------------------------------------------------------


class TestNoOutboxParity:
    def test_emitter_works_without_an_outbox(self) -> None:
        sender = _RecordingSender()
        em = _make_emitter(sender, outbox=None)
        try:
            em.emit("outcome", _outcome_event())
            em.flush(5.0)
            assert len(sender.calls) == 1
        finally:
            em.close(timeout=0.1)


# ---------------------------------------------------------------------------
# Outbox=Outbox(...) — persist on enqueue, ack on send
# ---------------------------------------------------------------------------


class TestOutboxPersistAndAck:
    def test_enqueue_writes_to_outbox_before_in_memory_queue(self, tmp_path: Path) -> None:
        # Sender thread off — we want to observe the durable state
        # without the consumer racing us.
        ob = _make_outbox(tmp_path)
        em = _make_emitter(_RecordingSender(), outbox=ob, start_thread=False)
        try:
            em.emit("outcome", _outcome_event("hello"))
            # The payload is on disk before the sender has touched it.
            rows = ob.pending(limit=10)
            assert len(rows) == 1
            assert rows[0][1]["switch_name"] == "hello"
        finally:
            em.close(timeout=0.1)
            ob.close()

    def test_successful_send_acks_the_outbox_row(self, tmp_path: Path) -> None:
        ob = _make_outbox(tmp_path)
        sender = _RecordingSender(succeed=True)
        em = _make_emitter(sender, outbox=ob)
        try:
            em.emit("outcome", _outcome_event())
            em.flush(5.0)
            assert ob.pending_count() == 0
            assert len(sender.calls) == 1
        finally:
            em.close(timeout=0.1)
            ob.close()

    def test_failed_send_leaves_row_and_marks_attempt(self, tmp_path: Path) -> None:
        ob = _make_outbox(tmp_path)
        sender = _RecordingSender(succeed=False)
        em = _make_emitter(sender, outbox=ob)
        try:
            em.emit("outcome", _outcome_event())
            em.flush(5.0)
            # Row stays for retry; sender did record the attempt.
            assert ob.pending_count() == 1
            rid = ob.pending(limit=1)[0][0]
            assert ob.attempts(rid) >= 1
            assert len(sender.calls) == 1
        finally:
            em.close(timeout=0.1)
            ob.close()


# ---------------------------------------------------------------------------
# Replay on init — durable state is the contract
# ---------------------------------------------------------------------------


class TestReplayOnInit:
    def test_pre_existing_rows_replay_into_new_emitter(self, tmp_path: Path) -> None:
        # Producer process: writes 3 rows then dies.
        ob1 = Outbox(path=tmp_path / "ob.sqlite")
        ob1.enqueue({"switch_name": "first", "phase": "P0"})
        ob1.enqueue({"switch_name": "second", "phase": "P0"})
        ob1.enqueue({"switch_name": "third", "phase": "P0"})
        ob1.close()

        # Consumer process: re-opens the outbox and starts an emitter.
        # All three rows should drain to the sender.
        ob2 = Outbox(path=tmp_path / "ob.sqlite")
        sender = _RecordingSender(succeed=True)
        em = _make_emitter(sender, outbox=ob2)
        try:
            # Give the daemon thread time to drain. flush() waits on
            # the in-memory queue's unfinished_tasks counter — replay
            # bumps that counter so flush() catches the replay path.
            em.flush(5.0)
            switch_names = sorted(p["switch_name"] for p in sender.calls)
            assert switch_names == ["first", "second", "third"]
            assert ob2.pending_count() == 0
        finally:
            em.close(timeout=0.1)
            ob2.close()

    def test_emitter_with_no_pre_existing_rows_starts_clean(self, tmp_path: Path) -> None:
        ob = _make_outbox(tmp_path)
        sender = _RecordingSender(succeed=True)
        em = _make_emitter(sender, outbox=ob)
        try:
            # Without emitting anything, the sender hasn't been called.
            time.sleep(0.05)
            assert sender.calls == []
        finally:
            em.close(timeout=0.1)
            ob.close()


# ---------------------------------------------------------------------------
# Stats parity — sent/failed accounting still tracks
# ---------------------------------------------------------------------------


class TestStatsAlongsideOutbox:
    def test_sent_counter_tracks_successful_sends(self, tmp_path: Path) -> None:
        ob = _make_outbox(tmp_path)
        sender = _RecordingSender(succeed=True)
        em = _make_emitter(sender, outbox=ob)
        try:
            for _ in range(5):
                em.emit("outcome", _outcome_event())
            em.flush(5.0)
            assert em.stats()["sent"] == 5
            assert em.stats()["failed"] == 0
        finally:
            em.close(timeout=0.1)
            ob.close()

    def test_failed_counter_tracks_unsuccessful_sends(self, tmp_path: Path) -> None:
        ob = _make_outbox(tmp_path)
        sender = _RecordingSender(succeed=False)
        em = _make_emitter(sender, outbox=ob)
        try:
            for _ in range(3):
                em.emit("outcome", _outcome_event())
            em.flush(5.0)
            assert em.stats()["sent"] == 0
            assert em.stats()["failed"] == 3
            # And the rows are still durable.
            assert ob.pending_count() == 3
        finally:
            em.close(timeout=0.1)
            ob.close()
