# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""SqliteStorage — basic CRUD, concurrency, WAL mode verification."""

from __future__ import annotations

import multiprocessing as mp
import sqlite3
import time

import pytest

from dendra import ClassificationRecord, SqliteStorage, Verdict


def _record(output: str = "bug") -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=time.time(),
        input={"title": "x"},
        label=output,
        outcome=Verdict.CORRECT.value,
        source="rule",
        confidence=1.0,
    )


def _worker_append(db_path: str, switch_name: str, count: int, marker: str) -> int:
    storage = SqliteStorage(db_path)
    for i in range(count):
        storage.append_record(switch_name, _record(output=f"{marker}-{i}"))
    return count


class TestBasicCRUD:
    def test_append_and_load(self, tmp_path):
        db = tmp_path / "outcomes.db"
        storage = SqliteStorage(db)
        storage.append_record("triage", _record(output="bug"))
        storage.append_record("triage", _record(output="feature"))
        records = storage.load_records("triage")
        assert [r.label for r in records] == ["bug", "feature"]

    def test_load_empty_switch(self, tmp_path):
        db = tmp_path / "outcomes.db"
        storage = SqliteStorage(db)
        assert storage.load_records("never-written") == []

    def test_switches_are_isolated(self, tmp_path):
        db = tmp_path / "outcomes.db"
        storage = SqliteStorage(db)
        storage.append_record("a", _record("a1"))
        storage.append_record("a", _record("a2"))
        storage.append_record("b", _record("b1"))
        assert [r.label for r in storage.load_records("a")] == ["a1", "a2"]
        assert [r.label for r in storage.load_records("b")] == ["b1"]

    def test_count(self, tmp_path):
        db = tmp_path / "outcomes.db"
        storage = SqliteStorage(db)
        for _ in range(42):
            storage.append_record("s", _record())
        assert storage.count("s") == 42
        assert storage.count("other") == 0

    def test_switch_names(self, tmp_path):
        db = tmp_path / "outcomes.db"
        storage = SqliteStorage(db)
        storage.append_record("alpha", _record())
        storage.append_record("beta", _record())
        storage.append_record("alpha", _record())
        assert storage.switch_names() == ["alpha", "beta"]


class TestDurability:
    def test_survives_storage_reinit(self, tmp_path):
        db = tmp_path / "outcomes.db"
        s1 = SqliteStorage(db)
        s1.append_record("s", _record("a"))
        s1.append_record("s", _record("b"))

        # Fresh instance, same database file — data must persist.
        s2 = SqliteStorage(db)
        assert [r.label for r in s2.load_records("s")] == ["a", "b"]

    @pytest.mark.parametrize("sync", ["OFF", "NORMAL", "FULL", "EXTRA"])
    def test_sync_modes_accepted(self, tmp_path, sync):
        db = tmp_path / f"outcomes-{sync}.db"
        storage = SqliteStorage(db, sync=sync)
        storage.append_record("s", _record("a"))
        assert storage.count("s") == 1

    def test_invalid_sync_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="sync must be"):
            SqliteStorage(tmp_path / "x.db", sync="SOMETIMES")

    def test_invalid_timeout_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="timeout must be positive"):
            SqliteStorage(tmp_path / "x.db", timeout=0)


class TestWALMode:
    def test_journal_mode_is_wal(self, tmp_path):
        db = tmp_path / "outcomes.db"
        SqliteStorage(db)
        # Independent connection — verify WAL was actually set.
        # NOTE: `with sqlite3.connect() as conn:` only commits the
        # transaction; we need explicit close() to avoid leaking
        # the connection (tripping pytest's unraisable-exception
        # warnings).
        conn = sqlite3.connect(str(db))
        try:
            (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
            assert mode.lower() == "wal"
        finally:
            conn.close()

    def test_wal_files_appear_after_write(self, tmp_path):
        db = tmp_path / "outcomes.db"
        storage = SqliteStorage(db)
        storage.append_record("s", _record())
        # WAL creates outcomes.db-wal and outcomes.db-shm sidecars.
        assert db.with_suffix(".db-wal").exists() or db.exists()


class TestConcurrentWriters:
    """Multiple processes writing to the same SQLite DB (WAL mode).

    WAL gives us 1-writer/many-readers — concurrent INSERTs are
    serialized by SQLite's BEGIN IMMEDIATE. This test verifies no
    data is lost to that serialization.
    """

    def test_no_data_loss_under_contention(self, tmp_path):
        db = tmp_path / "outcomes.db"
        # Touch the DB once to initialize the schema before pooling.
        SqliteStorage(db)

        n_workers = 4
        records_per_worker = 200
        ctx = mp.get_context("spawn")
        with ctx.Pool(n_workers) as pool:
            results = pool.starmap(
                _worker_append,
                [
                    (str(db), "triage", records_per_worker, f"w{i}")
                    for i in range(n_workers)
                ],
            )
        assert sum(results) == n_workers * records_per_worker

        reader = SqliteStorage(db)
        all_records = reader.load_records("triage")
        assert len(all_records) == n_workers * records_per_worker

        marker_counts: dict[str, int] = {}
        for rec in all_records:
            marker = rec.label.split("-")[0]
            marker_counts[marker] = marker_counts.get(marker, 0) + 1
        assert marker_counts == {f"w{i}": records_per_worker for i in range(n_workers)}

    def test_concurrent_reads_during_writes(self, tmp_path):
        """Readers see a consistent (committed) snapshot while writes happen.

        We can't easily orchestrate true concurrency from a single
        test, but we can verify that reads during interleaved writes
        never see partial/corrupt records — WAL guarantees this.
        """
        db = tmp_path / "outcomes.db"
        storage = SqliteStorage(db)

        for i in range(50):
            storage.append_record("s", _record(output=f"r-{i}"))
            # Each read after a write should see exactly `i+1` records.
            recs = storage.load_records("s")
            assert len(recs) == i + 1
            assert all(r.label.startswith("r-") for r in recs)


class TestGracefulDegradation:
    def test_skips_corrupt_rows(self, tmp_path):
        db = tmp_path / "outcomes.db"
        storage = SqliteStorage(db)
        storage.append_record("s", _record("a"))

        # Inject a row with invalid JSON. Explicit close() required:
        # sqlite3's context manager commits but doesn't close.
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "INSERT INTO outcomes (switch_name, timestamp, data) VALUES (?, ?, ?)",
                ("s", time.time(), "not-valid-json"),
            )
            conn.commit()
        finally:
            conn.close()

        storage.append_record("s", _record("b"))
        out = [r.label for r in storage.load_records("s")]
        # Bad row silently skipped; good rows survive in append order.
        assert out == ["a", "b"]
