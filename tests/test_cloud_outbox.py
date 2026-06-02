# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#32 PR 1 — durable SQLite outbox for cloud-bound verdict telemetry.

Today the CloudVerdictEmitter holds pending verdicts in an in-memory
`queue.Queue`. On process exit (Cloud Run redeploy, container restart,
short-lived script) anything not yet drained is lost; on offline runs
nothing accumulates for later sync. This module introduces an
SQLite-backed FIFO primitive (`Outbox`) that survives restarts and
caps growth at a bounded row count.

PR 2 wires this in as the queue backing for the emitter. PR 3 adds the
opportunistic background flusher. Together they convert "switch works"
from "cloud reachable now" to "cloud reachable eventually" — the
deepest seamless-connect fix per [private#32](https://github.com/b-tree-labs/postrule-private/issues/32).

Contract pinned by these tests:

  enqueue(payload) → row_id  (FIFO)
  pending(limit)   → [(row_id, payload), ...]
  ack(row_id)      → row deleted
  pending_count()  → integer
  mark_attempt(id) → increments attempts, sets last_attempt_at
  Restart safety   → after re-open, rows still pending
  Bounded growth   → drops OLDEST when max_rows exceeded (matches the
                      in-memory queue's drop-oldest semantic)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from postrule.cloud.outbox import Outbox

# ---------------------------------------------------------------------------
# enqueue / pending / ack — the basic FIFO contract
# ---------------------------------------------------------------------------


class TestBasicFifo:
    def test_enqueue_returns_increasing_row_ids(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        rid1 = ob.enqueue({"switch_name": "a", "request_id": "r1"})
        rid2 = ob.enqueue({"switch_name": "b", "request_id": "r2"})
        rid3 = ob.enqueue({"switch_name": "c", "request_id": "r3"})
        assert rid1 < rid2 < rid3

    def test_pending_returns_fifo_order(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        ob.enqueue({"switch_name": "first"})
        ob.enqueue({"switch_name": "second"})
        ob.enqueue({"switch_name": "third"})
        rows = ob.pending(limit=10)
        assert [p["switch_name"] for _id, p in rows] == ["first", "second", "third"]

    def test_pending_respects_limit(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        for i in range(10):
            ob.enqueue({"i": i})
        rows = ob.pending(limit=3)
        assert len(rows) == 3
        # Still FIFO: the first 3 enqueued, not the last.
        assert [p["i"] for _id, p in rows] == [0, 1, 2]

    def test_ack_removes_row(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        rid1 = ob.enqueue({"k": 1})
        ob.enqueue({"k": 2})
        ob.ack(rid1)
        rows = ob.pending(limit=10)
        assert len(rows) == 1
        assert rows[0][1]["k"] == 2

    def test_ack_on_unknown_id_is_noop(self, tmp_path: Path) -> None:
        # A double-ack from a retried sender must not blow up.
        ob = Outbox(path=tmp_path / "ob.sqlite")
        ob.enqueue({"k": 1})
        ob.ack(999_999)  # never inserted
        assert ob.pending_count() == 1

    def test_pending_count_matches_enqueued_minus_acked(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        assert ob.pending_count() == 0
        ids = [ob.enqueue({"i": i}) for i in range(5)]
        assert ob.pending_count() == 5
        ob.ack(ids[0])
        ob.ack(ids[1])
        assert ob.pending_count() == 3


# ---------------------------------------------------------------------------
# Retry instrumentation
# ---------------------------------------------------------------------------


class TestRetryInstrumentation:
    def test_mark_attempt_increments_counter(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        rid = ob.enqueue({"k": 1})
        ob.mark_attempt(rid)
        ob.mark_attempt(rid)
        ob.mark_attempt(rid)
        attempts = ob.attempts(rid)
        assert attempts == 3

    def test_mark_attempt_on_unknown_id_is_noop(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        ob.mark_attempt(999_999)  # row never existed


# ---------------------------------------------------------------------------
# Restart safety — the whole point of being durable
# ---------------------------------------------------------------------------


class TestRestartSafety:
    def test_rows_survive_outbox_recreate(self, tmp_path: Path) -> None:
        path = tmp_path / "ob.sqlite"
        ob1 = Outbox(path=path)
        ob1.enqueue({"k": "persisted"})
        ob1.close()
        # Process restart → fresh Outbox handle on the same file.
        ob2 = Outbox(path=path)
        rows = ob2.pending(limit=10)
        assert len(rows) == 1
        assert rows[0][1]["k"] == "persisted"
        ob2.close()


# ---------------------------------------------------------------------------
# Bounded growth — drop OLDEST on overflow (matches the in-memory queue)
# ---------------------------------------------------------------------------


class TestBoundedGrowth:
    def test_exceeding_max_rows_evicts_oldest(self, tmp_path: Path) -> None:
        # Max 3 rows; enqueueing 5 → only the latest 3 remain.
        ob = Outbox(path=tmp_path / "ob.sqlite", max_rows=3)
        for i in range(5):
            ob.enqueue({"i": i})
        assert ob.pending_count() == 3
        kept = [p["i"] for _id, p in ob.pending(limit=10)]
        # FIFO order preserved among the survivors — items 2,3,4 kept.
        assert kept == [2, 3, 4]

    def test_drop_counter_records_evictions(self, tmp_path: Path) -> None:
        # Operators need to know if the outbox is bleeding work — expose
        # the count of dropped rows so a UI/log line can surface it.
        ob = Outbox(path=tmp_path / "ob.sqlite", max_rows=2)
        for i in range(5):
            ob.enqueue({"i": i})
        # 5 enqueued, max 2 → 3 oldest dropped.
        assert ob.dropped_oldest_count() == 3


# ---------------------------------------------------------------------------
# Payload encoding — must round-trip arbitrary verdict shapes
# ---------------------------------------------------------------------------


class TestPayloadEncoding:
    def test_nested_payload_round_trips(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        payload = {
            "switch_name": "intent",
            "phase": "P0",
            "rule_correct": True,
            "model_correct": None,
            "metadata": {"region": "us-west", "rev": 42},
            "project": "acme-co/billing",
        }
        ob.enqueue(payload)
        rows = ob.pending(limit=1)
        assert rows[0][1] == payload

    def test_unicode_strings_survive(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        payload = {"switch_name": "intent", "note": "héllo — wörld"}
        ob.enqueue(payload)
        rows = ob.pending(limit=1)
        assert rows[0][1]["note"] == "héllo — wörld"

    @pytest.mark.parametrize("bad", [object(), {1, 2, 3}, lambda x: x])
    def test_non_json_serializable_payload_raises(self, tmp_path: Path, bad: object) -> None:
        # Defensive: callers must hand us JSON-able dicts. The emitter
        # path always does; we surface a TypeError early rather than
        # accept the payload and corrupt the file.
        ob = Outbox(path=tmp_path / "ob.sqlite")
        with pytest.raises((TypeError, ValueError)):
            ob.enqueue({"junk": bad})


class TestOldestPendingAt:
    def test_none_when_empty_then_iso_after_enqueue(self, tmp_path: Path) -> None:
        ob = Outbox(path=tmp_path / "ob.sqlite")
        assert ob.oldest_pending_at() is None
        ob.enqueue({"switch_name": "a", "request_id": "r1"})
        ts = ob.oldest_pending_at()
        assert isinstance(ts, str) and ts
        ob.close()
