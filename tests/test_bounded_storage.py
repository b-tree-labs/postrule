# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""BoundedInMemoryStorage + persist=True default wiring."""

from __future__ import annotations

import time

import pytest

from dendra import (
    BoundedInMemoryStorage,
    ClassificationRecord,
    FileStorage,
    LearnedSwitch,
    Verdict,
)


def _rec(output: str = "bug") -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=time.time(),
        input={"title": "x"},
        label=output,
        outcome=Verdict.CORRECT.value,
        source="rule",
        confidence=1.0,
    )


class TestBoundedInMemoryStorage:
    def test_evicts_oldest_past_cap(self):
        storage = BoundedInMemoryStorage(max_records=3)
        for i in range(5):
            storage.append_record("triage", _rec(output=f"o{i}"))
        outs = storage.load_records("triage")
        assert [r.label for r in outs] == ["o2", "o3", "o4"]

    def test_per_switch_isolation(self):
        storage = BoundedInMemoryStorage(max_records=2)
        storage.append_record("a", _rec("a0"))
        storage.append_record("b", _rec("b0"))
        storage.append_record("a", _rec("a1"))
        storage.append_record("a", _rec("a2"))
        assert [r.label for r in storage.load_records("a")] == ["a1", "a2"]
        assert [r.label for r in storage.load_records("b")] == ["b0"]

    def test_rejects_non_positive_cap(self):
        with pytest.raises(ValueError, match="positive"):
            BoundedInMemoryStorage(max_records=0)
        with pytest.raises(ValueError, match="positive"):
            BoundedInMemoryStorage(max_records=-5)

    def test_unknown_switch_returns_empty(self):
        storage = BoundedInMemoryStorage()
        assert storage.load_records("nothing") == []


class TestDefaultStorageWiring:
    def test_bare_switch_gets_bounded_default(self):
        s = LearnedSwitch(name="triage", rule=lambda _: "bug", author="alice")
        assert isinstance(s.storage, BoundedInMemoryStorage)

    def test_persist_true_uses_resilient_filestorage(self, tmp_path, monkeypatch):
        """persist=True wraps FileStorage in ResilientStorage by default
        so transient I/O failures do not take down classification."""
        from dendra import ResilientStorage

        monkeypatch.chdir(tmp_path)
        s = LearnedSwitch(
            name="triage",
            rule=lambda _: "bug",
            author="alice",
            persist=True,
            auto_record=False,
        )
        assert isinstance(s.storage, ResilientStorage)
        assert isinstance(s.storage.primary, FileStorage)

        r = s.classify({"title": "whatever"})
        s.record_verdict(
            input={"title": "whatever"},
            label=r.label,
            outcome=Verdict.CORRECT.value,
            source=r.source,
            confidence=r.confidence,
        )
        recs = s.storage.load_records("triage")
        assert len(recs) == 1
        assert recs[0].label == "bug"

    def test_persist_plus_explicit_storage_raises(self, tmp_path):
        with pytest.raises(ValueError, match="persist=True is incompatible"):
            LearnedSwitch(
                name="triage",
                rule=lambda _: "bug",
                author="alice",
                storage=BoundedInMemoryStorage(),
                persist=True,
            )

    def test_explicit_storage_wins(self):
        storage = BoundedInMemoryStorage(max_records=5)
        s = LearnedSwitch(name="triage", rule=lambda _: "bug", author="alice", storage=storage)
        assert s.storage is storage
