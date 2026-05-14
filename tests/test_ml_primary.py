# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for Phase 4 (ML_WITH_FALLBACK) and Phase 5 (ML_PRIMARY + circuit breaker)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from postrule import (
    LearnedSwitch,
    MLPrediction,
    ModelPrediction,
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


@dataclass
class FakeModel:
    label: str = "bug"
    confidence: float = 0.95
    raises: bool = False
    classify_calls: int = 0

    def classify(self, input, labels):
        self.classify_calls += 1
        if self.raises:
            raise RuntimeError("model classifier went down")
        return ModelPrediction(label=self.label, confidence=self.confidence)


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
        assert r.label == "feature_request"
        assert r.source == "ml"
        assert r.confidence == pytest.approx(0.97)

    def test_rule_fallback_on_low_confidence_no_model(self):
        # No model classifier wired up: cascade collapses to H -> R,
        # preserving v1.0 behavior.
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=FakeMLHead(label="feature_request", confidence=0.30),
            config=SwitchConfig(phase=Phase.ML_WITH_FALLBACK, confidence_threshold=0.85),
        )
        r = s.classify({"title": "App keeps crashing"})
        assert r.label == "bug"
        assert r.source == "rule_fallback"

    def test_cascades_to_model_on_low_confidence_h(self):
        # Predecessor-cascade: low-confidence H falls to MODEL_PRIMARY logic
        # (M then R), not directly to R. The model classifier is the next
        # tier down the cascade.
        ml = FakeMLHead(label="feature_request", confidence=0.30)
        model = FakeModel(label="bug", confidence=0.97)
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=ml,
            model=model,
            config=SwitchConfig(phase=Phase.ML_WITH_FALLBACK, confidence_threshold=0.85),
        )
        r = s.classify({"title": "Some unrelated text"})
        assert r.label == "bug"
        assert r.source == "model"
        assert model.classify_calls == 1
        # H prediction is preserved on the result for audit purposes.
        assert r._ml_output == "feature_request"
        assert r._ml_confidence == pytest.approx(0.30)

    def test_cascades_to_rule_on_low_confidence_h_and_m(self):
        # Both tiers below threshold: cascade reaches R.
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=FakeMLHead(label="feature_request", confidence=0.30),
            model=FakeModel(label="feature_request", confidence=0.40),
            config=SwitchConfig(phase=Phase.ML_WITH_FALLBACK, confidence_threshold=0.85),
        )
        r = s.classify({"title": "App keeps crashing"})
        assert r.label == "bug"
        assert r.source == "rule_fallback"

    def test_cascades_to_model_on_h_failure(self):
        # H raises: cascade falls to MODEL_PRIMARY logic (M then R).
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=FakeMLHead(raises=True),
            model=FakeModel(label="feature_request", confidence=0.95),
            config=SwitchConfig(phase=Phase.ML_WITH_FALLBACK),
        )
        r = s.classify({"title": "App keeps crashing"})
        assert r.label == "feature_request"
        assert r.source == "model"

    def test_rule_fallback_on_ml_error_no_model(self):
        # H raises and no model wired: cascade collapses to R.
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            ml_head=FakeMLHead(raises=True),
            config=SwitchConfig(phase=Phase.ML_WITH_FALLBACK),
        )
        r = s.classify({"title": "App keeps crashing"})
        assert r.label == "bug"
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
        assert r.label == "feature_request"
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
        assert r.label == "bug"
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
        assert r.label == "bug"

    def test_safety_critical_caps_at_phase_4(self):
        # Paper §7.1: safety_critical=True must refuse to graduate to ML_PRIMARY.
        # Check now fires at SwitchConfig.__post_init__ (tighter — the
        # misconfiguration is caught at the config-construction source rather
        # than at LearnedSwitch construction).
        with pytest.raises(ValueError, match="safety_critical"):
            SwitchConfig(starting_phase=Phase.ML_PRIMARY, safety_critical=True)
