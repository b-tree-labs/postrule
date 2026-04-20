# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for Storage — InMemoryStorage + FileStorage."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from dendra import FileStorage, InMemoryStorage, OutcomeRecord


def _record(**overrides) -> OutcomeRecord:
    defaults = dict(
        timestamp=time.time(),
        input={"x": 1},
        output="bug",
        outcome="correct",
        source="rule",
        confidence=1.0,
    )
    defaults.update(overrides)
    return OutcomeRecord(**defaults)


# ---------------------------------------------------------------------------
# InMemoryStorage
# ---------------------------------------------------------------------------


class TestInMemoryStorage:
    def test_append_and_load(self):
        s = InMemoryStorage()
        s.append_outcome("switch-a", _record(output="a"))
        s.append_outcome("switch-a", _record(output="b"))
        loaded = s.load_outcomes("switch-a")
        assert len(loaded) == 2
        assert [r.output for r in loaded] == ["a", "b"]

    def test_switch_isolation(self):
        s = InMemoryStorage()
        s.append_outcome("switch-a", _record(output="a"))
        s.append_outcome("switch-b", _record(output="b"))
        assert [r.output for r in s.load_outcomes("switch-a")] == ["a"]
        assert [r.output for r in s.load_outcomes("switch-b")] == ["b"]

    def test_load_missing_switch_returns_empty(self):
        s = InMemoryStorage()
        assert s.load_outcomes("nothing-here") == []


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
        s.append_outcome("triage", _record(output="bug"))
        path = tmp_path / "triage" / "outcomes.jsonl"
        assert path.exists()
        lines = path.read_text().splitlines()
        assert len(lines) == 1
        assert '"output": "bug"' in lines[0]

    def test_roundtrip(self, tmp_path):
        s = FileStorage(tmp_path)
        s.append_outcome("triage", _record(output="a", outcome="correct"))
        s.append_outcome("triage", _record(output="b", outcome="incorrect"))
        records = s.load_outcomes("triage")
        assert len(records) == 2
        assert records[0].output == "a"
        assert records[1].outcome == "incorrect"

    def test_survives_process_restart(self, tmp_path):
        """Two separate FileStorage instances against the same path must
        see each other's writes — durability is the whole point."""
        s1 = FileStorage(tmp_path)
        s1.append_outcome("triage", _record(output="a"))
        s2 = FileStorage(tmp_path)
        records = s2.load_outcomes("triage")
        assert len(records) == 1
        assert records[0].output == "a"

    def test_switch_isolation(self, tmp_path):
        s = FileStorage(tmp_path)
        s.append_outcome("triage", _record(output="a"))
        s.append_outcome("router", _record(output="b"))
        assert (tmp_path / "triage" / "outcomes.jsonl").exists()
        assert (tmp_path / "router" / "outcomes.jsonl").exists()
        assert [r.output for r in s.load_outcomes("triage")] == ["a"]
        assert [r.output for r in s.load_outcomes("router")] == ["b"]

    def test_skips_malformed_lines_gracefully(self, tmp_path):
        s = FileStorage(tmp_path)
        s.append_outcome("triage", _record(output="good"))
        # Corrupt the log with an unparseable line in the middle
        path = tmp_path / "triage" / "outcomes.jsonl"
        with open(path, "a") as f:
            f.write("not-valid-json\n")
        s.append_outcome("triage", _record(output="recovered"))

        records = s.load_outcomes("triage")
        outputs = [r.output for r in records]
        assert "good" in outputs
        assert "recovered" in outputs

    def test_load_missing_switch_returns_empty(self, tmp_path):
        s = FileStorage(tmp_path)
        assert s.load_outcomes("nope") == []

    def test_base_path_accepts_str(self, tmp_path):
        # Convenience — callers commonly pass a string not a Path.
        s = FileStorage(str(tmp_path))
        s.append_outcome("x", _record())
        assert len(s.load_outcomes("x")) == 1
