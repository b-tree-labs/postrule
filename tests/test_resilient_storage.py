# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""ResilientStorage — primary → fallback → drain-on-recovery cycle."""

from __future__ import annotations

import time
import warnings

import pytest

from dendra import (
    BoundedInMemoryStorage,
    ClassificationRecord,
    FileStorage,
    LearnedSwitch,
    ResilientStorage,
    StorageBase,
    Verdict,
)

# ResilientStorage emits UserWarnings on entry/exit of degraded mode — those
# are operator signals, not test failures. Individual tests that assert on
# the warnings use ``with warnings.catch_warnings(record=True)`` to inspect.
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _rec(label: str = "bug") -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=time.time(),
        input={"title": "x"},
        label=label,
        outcome=Verdict.CORRECT.value,
        source="rule",
        confidence=1.0,
    )


class _FlappingStorage(StorageBase):
    """Test fixture: a backend that fails on demand.

    Toggle ``fail`` to True/False to simulate disk health changes.
    Records written while healthy accumulate in ``._log``.
    """

    def __init__(self) -> None:
        self.fail: bool = False
        self._log: dict[str, list[ClassificationRecord]] = {}
        self.fail_count: int = 0
        self.append_count: int = 0

    def append_record(self, switch_name, record):
        self.append_count += 1
        if self.fail:
            self.fail_count += 1
            raise OSError("simulated disk failure")
        self._log.setdefault(switch_name, []).append(record)

    def load_records(self, switch_name):
        return list(self._log.get(switch_name, []))


# ---------------------------------------------------------------------------
# Happy-path behavior
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_writes_go_to_primary_when_healthy(self):
        primary = _FlappingStorage()
        r = ResilientStorage(primary)
        r.append_record("s", _rec("a"))
        r.append_record("s", _rec("b"))
        assert not r.degraded
        assert r.degraded_since is None
        assert r.degraded_writes == 0
        assert [x.label for x in primary.load_records("s")] == ["a", "b"]
        assert r.load_records("s") == primary.load_records("s")

    def test_load_combines_primary_and_fallback(self):
        primary = _FlappingStorage()
        r = ResilientStorage(primary, recovery_probe_every=100_000)
        r.append_record("s", _rec("a"))  # goes to primary
        primary.fail = True
        r.append_record("s", _rec("b"))  # falls back
        r.append_record("s", _rec("c"))  # falls back
        labels = [x.label for x in r.load_records("s")]
        assert labels == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Degradation + auto-recovery
# ---------------------------------------------------------------------------


class TestDegradationCycle:
    def test_primary_failure_triggers_degraded_mode(self):
        primary = _FlappingStorage()
        primary.fail = True
        r = ResilientStorage(primary, recovery_probe_every=100_000)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            r.append_record("s", _rec("a"))
            assert any("primary backend failed" in str(x.message) for x in w)
        assert r.degraded
        assert r.degraded_since is not None
        assert r.degraded_writes == 1

    def test_fallback_writes_keep_classification_alive(self):
        primary = _FlappingStorage()
        primary.fail = True
        r = ResilientStorage(primary, recovery_probe_every=100_000)
        # 100 writes all land in fallback without raising.
        for i in range(100):
            r.append_record("s", _rec(f"label-{i}"))
        assert r.degraded_writes == 100
        labels = [x.label for x in r.load_records("s")]
        assert labels == [f"label-{i}" for i in range(100)]

    def test_recovery_drains_fallback_to_primary(self):
        primary = _FlappingStorage()
        r = ResilientStorage(primary, recovery_probe_every=3)

        primary.fail = True
        r.append_record("s", _rec("a"))
        r.append_record("s", _rec("b"))
        assert r.degraded
        assert len(primary.load_records("s")) == 0

        # Heal and do one more write. At 3 writes the recovery probe
        # fires; the drain should migrate a, b, and c into primary.
        primary.fail = False
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            r.append_record("s", _rec("c"))
            assert any("primary recovered" in str(x.message) for x in w)

        assert not r.degraded
        assert r.degraded_since is None
        labels = [x.label for x in primary.load_records("s")]
        assert labels == ["a", "b", "c"]

        # Next write goes straight to primary (healthy again).
        r.append_record("s", _rec("d"))
        labels = [x.label for x in primary.load_records("s")]
        assert labels == ["a", "b", "c", "d"]

    def test_probe_failure_keeps_us_degraded(self):
        primary = _FlappingStorage()
        primary.fail = True
        r = ResilientStorage(primary, recovery_probe_every=2)
        r.append_record("s", _rec("a"))
        r.append_record("s", _rec("b"))
        # Probe fired at the 2nd degraded write and failed (primary still
        # broken) → we stay degraded and no data lost.
        assert r.degraded
        labels = [x.label for x in r.load_records("s")]
        assert labels == ["a", "b"]


# ---------------------------------------------------------------------------
# Callbacks for operator integration
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_on_degrade_fires_once_per_episode(self):
        primary = _FlappingStorage()
        primary.fail = True
        calls = []
        r = ResilientStorage(primary, on_degrade=lambda e: calls.append(e))
        r.append_record("s", _rec("a"))
        r.append_record("s", _rec("b"))
        r.append_record("s", _rec("c"))
        assert len(calls) == 1  # one entry into degraded mode, not three
        assert isinstance(calls[0], OSError)

    def test_on_recover_fires_on_drain(self):
        primary = _FlappingStorage()
        drained = []
        r = ResilientStorage(
            primary,
            recovery_probe_every=2,
            on_recover=lambda n: drained.append(n),
        )
        primary.fail = True
        r.append_record("s", _rec("a"))
        primary.fail = False
        r.append_record("s", _rec("b"))  # 2nd write → probe + drain
        assert drained == [2]

    def test_callback_exceptions_are_swallowed(self):
        primary = _FlappingStorage()
        primary.fail = True

        def bad_hook(e):
            raise RuntimeError("hook blew up")

        r = ResilientStorage(primary, on_degrade=bad_hook)
        # Should not raise — hook failures must not break classification.
        r.append_record("s", _rec("a"))
        assert r.degraded


# ---------------------------------------------------------------------------
# Real FileStorage + simulated disk failure
# ---------------------------------------------------------------------------


class TestWithFileStoragePrimary:
    def test_filestorage_permission_failure_falls_back(self, tmp_path, monkeypatch):
        """Simulate a FileStorage.append_record permission error."""
        fs = FileStorage(tmp_path)
        r = ResilientStorage(fs, recovery_probe_every=100_000)

        # Write one record fine, then break FileStorage.
        r.append_record("triage", _rec("healthy"))

        def fail_append(self, *args, **kwargs):
            raise PermissionError("EACCES")

        monkeypatch.setattr(FileStorage, "append_record", fail_append)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            r.append_record("triage", _rec("degraded"))
            assert any("primary backend failed" in str(x.message) for x in w)
        assert r.degraded

        # Reads combine: FileStorage returns ['healthy'], fallback returns
        # ['degraded']; reader sees both in order.
        labels = [x.label for x in r.load_records("triage")]
        assert labels == ["healthy", "degraded"]


# ---------------------------------------------------------------------------
# persist=True default wires ResilientStorage
# ---------------------------------------------------------------------------


class TestPersistDefaultIsResilient:
    def test_persist_true_returns_resilient_wrapper(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        s = LearnedSwitch(
            name="triage",
            rule=lambda _: "bug",
            author="alice",
            persist=True,
        )
        assert isinstance(s.storage, ResilientStorage)
        assert isinstance(s.storage.primary, FileStorage)
        assert isinstance(s.storage.fallback, BoundedInMemoryStorage)


# ---------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_rejects_non_positive_probe_interval(self):
        with pytest.raises(ValueError, match="recovery_probe_every"):
            ResilientStorage(BoundedInMemoryStorage(), recovery_probe_every=0)

    def test_custom_fallback_accepted(self):
        primary = _FlappingStorage()
        primary.fail = True
        custom_fallback = BoundedInMemoryStorage(max_records=5)
        r = ResilientStorage(primary, fallback=custom_fallback, recovery_probe_every=100_000)
        r.append_record("s", _rec("a"))
        assert [x.label for x in custom_fallback.load_records("s")] == ["a"]
