# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Storage hardening tests — fsync, flock, multi-process writer races,
rotation-under-contention, Windows fallback warning.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time
import warnings

import pytest

from dendra import (
    ClassificationRecord,
    FileStorage,
    StorageBase,
    Verdict,
    deserialize_record,
    flock_supported,
    serialize_record,
)


def _record(output: str = "bug", ts: float | None = None) -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=ts if ts is not None else time.time(),
        input={"title": "x", "pid": os.getpid()},
        label=output,
        outcome=Verdict.CORRECT.value,
        source="rule",
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# Helpers for subprocess / multiprocess writers
# ---------------------------------------------------------------------------


def _worker_append(base_path: str, switch_name: str, count: int, marker: str) -> int:
    """Subprocess entry-point: append `count` records with the given marker."""
    storage = FileStorage(base_path)
    for i in range(count):
        storage.append_record(switch_name, _record(output=f"{marker}-{i}"))
    return count


def _worker_rotate_heavy(
    base_path: str,
    switch_name: str,
    count: int,
    marker: str,
    max_bytes: int,
    max_rotated: int,
) -> int:
    """Force frequent rotations by using a tight byte cap.

    ``max_rotated`` is sized to ensure retention never drops data
    during the test — we want to isolate rotation races, not
    exercise the aging-out policy.
    """
    storage = FileStorage(
        base_path,
        max_bytes_per_segment=max_bytes,
        max_rotated_segments=max_rotated,
    )
    for i in range(count):
        storage.append_record(switch_name, _record(output=f"{marker}-{i}"))
    return count


# ---------------------------------------------------------------------------
# Round-trip helpers
# ---------------------------------------------------------------------------


class TestSerializationHelpers:
    def test_roundtrip_exact(self):
        rec = _record(output="bug", ts=12345.0)
        line = serialize_record(rec)
        back = deserialize_record(line)
        assert back == rec

    def test_deserialize_raises_on_junk(self):
        import json as _json

        with pytest.raises(_json.JSONDecodeError):
            deserialize_record("not-json")

    def test_deserialize_raises_on_wrong_shape(self):
        with pytest.raises(TypeError):
            deserialize_record('{"wrong": "shape"}')


# ---------------------------------------------------------------------------
# StorageBase ABC
# ---------------------------------------------------------------------------


class TestStorageBaseABC:
    def test_cannot_instantiate_without_overrides(self):
        with pytest.raises(TypeError):
            StorageBase()  # type: ignore[abstract]

    def test_concrete_subclass_satisfies_protocol(self):
        class MyBackend(StorageBase):
            def __init__(self):
                self._log: list[ClassificationRecord] = []

            def append_record(self, switch_name, record):
                self._log.append(record)

            def load_records(self, switch_name):
                return list(self._log)

        b = MyBackend()
        b.append_record("x", _record("a"))
        assert [r.label for r in b.load_records("x")] == ["a"]


# ---------------------------------------------------------------------------
# FileStorage durability / fsync
# ---------------------------------------------------------------------------


class TestFsync:
    def test_fsync_kwarg_accepted(self, tmp_path):
        storage = FileStorage(tmp_path, fsync=True)
        storage.append_record("s", _record(output="a"))
        records = storage.load_records("s")
        assert len(records) == 1
        assert records[0].label == "a"

    def test_fsync_called_when_enabled(self, tmp_path, monkeypatch):
        called = []
        real_fsync = os.fsync

        def spy_fsync(fd):
            called.append(fd)
            return real_fsync(fd)

        monkeypatch.setattr("dendra.storage.os.fsync", spy_fsync)
        storage = FileStorage(tmp_path, fsync=True)
        storage.append_record("s", _record(output="a"))
        assert len(called) == 1

    def test_fsync_not_called_when_disabled(self, tmp_path, monkeypatch):
        called = []

        def spy_fsync(fd):
            called.append(fd)

        monkeypatch.setattr("dendra.storage.os.fsync", spy_fsync)
        storage = FileStorage(tmp_path, fsync=False)
        storage.append_record("s", _record(output="a"))
        assert called == []


# ---------------------------------------------------------------------------
# FileStorage concurrency — multi-process writers
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not flock_supported(), reason="POSIX flock required for concurrent-writer safety"
)
class TestMultiProcessWriters:
    """Exercise the flock-protected write path.

    Spawn N worker processes, each appending M records in parallel.
    After all complete, the log must contain exactly N*M well-formed
    records — no corruption, no partial lines, no data loss.
    """

    def test_no_data_loss_under_contention(self, tmp_path):
        n_workers = 4
        records_per_worker = 250
        ctx = mp.get_context("spawn")
        with ctx.Pool(n_workers) as pool:
            results = pool.starmap(
                _worker_append,
                [
                    (str(tmp_path), "triage", records_per_worker, f"w{i}")
                    for i in range(n_workers)
                ],
            )
        assert sum(results) == n_workers * records_per_worker

        # Read back and verify every record parsed cleanly.
        reader = FileStorage(tmp_path)
        all_records = reader.load_records("triage")
        assert len(all_records) == n_workers * records_per_worker

        # Every worker's records are present; none were overwritten.
        marker_counts: dict[str, int] = {}
        for rec in all_records:
            marker = rec.label.split("-")[0]
            marker_counts[marker] = marker_counts.get(marker, 0) + 1
        assert marker_counts == {f"w{i}": records_per_worker for i in range(n_workers)}

    def test_no_data_loss_with_frequent_rotation(self, tmp_path):
        """Force rotation mid-flight: tight byte cap → many rotations.

        Retention is sized generously (200 segments × 4 KB = 800 KB)
        so the aging-out policy never kicks in during the test. Any
        record loss observed here comes from a rotation race — which
        is what we're actually testing.
        """
        n_workers = 4
        records_per_worker = 150
        max_bytes = 4096
        max_rotated = 200
        ctx = mp.get_context("spawn")
        with ctx.Pool(n_workers) as pool:
            pool.starmap(
                _worker_rotate_heavy,
                [
                    (
                        str(tmp_path),
                        "triage",
                        records_per_worker,
                        f"w{i}",
                        max_bytes,
                        max_rotated,
                    )
                    for i in range(n_workers)
                ],
            )

        reader = FileStorage(
            tmp_path,
            max_bytes_per_segment=max_bytes,
            max_rotated_segments=max_rotated,
        )
        all_records = reader.load_records("triage")

        assert len(all_records) == n_workers * records_per_worker, (
            f"Expected {n_workers * records_per_worker} records under "
            f"rotation contention, got {len(all_records)}. This is the "
            "rotation-race canary — if it flaps, the exclusive-lock "
            "around _rotate() has regressed."
        )


# ---------------------------------------------------------------------------
# Lock disable mode — opt-out for single-writer workflows
# ---------------------------------------------------------------------------


class TestLockOptOut:
    def test_lock_false_skips_lockfile(self, tmp_path):
        storage = FileStorage(tmp_path, lock=False)
        storage.append_record("s", _record(output="a"))
        lock_path = tmp_path / "s" / ".lock"
        # With lock=False, no .lock sentinel is created.
        assert not lock_path.exists()

    def test_lock_true_creates_lockfile(self, tmp_path):
        storage = FileStorage(tmp_path, lock=True)
        storage.append_record("s", _record(output="a"))
        lock_path = tmp_path / "s" / ".lock"
        if flock_supported():
            assert lock_path.exists()
        # On Windows (no fcntl) the lockfile isn't created; warning
        # path is covered in TestWindowsFallback.


# ---------------------------------------------------------------------------
# Windows fallback — skipped on POSIX hosts
# ---------------------------------------------------------------------------


class TestWindowsFallback:
    @pytest.mark.skipif(
        sys.platform != "win32", reason="Windows-specific fallback path"
    )
    def test_warning_emitted_once_on_windows(self, tmp_path):
        # Reset the module-level flag so the warning fires.
        import dendra.storage as st

        st._WINDOWS_LOCK_WARNING_ISSUED = False
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            storage = FileStorage(tmp_path)
            storage.append_record("s", _record("a"))
            storage.append_record("s", _record("b"))
            matches = [x for x in w if "flock" in str(x.message).lower()]
            assert len(matches) == 1


# ---------------------------------------------------------------------------
# Compact (public rotation trigger)
# ---------------------------------------------------------------------------


class TestCompact:
    def test_compact_rotates_active_to_segment_1(self, tmp_path):
        storage = FileStorage(tmp_path)
        storage.append_record("s", _record("a"))
        assert (tmp_path / "s" / "outcomes.jsonl").exists()

        storage.compact("s")
        # Active gone, .1 exists with the previous content.
        assert not (tmp_path / "s" / "outcomes.jsonl").exists()
        assert (tmp_path / "s" / "outcomes.jsonl.1").exists()

        storage.append_record("s", _record("b"))
        out = [r.label for r in storage.load_records("s")]
        assert out == ["a", "b"]


# ---------------------------------------------------------------------------
# Path-traversal guard (v1 finding #1)
# ---------------------------------------------------------------------------


class TestPathTraversal:
    def test_parent_component_in_switch_name_rejected(self, tmp_path):
        storage = FileStorage(tmp_path)
        with pytest.raises(ValueError, match="must not contain '..'"):
            storage.append_record("../pwned", _record("x"))

    def test_nested_parent_component_rejected(self, tmp_path):
        storage = FileStorage(tmp_path)
        with pytest.raises(ValueError, match="must not contain '..'"):
            storage.append_record("legit/../../../etc", _record("x"))

    def test_absolute_switch_name_rejected(self, tmp_path):
        storage = FileStorage(tmp_path)
        with pytest.raises(ValueError, match="must be relative"):
            storage.append_record("/etc/passwd", _record("x"))

    def test_empty_switch_name_rejected(self, tmp_path):
        storage = FileStorage(tmp_path)
        with pytest.raises(ValueError, match="cannot be empty"):
            storage.append_record("", _record("x"))

    def test_legit_nested_name_allowed(self, tmp_path):
        """Nested paths without '..' are fine — still inside base_path."""
        storage = FileStorage(tmp_path)
        storage.append_record("team-a/switch-1", _record("x"))
        assert (tmp_path / "team-a" / "switch-1" / "outcomes.jsonl").exists()


# ---------------------------------------------------------------------------
# Batched-async FileStorage (v1 finding #29)
# ---------------------------------------------------------------------------


class TestBatchedFileStorage:
    def test_load_records_flushes_pending_writes(self, tmp_path):
        storage = FileStorage(tmp_path, batching=True, flush_interval_ms=5000)
        try:
            storage.append_record("s", _record("a"))
            storage.append_record("s", _record("b"))
            # flush_interval_ms=5000 — the background thread hasn't
            # drained yet. load_records must flush synchronously.
            records = storage.load_records("s")
            assert [r.label for r in records] == ["a", "b"]
        finally:
            storage.close()

    def test_close_drains_pending(self, tmp_path):
        storage = FileStorage(tmp_path, batching=True, flush_interval_ms=5000)
        storage.append_record("s", _record("a"))
        storage.append_record("s", _record("b"))
        storage.close()

        # Re-open and confirm the writes survived.
        storage2 = FileStorage(tmp_path, batching=False)
        assert [r.label for r in storage2.load_records("s")] == ["a", "b"]
        storage2.close()

    def test_background_flusher_drains_on_interval(self, tmp_path):
        storage = FileStorage(tmp_path, batching=True, flush_interval_ms=20)
        try:
            storage.append_record("s", _record("a"))
            # Poll the disk until the flusher drains the write.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if (tmp_path / "s" / "outcomes.jsonl").exists():
                    break
                time.sleep(0.01)
            assert (tmp_path / "s" / "outcomes.jsonl").exists()
        finally:
            storage.close()

    def test_batch_size_triggers_early_flush(self, tmp_path):
        """Hitting batch_size sets the flush_event even before the timer."""
        storage = FileStorage(
            tmp_path, batching=True, batch_size=3, flush_interval_ms=5000,
        )
        try:
            for label in ("a", "b", "c"):
                storage.append_record("s", _record(label))
            # Batch-size hit — flusher woken via the event. Poll briefly.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if (tmp_path / "s" / "outcomes.jsonl").exists():
                    break
                time.sleep(0.01)
            recs = storage.load_records("s")
            assert [r.label for r in recs] == ["a", "b", "c"]
        finally:
            storage.close()

    def test_append_after_close_raises(self, tmp_path):
        storage = FileStorage(tmp_path, batching=True)
        storage.close()
        with pytest.raises(RuntimeError, match="after close"):
            storage.append_record("s", _record("x"))


# ---------------------------------------------------------------------------
# Redaction hook (v1 finding #10 — D3 compliance)
# ---------------------------------------------------------------------------


class TestRedactionHook:
    def test_redactor_runs_before_persist_sync(self, tmp_path):
        from dataclasses import replace

        def scrub(r):
            return replace(r, input="<redacted>")

        storage = FileStorage(tmp_path, redact=scrub)
        storage.append_record("s", _record("x"))
        rec = storage.load_records("s")[0]
        assert rec.input == "<redacted>"
        storage.close()

    def test_redactor_runs_before_persist_batched(self, tmp_path):
        from dataclasses import replace

        def scrub(r):
            return replace(r, input="<redacted>")

        storage = FileStorage(tmp_path, batching=True, redact=scrub)
        try:
            storage.append_record("s", _record("x"))
            storage.flush()
            # Read from disk directly — bypasses any in-memory cache.
            raw = (tmp_path / "s" / "outcomes.jsonl").read_text()
            assert "<redacted>" in raw
            # And the PII that was in _record's input should not be there.
            # _record uses {"title": "x", "pid": ...} — "title" key gone.
            assert '"title"' not in raw
        finally:
            storage.close()

    def test_redactor_on_sqlite(self, tmp_path):
        from dataclasses import replace

        from dendra import SqliteStorage

        def scrub(r):
            return replace(r, input="<redacted>")

        storage = SqliteStorage(tmp_path / "db.sqlite", redact=scrub)
        storage.append_record("s", _record("x"))
        rec = storage.load_records("s")[0]
        assert rec.input == "<redacted>"
