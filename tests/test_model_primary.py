# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for Phase 2 (MODEL_PRIMARY) — LLM decides, rule is the floor."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from dendra import (
    LearnedSwitch,
    ModelPrediction,
    Phase,
    SwitchConfig,
)


@dataclass
class FakeLM:
    label: str = "bug"
    confidence: float = 0.95
    raises: bool = False

    def classify(self, input, labels):
        if self.raises:
            raise RuntimeError("provider down")
        return ModelPrediction(label=self.label, confidence=self.confidence)


def _rule(ticket: dict) -> str:
    if "crash" in (ticket.get("title", "") or "").lower():
        return "bug"
    return "feature_request"


# ---------------------------------------------------------------------------


class TestModelPrimaryRouting:
    def test_uses_model_output_when_confident(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=FakeLM(label="feature_request", confidence=0.97),
            config=SwitchConfig(
                auto_record=False, phase=Phase.MODEL_PRIMARY, confidence_threshold=0.85
            ),
        )
        # Rule would say "bug" for a crash ticket; LLM disagrees with high
        # confidence → LLM's answer wins.
        r = s.classify({"title": "App keeps crashing"})
        assert r.label == "feature_request"
        assert r.source == "model"
        assert r.confidence == pytest.approx(0.97)
        assert r.phase is Phase.MODEL_PRIMARY

    def test_rule_fallback_on_low_confidence(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=FakeLM(label="feature_request", confidence=0.40),
            config=SwitchConfig(
                auto_record=False, phase=Phase.MODEL_PRIMARY, confidence_threshold=0.85
            ),
        )
        r = s.classify({"title": "App keeps crashing"})
        # LLM confidence below threshold → rule wins.
        assert r.label == "bug"
        assert r.source == "rule_fallback"

    def test_rule_fallback_on_model_error(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=FakeLM(raises=True),
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_PRIMARY),
        )
        r = s.classify({"title": "App keeps crashing"})
        assert r.label == "bug"
        assert r.source == "rule_fallback"
        assert r.confidence == 1.0  # rule is certain of its own output

    def test_requires_model(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_PRIMARY),
        )
        with pytest.raises(ValueError, match="model"):
            s.classify({"title": "x"})


class TestModelPrimaryOutcomeCapture:
    def test_llm_decision_records_model_fields(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=FakeLM(label="feature_request", confidence=0.97),
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_PRIMARY),
        )
        r = s.classify({"title": "App keeps crashing"})
        r.mark_correct()
        recs = s.storage.load_records("triage")
        assert len(recs) == 1
        assert recs[0].source == "model"
        assert recs[0].model_output == "feature_request"
        assert recs[0].model_confidence == pytest.approx(0.97)
        # rule_output still captured for later transition-curve analysis.
        assert recs[0].rule_output == "bug"
