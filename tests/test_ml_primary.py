# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for Phase 4 (ML_WITH_FALLBACK) and Phase 5 (ML_PRIMARY + circuit breaker)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from dendra import (
    LearnedSwitch,
    MLPrediction,
    Phase,
    SwitchConfig,
)


@dataclass
class FakeMLHead:
    label: str = "bug"
    confidence: float = 0.95
    raises: bool = False
    version: str = "fake-1"
    predict_calls: int = 0

    def fit(self, records): ...

    def predict(self, input, labels):
        self.predict_calls += 1
        if self.raises:
            raise RuntimeError("model went down")
        return MLPrediction(label=self.label, confidence=self.confidence)

    def model_version(self):
        return self.version


def _rule(ticket: dict) -> str:
    if "crash" in (ticket.get("title", "") or "").lower():
        return "bug"
    return "feature_request"


# ---------------------------------------------------------------------------
# Phase 4: ML_WITH_FALLBACK
# ---------------------------------------------------------------------------


class TestMLWithFallback:
    def test_uses_ml_when_confident(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=FakeMLHead(label="feature_request", confidence=0.97),
            config=SwitchConfig(phase=Phase.ML_WITH_FALLBACK, confidence_threshold=0.85),
        )
        r = s.classify({"title": "App keeps crashing"})
        assert r.output == "feature_request"
        assert r.source == "ml"
        assert r.confidence == pytest.approx(0.97)

    def test_rule_fallback_on_low_confidence(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=FakeMLHead(label="feature_request", confidence=0.30),
            config=SwitchConfig(phase=Phase.ML_WITH_FALLBACK, confidence_threshold=0.85),
        )
        r = s.classify({"title": "App keeps crashing"})
        assert r.output == "bug"
        assert r.source == "rule_fallback"

    def test_rule_fallback_on_ml_error(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=FakeMLHead(raises=True),
            config=SwitchConfig(phase=Phase.ML_WITH_FALLBACK),
        )
        r = s.classify({"title": "App keeps crashing"})
        assert r.output == "bug"
        assert r.source == "rule_fallback"

    def test_requires_ml_head(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            config=SwitchConfig(phase=Phase.ML_WITH_FALLBACK),
        )
        with pytest.raises(ValueError, match="ml"):
            s.classify({"title": "x"})


# ---------------------------------------------------------------------------
# Phase 5: ML_PRIMARY + circuit breaker
# ---------------------------------------------------------------------------


class TestMLPrimary:
    def test_uses_ml_output_always(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=FakeMLHead(label="feature_request", confidence=0.55),
            config=SwitchConfig(phase=Phase.ML_PRIMARY),
        )
        # Even at low confidence, Phase 5 trusts the ML — the safety floor
        # is the circuit breaker, not the confidence threshold.
        r = s.classify({"title": "App keeps crashing"})
        assert r.output == "feature_request"
        assert r.source == "ml"

    def test_circuit_trips_on_ml_error(self):
        ml = FakeMLHead(raises=True)
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=ml,
            config=SwitchConfig(phase=Phase.ML_PRIMARY, safety_critical=False),
        )
        r = s.classify({"title": "App keeps crashing"})
        # First call trips breaker and falls back.
        assert r.source == "rule_fallback"
        assert r.output == "bug"
        assert s.status().circuit_breaker_tripped is True

    def test_subsequent_calls_stay_in_fallback_until_reset(self):
        ml = FakeMLHead(raises=True)
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=ml,
            config=SwitchConfig(phase=Phase.ML_PRIMARY),
        )
        s.classify({"title": "App keeps crashing"})
        # Restore the ML head.
        ml.raises = False
        r = s.classify({"title": "App keeps crashing"})
        # Breaker is still tripped → rule wins even though ML now works.
        assert r.source == "rule_fallback"

    def test_breaker_reset_restores_ml(self):
        ml = FakeMLHead(raises=True)
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=ml,
            config=SwitchConfig(phase=Phase.ML_PRIMARY),
        )
        s.classify({"title": "App keeps crashing"})
        ml.raises = False
        s.reset_circuit_breaker()
        r = s.classify({"title": "App keeps crashing"})
        assert r.source == "ml"
        assert r.output == "bug"

    def test_safety_critical_caps_at_phase_4(self):
        # Paper §7.1: safety_critical=True must refuse to graduate to ML_PRIMARY.
        cfg = SwitchConfig(phase=Phase.ML_PRIMARY, safety_critical=True)
        with pytest.raises(ValueError, match="safety_critical"):
            LearnedSwitch(
                name="triage",
                rule=_rule,
                author="alice",
                ml_head=FakeMLHead(),
                config=cfg,
            )
