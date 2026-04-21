# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for Phase 2 (LLM_PRIMARY) — LLM decides, rule is the floor."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from dendra import (
    LearnedSwitch,
    LLMPrediction,
    Outcome,
    Phase,
    SwitchConfig,
)


@dataclass
class FakeLLM:
    label: str = "bug"
    confidence: float = 0.95
    raises: bool = False

    def classify(self, input, labels):
        if self.raises:
            raise RuntimeError("provider down")
        return LLMPrediction(label=self.label, confidence=self.confidence)


def _rule(ticket: dict) -> str:
    if "crash" in (ticket.get("title", "") or "").lower():
        return "bug"
    return "feature_request"


# ---------------------------------------------------------------------------


class TestLLMPrimaryRouting:
    def test_uses_llm_output_when_confident(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            llm=FakeLLM(label="feature_request", confidence=0.97),
            config=SwitchConfig(phase=Phase.LLM_PRIMARY, confidence_threshold=0.85),
        )
        # Rule would say "bug" for a crash ticket; LLM disagrees with high
        # confidence → LLM's answer wins.
        r = s.classify({"title": "App keeps crashing"})
        assert r.output == "feature_request"
        assert r.source == "llm"
        assert r.confidence == pytest.approx(0.97)
        assert r.phase is Phase.LLM_PRIMARY

    def test_rule_fallback_on_low_confidence(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            llm=FakeLLM(label="feature_request", confidence=0.40),
            config=SwitchConfig(phase=Phase.LLM_PRIMARY, confidence_threshold=0.85),
        )
        r = s.classify({"title": "App keeps crashing"})
        # LLM confidence below threshold → rule wins.
        assert r.output == "bug"
        assert r.source == "rule_fallback"

    def test_rule_fallback_on_llm_error(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            llm=FakeLLM(raises=True),
            config=SwitchConfig(phase=Phase.LLM_PRIMARY),
        )
        r = s.classify({"title": "App keeps crashing"})
        assert r.output == "bug"
        assert r.source == "rule_fallback"
        assert r.confidence == 1.0  # rule is certain of its own output

    def test_requires_llm(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            config=SwitchConfig(phase=Phase.LLM_PRIMARY),
        )
        with pytest.raises(ValueError, match="llm"):
            s.classify({"title": "x"})


class TestLLMPrimaryOutcomeCapture:
    def test_llm_decision_records_llm_fields(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            llm=FakeLLM(label="feature_request", confidence=0.97),
            config=SwitchConfig(phase=Phase.LLM_PRIMARY),
        )
        r = s.classify({"title": "App keeps crashing"})
        s.record_outcome(
            input={"title": "App keeps crashing"},
            output=r.output,
            outcome=Outcome.CORRECT.value,
            source=r.source,
            confidence=r.confidence,
        )
        recs = s.storage.load_outcomes("triage")
        assert len(recs) == 1
        assert recs[0].source == "llm"
        assert recs[0].llm_output == "feature_request"
        assert recs[0].llm_confidence == pytest.approx(0.97)
        # rule_output still captured for later transition-curve analysis.
        assert recs[0].rule_output == "bug"
