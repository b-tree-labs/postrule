# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for Storage — InMemoryStorage + FileStorage."""

from __future__ import annotations

import time

import pytest

from dendra import ClassificationRecord, FileStorage, InMemoryStorage


def _record(**overrides) -> ClassificationRecord:
    # Accept either label= or the legacy output= kwarg at the helper's
    # surface to keep parameterized tests concise. The dataclass field
    # is .label.
    if "output" in overrides:
        overrides["label"] = overrides.pop("output")
    defaults = {
        "timestamp": time.time(),
        "input": {"x": 1},
        "label": "bug",
        "outcome": "correct",
        "source": "rule",
        "confidence": 1.0,
    }
    defaults.update(overrides)
    return ClassificationRecord(**defaults)


# ---------------------------------------------------------------------------
# InMemoryStorage
# ---------------------------------------------------------------------------


class TestInMemoryStorage:
    def test_append_and_load(self):
        s = InMemoryStorage()
        s.append_record("switch-a", _record(output="a"))
        s.append_record("switch-a", _record(output="b"))
        loaded = s.load_records("switch-a")
        assert len(loaded) == 2
        assert [r.label for r in loaded] == ["a", "b"]

    def test_switch_isolation(self):
        s = InMemoryStorage()
        s.append_record("switch-a", _record(output="a"))
        s.append_record("switch-b", _record(output="b"))
        assert [r.label for r in s.load_records("switch-a")] == ["a"]
        assert [r.label for r in s.load_records("switch-b")] == ["b"]

    def test_load_missing_switch_returns_empty(self):
        s = InMemoryStorage()
        assert s.load_records("nothing-here") == []


# ---------------------------------------------------------------------------
# FileStorage
# ---------------------------------------------------------------------------


class TestFileStorage:
    def test_creates_base_dir(self, tmp_path):
        base = tmp_path / "learn"
        FileStorage(base)
        assert base.is_dir()

    def test_append_writes_jsonl_line(self, tmp_path):
        s = FileStorage(tmp_path)
        s.append_record("triage", _record(output="bug"))
        path = tmp_path / "triage" / "outcomes.jsonl"
        assert path.exists()
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        assert '"label": "bug"' in lines[0]

    def test_roundtrip(self, tmp_path):
        s = FileStorage(tmp_path)
        s.append_record("triage", _record(output="a", outcome="correct"))
        s.append_record("triage", _record(output="b", outcome="incorrect"))
        records = s.load_records("triage")
        assert len(records) == 2
        assert records[0].label == "a"
        assert records[1].outcome == "incorrect"

    def test_survives_process_restart(self, tmp_path):
        """Two separate FileStorage instances against the same path must
        see each other's writes — durability is the whole point."""
        s1 = FileStorage(tmp_path)
        s1.append_record("triage", _record(output="a"))
        s2 = FileStorage(tmp_path)
        records = s2.load_records("triage")
        assert len(records) == 1
        assert records[0].label == "a"

    def test_switch_isolation(self, tmp_path):
        s = FileStorage(tmp_path)
        s.append_record("triage", _record(output="a"))
        s.append_record("router", _record(output="b"))
        assert (tmp_path / "triage" / "outcomes.jsonl").exists()
        assert (tmp_path / "router" / "outcomes.jsonl").exists()
        assert [r.label for r in s.load_records("triage")] == ["a"]
        assert [r.label for r in s.load_records("router")] == ["b"]

    def test_skips_malformed_lines_gracefully(self, tmp_path):
        s = FileStorage(tmp_path)
        s.append_record("triage", _record(output="good"))
        # Corrupt the log with an unparseable line in the middle
        path = tmp_path / "triage" / "outcomes.jsonl"
        with open(path, "a") as f:
            f.write("not-valid-json\n")
        s.append_record("triage", _record(output="recovered"))

        records = s.load_records("triage")
        outputs = [r.label for r in records]
        assert "good" in outputs
        assert "recovered" in outputs

    def test_load_missing_switch_returns_empty(self, tmp_path):
        s = FileStorage(tmp_path)
        assert s.load_records("nope") == []

    def test_base_path_accepts_str(self, tmp_path):
        # Convenience — callers commonly pass a string not a Path.
        s = FileStorage(str(tmp_path))
        s.append_record("x", _record())
        assert len(s.load_records("x")) == 1


class TestFileStorageRotation:
    """Zero-maintenance log rotation — segments cap, retention prunes."""

    def test_rotates_when_active_exceeds_cap(self, tmp_path):
        # 200-byte cap means every ~2 records triggers rotation.
        s = FileStorage(tmp_path, max_bytes_per_segment=200, max_rotated_segments=4)
        for i in range(10):
            s.append_record("s", _record(output=f"label-{i}"))
        files = sorted((tmp_path / "s").iterdir())
        # Active + at least one rotated segment present.
        assert (tmp_path / "s" / "outcomes.jsonl").exists()
        assert any("outcomes.jsonl." in p.name for p in files)

    def test_retention_cap_drops_oldest(self, tmp_path):
        # 100 bytes per segment; 2 rotated kept.
        s = FileStorage(tmp_path, max_bytes_per_segment=100, max_rotated_segments=2)
        for i in range(40):
            s.append_record("s", _record(output=f"label-{i}"))
        files = [p.name for p in (tmp_path / "s").iterdir()]
        # Never more than active + 2 rotated on disk.
        rotated = [f for f in files if f.startswith("outcomes.jsonl.")]
        assert len(rotated) <= 2

    def test_load_returns_segments_in_chronological_order(self, tmp_path):
        s = FileStorage(tmp_path, max_bytes_per_segment=150, max_rotated_segments=3)
        for i in range(12):
            s.append_record("s", _record(output=f"label-{i:02d}"))
        outputs = [r.label for r in s.load_records("s")]
        # Sorted chronologically; older labels appear before newer ones.
        # (Retention may drop the very-oldest rows; surviving rows must be
        # strictly increasing.)
        assert outputs == sorted(outputs)

    def test_no_rotation_when_below_cap(self, tmp_path):
        s = FileStorage(tmp_path, max_bytes_per_segment=10**9)
        for i in range(20):
            s.append_record("s", _record(output=f"label-{i}"))
        # The .lock sentinel is an implementation detail of the flock
        # concurrency contract, not a rotated segment.
        data_files = sorted(
            p.name for p in (tmp_path / "s").iterdir() if not p.name.startswith(".")
        )
        assert data_files == ["outcomes.jsonl"]

    def test_bytes_on_disk_and_switch_names(self, tmp_path):
        s = FileStorage(tmp_path)
        s.append_record("alpha", _record(output="a"))
        s.append_record("beta", _record(output="b"))
        assert set(s.switch_names()) == {"alpha", "beta"}
        assert s.bytes_on_disk("alpha") > 0
        assert s.bytes_on_disk("nonexistent") == 0

    def test_compact_forces_rotation(self, tmp_path):
        s = FileStorage(tmp_path, max_bytes_per_segment=10**6)
        s.append_record("s", _record(output="one"))
        assert not (tmp_path / "s" / "outcomes.jsonl.1").exists()
        s.compact("s")
        assert (tmp_path / "s" / "outcomes.jsonl.1").exists()

    def test_rejects_invalid_rotation_params(self, tmp_path):
        with pytest.raises(ValueError):
            FileStorage(tmp_path, max_bytes_per_segment=0)
        with pytest.raises(ValueError):
            FileStorage(tmp_path, max_rotated_segments=-1)
