# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for the core LearnedSwitch class (Phase 0 — RULE mode)."""

from __future__ import annotations

import pytest

from dendra import (
    ClassificationRecord,
    ClassificationResult,
    InMemoryStorage,
    LearnedSwitch,
    Phase,
    SwitchConfig,
    SwitchStatus,
    Verdict,
)


def _rule(ticket: dict) -> str:
    if "crash" in (ticket.get("title", "") or "").lower():
        return "bug"
    return "feature_request"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_minimal_args(self):
        s = LearnedSwitch(name="x", rule=_rule, author="alice")
        assert s.name == "x"
        assert s.author == "alice"
        assert s.phase() is Phase.RULE

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name"):
            LearnedSwitch(name="", rule=_rule, author="alice")

    def test_rejects_non_callable_rule(self):
        with pytest.raises(ValueError, match="callable"):
            LearnedSwitch(name="x", rule="not-callable", author="alice")  # type: ignore[arg-type]

    def test_rejects_empty_author(self):
        with pytest.raises(ValueError, match="author"):
            LearnedSwitch(name="x", rule=_rule, author="")

    def test_default_storage_is_bounded_in_memory(self):
        from dendra import BoundedInMemoryStorage

        s = LearnedSwitch(name="x", rule=_rule, author="alice")
        assert isinstance(s.storage, BoundedInMemoryStorage)

    def test_accepts_explicit_storage(self):
        store = InMemoryStorage()
        s = LearnedSwitch(name="x", rule=_rule, author="alice", storage=store)
        assert s.storage is store


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------


class TestClassify:
    def test_returns_rule_output(self):
        s = LearnedSwitch(name="triage", rule=_rule, author="alice")
        result = s.classify({"title": "App keeps crashing"})
        assert isinstance(result, ClassificationResult)
        assert result.label == "bug"

    def test_source_is_rule_in_phase_0(self):
        s = LearnedSwitch(name="triage", rule=_rule, author="alice")
        result = s.classify({"title": "add feature"})
        assert result.source == "rule"
        assert result.confidence == 1.0
        assert result.phase is Phase.RULE


# ---------------------------------------------------------------------------
# record_verdict()
# ---------------------------------------------------------------------------


class TestRecordOutcome:
    def test_appends_record_to_storage(self):
        s = LearnedSwitch(name="triage", rule=_rule, author="alice")
        s.record_verdict(
            input={"title": "App keeps crashing"},
            label="bug",
            outcome=Verdict.CORRECT.value,
        )
        records = s.storage.load_records("triage")
        assert len(records) == 1
        assert records[0].outcome == Verdict.CORRECT.value
        assert records[0].label == "bug"

    def test_records_carry_timestamp_and_source(self):
        s = LearnedSwitch(name="triage", rule=_rule, author="alice")
        s.record_verdict(
            input={"title": "x"},
            label="feature_request",
            outcome=Verdict.INCORRECT.value,
        )
        r = s.storage.load_records("triage")[0]
        assert isinstance(r, ClassificationRecord)
        assert r.timestamp > 0
        assert r.source == "rule"
        assert r.confidence == 1.0

    def test_rejects_unknown_outcome_string(self):
        s = LearnedSwitch(name="triage", rule=_rule, author="alice")
        with pytest.raises(ValueError, match="outcome"):
            s.record_verdict(
                input={},
                label="x",
                outcome="not-a-valid-outcome",
            )


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


class TestStatus:
    def test_empty_status(self):
        s = LearnedSwitch(name="triage", rule=_rule, author="alice")
        st = s.status()
        assert isinstance(st, SwitchStatus)
        assert st.phase is Phase.RULE
        assert st.outcomes_total == 0
        assert st.outcomes_correct == 0
        assert st.outcomes_incorrect == 0

    def test_tracks_outcomes(self):
        s = LearnedSwitch(name="triage", rule=_rule, author="alice")
        for _ in range(3):
            s.record_verdict(input={}, label="bug", outcome=Verdict.CORRECT.value)
        s.record_verdict(input={}, label="bug", outcome=Verdict.INCORRECT.value)
        s.record_verdict(input={}, label="bug", outcome=Verdict.UNKNOWN.value)
        st = s.status()
        assert st.outcomes_total == 5
        assert st.outcomes_correct == 3
        assert st.outcomes_incorrect == 1


# ---------------------------------------------------------------------------
# SwitchConfig
# ---------------------------------------------------------------------------


class TestSwitchConfig:
    def test_defaults(self):
        cfg = SwitchConfig()
        assert cfg.confidence_threshold == pytest.approx(0.85)
        assert cfg.safety_critical is False

    def test_config_attached_to_switch(self):
        s = LearnedSwitch(
            name="x",
            rule=_rule,
            author="alice",
            config=SwitchConfig(safety_critical=True),
        )
        assert s.config.safety_critical is True
