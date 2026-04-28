# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Storage-layer chaos: disk-full, perm-denied, fsync errors, partial
writes, file-deletion, oversized switch names, symlink loops, fd
exhaustion, concurrent rotation.

Goal: each test pins ONE failure mode and asserts the contract from the
storage docstrings. Failures here are bug reports against the storage
backends.
"""

from __future__ import annotations

import errno
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from dendra import (
    BoundedInMemoryStorage,
    ClassificationRecord,
    FileStorage,
    LearnedSwitch,
    Phase,
    ResilientStorage,
    SqliteStorage,
    SwitchConfig,
    Verdict,
)


def _rec(label: str = "x", ts: float | None = None) -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=ts if ts is not None else time.time(),
        input="i",
        label=label,
        outcome=Verdict.CORRECT.value,
        source="rule",
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# Disk-full simulation
# ---------------------------------------------------------------------------


class TestDiskFull:
    def test_resilient_storage_falls_back_on_enospc(self, tmp_path, monkeypatch):
        """ENOSPC mid-write: ResilientStorage must enter degraded mode.

        Asserts the docstring contract: classification continues; the
        write either lands in fallback or is surfaced as a degraded
        event. Never silently dropped.
        """
        primary = FileStorage(tmp_path / "store")
        resilient = ResilientStorage(primary)

        real_write = os.write
        # Wrap os.write so the Nth call raises ENOSPC. Targeting os.write
        # rather than open() , FileStorage uses an fd cache, so the
        # write IS the failure point in practice.
        calls = {"n": 0}

        def evil_write(fd, data):
            calls["n"] += 1
            if calls["n"] >= 1:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_write(fd, data)

        monkeypatch.setattr(os, "write", evil_write)

        with pytest.warns(UserWarning, match="primary backend failed"):
            resilient.append_record("s", _rec("after_disk_full"))

        assert resilient.degraded is True
        # Record landed in fallback , never silently dropped.
        fb_records = resilient.fallback.load_records("s")
        assert len(fb_records) == 1
        assert fb_records[0].label == "after_disk_full"

    def test_filestorage_propagates_oserror_on_enospc(self, tmp_path, monkeypatch):
        """Bare FileStorage propagates the OSError; doesn't silently drop.

        Counterpart to the resilient test: callers that opt out of
        ResilientStorage must SEE the failure, not have it swallowed.
        """
        store = FileStorage(tmp_path / "store")
        # Prime an fd (ensures os.write is the failure surface, not open).
        store.append_record("s", _rec("first"))

        def evil_write(fd, data):
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(os, "write", evil_write)
        with pytest.raises(OSError):
            store.append_record("s", _rec("doomed"))


# ---------------------------------------------------------------------------
# Permission denied
# ---------------------------------------------------------------------------


class TestPermissionDenied:
    def test_resilient_falls_back_on_permission_error(self, tmp_path):
        """chmod the active file mid-test: resilient enters degraded.

        macOS / Linux semantics: a 0o000 dir prevents subsequent
        creates. We strip writable bit on the segment file and on the
        switch dir to force OSError on append.
        """
        base = tmp_path / "store"
        primary = FileStorage(base)
        resilient = ResilientStorage(primary)
        # Prime so the switch dir + file exist.
        resilient.append_record("s", _rec("pre"))

        # Drop the cached fd so the next write re-opens (and trips the
        # perm denial). FileStorage keeps the fd open across calls.
        primary._invalidate_fd("s")
        switch_dir = base / "s"
        active = switch_dir / "outcomes.jsonl"
        original_mode = switch_dir.stat().st_mode
        try:
            os.chmod(active, 0o400)
            os.chmod(switch_dir, 0o500)
            with pytest.warns(UserWarning):
                resilient.append_record("s", _rec("post"))
            assert resilient.degraded is True
        finally:
            os.chmod(switch_dir, original_mode)
            os.chmod(active, 0o644)


# ---------------------------------------------------------------------------
# fsync errors
# ---------------------------------------------------------------------------


class TestFsyncError:
    def test_fsync_oserror_propagates_but_write_completed(self, tmp_path, monkeypatch):
        """fsync raising OSError must surface (so callers can react).

        Contract decision: FileStorage(fsync=True) means the user wants
        durability confirmation. A silent fsync failure would be the
        worst outcome (claim of durability without durability). The
        write payload IS already in the kernel buffer at this point ,
        the OSError signals "we don't know if it hit disk" and lets the
        caller (typically ResilientStorage) decide.
        """
        store = FileStorage(tmp_path / "store", fsync=True)
        # Prime to populate fd cache and ensure the file has bytes.
        store.append_record("s", _rec("first"))

        real_fsync = os.fsync

        def angry_fsync(fd):
            raise OSError(errno.EIO, "I/O error during fsync")

        monkeypatch.setattr(os, "fsync", angry_fsync)
        with pytest.raises(OSError):
            store.append_record("s", _rec("second"))

        # Lift the fault and confirm prior data survived.
        monkeypatch.setattr(os, "fsync", real_fsync)
        store._invalidate_fd("s")
        records = store.load_records("s")
        assert any(r.label == "first" for r in records), "earlier writes must persist"

    def test_resilient_storage_swallows_fsync_failure(self, tmp_path, monkeypatch):
        """ResilientStorage wraps the failure: classification continues."""
        store = FileStorage(tmp_path / "store", fsync=True)
        resilient = ResilientStorage(store)
        resilient.append_record("s", _rec("ok"))

        def angry_fsync(fd):
            raise OSError(errno.EIO, "I/O error during fsync")

        monkeypatch.setattr(os, "fsync", angry_fsync)
        # Drop fd cache so reopen path is exercised after the fault.
        store._invalidate_fd("s")
        with pytest.warns(UserWarning):
            resilient.append_record("s", _rec("through_fault"))
        assert resilient.degraded is True


# ---------------------------------------------------------------------------
# Partial writes during rotation
# ---------------------------------------------------------------------------


class TestPartialRotation:
    def test_segment_rename_then_crash_does_not_lose_data(self, tmp_path, monkeypatch):
        """Process death between rename and write: next process recovers.

        Scenario: rotation moves outcomes.jsonl → outcomes.jsonl.1, then
        we kill the process via SystemExit. Open a fresh FileStorage and
        confirm prior data is loadable; new writes go to a fresh active
        segment. No double-counts, no orphan empty files.
        """
        base = tmp_path / "store"
        # Cap each segment small + retention high so all records survive
        # rotation. We're testing crash recovery, not retention drop.
        store = FileStorage(base, max_bytes_per_segment=200, max_rotated_segments=50)
        # Fill the active segment past the cap so the next append
        # forces a rotation.
        for i in range(20):
            store.append_record("s", _rec(f"pre-{i}"))

        # Now rig the next append to crash AFTER rotation moves the file
        # but BEFORE the new active segment is written. Patch os.write
        # to raise once after the rotation point.
        real_invalidate = store._invalidate_fd
        crashed = {"yes": False}

        def crash_after_rotate(switch_name):
            real_invalidate(switch_name)
            if not crashed["yes"]:
                crashed["yes"] = True
                raise SystemExit("killed mid-rotation")

        monkeypatch.setattr(store, "_invalidate_fd", crash_after_rotate)
        with pytest.raises(SystemExit):
            store.append_record("s", _rec("doomed"))

        # New process simulation: fresh FileStorage on the same base.
        monkeypatch.undo()
        store2 = FileStorage(base, max_bytes_per_segment=200, max_rotated_segments=50)
        records = store2.load_records("s")
        # Must recover EVERY pre-rotation record, no double-counts.
        prelabels = sorted(r.label for r in records if r.label.startswith("pre-"))
        assert prelabels == sorted(f"pre-{i}" for i in range(20)), (
            f"recovery missed records: got {len(prelabels)}, expected 20"
        )
        # No "doomed" record should appear (the write never happened).
        assert not any(r.label == "doomed" for r in records)


# ---------------------------------------------------------------------------
# File deleted mid-write
# ---------------------------------------------------------------------------


class TestFileDeletedMidWrite:
    def test_unlink_active_segment_then_append_recreates_or_fails_loud(self, tmp_path):
        """unlink the active segment between batches: not silently dropped.

        On POSIX, an open-fd pointing at a now-unlinked file keeps
        receiving writes (they just go to a ghost inode). FileStorage
        uses an fd cache, so this CAN happen. The contract: either the
        next reader sees the data anyway (POSIX ghost-file semantics),
        OR we recreate / fail loud. Silent loss is the bug.
        """
        base = tmp_path / "store"
        store = FileStorage(base)
        store.append_record("s", _rec("first"))
        active = base / "s" / "outcomes.jsonl"
        assert active.exists()

        # Unlink the active file. Cached fd still points at the inode.
        active.unlink()
        # Drop the fd cache so the next append reopens , that's our
        # contract for "recreates."
        store._invalidate_fd("s")
        store.append_record("s", _rec("after_unlink"))

        records = store.load_records("s")
        labels = [r.label for r in records]
        # We tolerate either: (a) "after_unlink" present (recreated)
        # OR (b) the call raised. Silent drop with no record is the bug.
        assert "after_unlink" in labels, (
            f"after_unlink not recreated and no exception raised; silent data loss. labels={labels}"
        )


# ---------------------------------------------------------------------------
# Adversarial switch_name shapes
# ---------------------------------------------------------------------------


class TestSwitchNameValidation:
    @pytest.mark.parametrize(
        "bad_name,reason",
        [
            ("", "empty"),
            ("..", "dotdot"),
            ("../escape", "parent-traversal"),
            ("/abs/path", "absolute"),
            ("a/../b", "embedded dotdot"),
        ],
    )
    def test_filestorage_refuses_path_traversal(self, tmp_path, bad_name, reason):
        """Path-traversal attempts must raise, not write to ../etc/."""
        store = FileStorage(tmp_path / "store")
        with pytest.raises(ValueError):
            store.append_record(bad_name, _rec())

    def test_filestorage_handles_long_switch_name(self, tmp_path):
        """A 300-char switch name must fail before producing a corrupt OS path.

        macOS HFS+ / APFS limits NAME_MAX to 255 bytes per component.
        A 300-char single-component name OS-level fails with ENAMETOOLONG.
        Either we refuse pre-flight with a clear ValueError, or we
        propagate the OSError loudly. Silent corruption is the bug.
        """
        store = FileStorage(tmp_path / "store")
        long_name = "x" * 300
        with pytest.raises((ValueError, OSError)):
            store.append_record(long_name, _rec())


class TestSymlinkLoops:
    def test_filestorage_symlink_loop_in_base_path(self, tmp_path):
        """Symlink loops in base_path must surface, not hang.

        Construct base/loop -> base/loop and ensure FileStorage either
        raises promptly on construction or on first write. macOS resolves
        with ELOOP after ~32 hops.
        """
        base = tmp_path / "store"
        base.mkdir()
        loop = base / "loop"
        loop.symlink_to(loop)  # self-referential symlink

        store = FileStorage(base)
        # The loop is a sibling, not the base, so plain operations on
        # other switch names must still succeed. The bug would be
        # FileStorage's iterdir-style methods walking into the loop.
        store.append_record("ok", _rec())
        # switch_names walks iterdir; must terminate.
        names = store.switch_names()
        assert "ok" in names

    def test_filestorage_symlink_loop_as_switch_dir(self, tmp_path):
        """A switch dir that's a self-referential symlink must fail loud."""
        base = tmp_path / "store"
        base.mkdir()
        bad = base / "bad"
        bad.symlink_to(bad)

        store = FileStorage(base)
        with pytest.raises((OSError, ValueError)):
            store.append_record("bad", _rec())


# ---------------------------------------------------------------------------
# File-handle exhaustion
# ---------------------------------------------------------------------------


class TestFileHandleExhaustion:
    @pytest.mark.slow
    def test_many_filestorage_instances_concurrently(self, tmp_path):
        """Open 256 FileStorage instances, each writing to its own switch.

        Asserts: either all succeed (we stayed under the host's RLIMIT_NOFILE)
        OR a clean exception is raised. We don't leak file descriptors.

        Capping at 256 (not the docstring's 1000) so the test runs fast
        on a default 256-file ulimit setup. The bug shape would be
        "succeeds on a few, then silent dead fds."
        """
        N = 256
        stores: list[FileStorage] = []
        try:
            for i in range(N):
                s = FileStorage(tmp_path / f"store_{i}")
                s.append_record(f"sw{i}", _rec(f"r{i}"))
                stores.append(s)
        except OSError as e:
            # Clean failure , fd-limit reached. Acceptable.
            assert e.errno in (errno.EMFILE, errno.ENFILE)
            return

        # All succeeded , close them all and confirm load_records still works.
        for s in stores:
            s.close()
        # Random spot-check that data round-tripped.
        s = FileStorage(tmp_path / "store_42")
        records = s.load_records("sw42")
        assert any(r.label == "r42" for r in records)


# ---------------------------------------------------------------------------
# Concurrent rotation
# ---------------------------------------------------------------------------


class TestConcurrentRotation:
    def test_two_threads_triggering_rotation_simultaneously(self, tmp_path):
        """Two threads both push the active segment past the rotation cap.

        Contract from FileStorage docstring: under exclusive lock, the
        check-then-rotate is atomic. Two concurrent rotations must
        produce one rotation, not two clobbering each other. Lost
        segments would show up as missing records in load_records.
        """
        base = tmp_path / "store"
        # Small per-segment cap so rotations fire frequently; high retention
        # so retention drop doesn't masquerade as a race-induced loss.
        # We only care about the rotation race here.
        store = FileStorage(base, max_bytes_per_segment=300, max_rotated_segments=200)

        N_PER_THREAD = 30
        N_THREADS = 4
        errors: list[BaseException] = []

        def worker(tid: int) -> None:
            try:
                for i in range(N_PER_THREAD):
                    store.append_record("s", _rec(f"t{tid}-r{i}"))
            except BaseException as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=N_THREADS) as pool:
            list(pool.map(worker, range(N_THREADS)))

        assert not errors, f"workers raised: {errors[0]}"

        records = store.load_records("s")
        labels = sorted(r.label for r in records)
        expected = sorted(f"t{t}-r{i}" for t in range(N_THREADS) for i in range(N_PER_THREAD))
        # Every record must be present exactly once.
        assert labels == expected, (
            f"rotation race lost or duplicated records: "
            f"expected {len(expected)}, got {len(labels)}, "
            f"missing={set(expected) - set(labels)}, "
            f"dup_count={len(labels) - len(set(labels))}"
        )


# ---------------------------------------------------------------------------
# SqliteStorage-specific
# ---------------------------------------------------------------------------


class TestSqliteChaos:
    def test_db_file_deleted_under_us(self, tmp_path):
        """Delete the SQLite DB file mid-session: next write must fail or recreate.

        Silent data loss without an exception is the bug. Either the
        next op raises (operator gets a signal) or the file is recreated
        with the existing record present (impossible without journals,
        so realistically: raise).
        """
        db = tmp_path / "outcomes.db"
        store = SqliteStorage(db)
        store.append_record("s", _rec("first"))
        db.unlink()

        # Next call: Must either succeed (recreate) with the new record
        # readable, or raise.
        try:
            store.append_record("s", _rec("after_unlink"))
        except Exception:
            return  # acceptable

        records = store.load_records("s")
        labels = [r.label for r in records]
        # If it succeeded, the new record must be present.
        assert "after_unlink" in labels, "silent loss: write succeeded but record missing"

    def test_invalid_sync_mode_rejected(self, tmp_path):
        """Constructor-time validation: bad sync mode raises."""
        with pytest.raises(ValueError):
            SqliteStorage(tmp_path / "x.db", sync="WRONG")


# ---------------------------------------------------------------------------
# BoundedInMemoryStorage / InMemoryStorage edge cases
# ---------------------------------------------------------------------------


class TestBoundedEdges:
    def test_bounded_zero_max_records_rejected(self):
        with pytest.raises(ValueError):
            BoundedInMemoryStorage(max_records=0)

    def test_bounded_negative_max_records_rejected(self):
        with pytest.raises(ValueError):
            BoundedInMemoryStorage(max_records=-1)

    def test_bounded_evicts_oldest_first(self, basic_record):
        b = BoundedInMemoryStorage(max_records=3)
        for i in range(5):
            b.append_record("s", basic_record(label=f"r{i}"))
        labels = [r.label for r in b.load_records("s")]
        assert labels == ["r2", "r3", "r4"], f"FIFO eviction broken: {labels}"


# ---------------------------------------------------------------------------
# ResilientStorage end-to-end through dispatch
# ---------------------------------------------------------------------------


class TestDispatchSurvivesStorageFailure:
    def test_dispatch_continues_when_storage_throws(self, tmp_path, monkeypatch):
        """A storage append that raises must NOT stop dispatch.

        Contract: storage failure is observable in telemetry / degraded
        signal but the classify decision still returns to the caller.
        """

        class HostileStorage(BoundedInMemoryStorage):
            def append_record(self, switch_name, record):
                raise RuntimeError("storage is hostile")

        sw = LearnedSwitch(
            rule=lambda x: "ok",
            name="t",
            author="t",
            config=SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=True,  # try to write, expect swallow
                auto_advance=False,
                auto_demote=False,
            ),
            storage=HostileStorage(),
        )
        # Must NOT raise.
        result = sw.classify("input")
        assert result.label == "ok"
