# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for Phase 3 (ML_SHADOW) — ML head trains behind the primary path."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from postrule import (
    LearnedSwitch,
    MLHead,
    MLPrediction,
    ModelPrediction,
    Phase,
    SwitchConfig,
)


@dataclass
class FakeMLHead:
    label: str = "bug"
    confidence: float = 0.73
    trained_rows: int = 0
    predict_calls: int = 0
    version: str = "fake-0"

    def fit(self, records):
        self.trained_rows = len(list(records))

    def predict(self, input, labels):
        self.predict_calls += 1
        return MLPrediction(label=self.label, confidence=self.confidence)

    def model_version(self) -> str:
        return self.version


@dataclass
class FakeLM:
    label: str = "bug"
    confidence: float = 0.9

    def classify(self, input, labels):
        return ModelPrediction(label=self.label, confidence=self.confidence)


def _rule(ticket: dict) -> str:
    if "crash" in (ticket.get("title", "") or "").lower():
        return "bug"
    return "feature_request"


# ---------------------------------------------------------------------------


class TestMLHeadProtocol:
    def test_fake_ml_head_satisfies_protocol(self):
        assert isinstance(FakeMLHead(), MLHead)

    def test_ml_prediction_shape(self):
        p = MLPrediction(label="bug", confidence=0.8)
        assert p.label == "bug"
        assert p.confidence == pytest.approx(0.8)


class TestMLShadowRouting:
    def test_shadow_does_not_change_user_decision(self):
        ml = FakeMLHead(label="feature_request", confidence=0.95)
        model_stub = FakeLM(label="bug", confidence=0.9)
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=model_stub,
            ml_head=ml,
            config=SwitchConfig(
                auto_record=False, phase=Phase.ML_SHADOW, confidence_threshold=0.85
            ),
        )
        # ML disagrees loudly; shadow mode must NOT let it influence the answer.
        # The primary path at Phase 3 is MODEL_PRIMARY semantics — LLM decides.
        r = s.classify({"title": "App keeps crashing"})
        assert r.label == "bug"
        assert r.source == "model"
        assert r.phase is Phase.ML_SHADOW
        # ML head IS invoked for shadow capture.
        assert ml.predict_calls == 1

    def test_shadow_without_ml_head_raises(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=FakeLM(),
            config=SwitchConfig(auto_record=False, phase=Phase.ML_SHADOW),
        )
        with pytest.raises(ValueError, match="ml"):
            s.classify({"title": "x"})

    def test_shadow_ml_failure_does_not_block_decision(self):
        class BrokenML:
            def fit(self, records): ...
            def predict(self, input, labels):
                raise RuntimeError("model load failed")

            def model_version(self):
                return "broken"

        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=FakeLM(label="bug", confidence=0.9),
            ml_head=BrokenML(),
            config=SwitchConfig(auto_record=False, phase=Phase.ML_SHADOW),
        )
        r = s.classify({"title": "App keeps crashing"})
        assert r.label == "bug"
        assert r.source == "model"


class TestMLShadowOutcomeCapture:
    def test_ml_fields_populated(self):
        ml = FakeMLHead(label="feature_request", confidence=0.6)
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=FakeLM(label="bug", confidence=0.9),
            ml_head=ml,
            config=SwitchConfig(auto_record=False, phase=Phase.ML_SHADOW),
        )
        r = s.classify({"title": "App keeps crashing"})
        r.mark_correct()
        recs = s.storage.load_records("triage")
        assert len(recs) == 1
        assert recs[0].ml_output == "feature_request"
        assert recs[0].ml_confidence == pytest.approx(0.6)
        # rule and model shadows still captured for side-by-side analysis.
        assert recs[0].rule_output == "bug"
        assert recs[0].model_output == "bug"


class TestMLShadowStatus:
    def test_status_reports_ml_agreement_rate(self):
        ml = FakeMLHead(label="bug", confidence=0.7)  # agrees with primary
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=FakeLM(label="bug", confidence=0.95),
            ml_head=ml,
            config=SwitchConfig(auto_record=False, phase=Phase.ML_SHADOW),
        )
        for t in ("crash a", "crash b", "crash c"):
            r = s.classify({"title": t})
            r.mark_correct()
        st = s.status()
        assert st.ml_agreement_rate == pytest.approx(1.0)
        assert st.model_version == "fake-0"
