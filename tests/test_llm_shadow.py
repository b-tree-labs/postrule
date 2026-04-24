# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for Phase 1 (MODEL_SHADOW) — rule decides, LLM predicts alongside."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from dendra import (
    ClassificationRecord,
    ClassificationResult,
    InMemoryStorage,
    LearnedSwitch,
    ModelClassifier,
    ModelPrediction,
    Phase,
    SwitchConfig,
    Verdict,
    ml_switch,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeLLM:
    """Deterministic ModelClassifier for tests — returns the label it's told to."""

    label: str = "bug"
    confidence: float = 0.91
    calls: int = 0

    def classify(self, input, labels):  # matches ModelClassifier protocol
        self.calls += 1
        return ModelPrediction(label=self.label, confidence=self.confidence)


def _rule(ticket: dict) -> str:
    if "crash" in (ticket.get("title", "") or "").lower():
        return "bug"
    return "feature_request"


# ---------------------------------------------------------------------------
# Phase enum (6-phase lifecycle per paper outline §3.1)
# ---------------------------------------------------------------------------


class TestPhaseEnum:
    def test_six_phases_present(self):
        names = {p.name for p in Phase}
        assert names == {
            "RULE",
            "MODEL_SHADOW",
            "MODEL_PRIMARY",
            "ML_SHADOW",
            "ML_WITH_FALLBACK",
            "ML_PRIMARY",
        }

    def test_phase_ordering_rule_is_first(self):
        # Ordering matters for transition logic; RULE must be the floor.
        assert list(Phase)[0] is Phase.RULE


# ---------------------------------------------------------------------------
# ModelClassifier protocol
# ---------------------------------------------------------------------------


class TestLLMClassifierProtocol:
    def test_fake_llm_satisfies_protocol(self):
        f = FakeLLM()
        assert isinstance(f, ModelClassifier)

    def test_prediction_has_label_and_confidence(self):
        p = ModelPrediction(label="bug", confidence=0.9)
        assert p.label == "bug"
        assert 0.0 <= p.confidence <= 1.0


# ---------------------------------------------------------------------------
# Phase 1 classify() — rule still decides, LLM runs in shadow
# ---------------------------------------------------------------------------


class TestPhase1LLMShadow:
    def test_classify_decision_comes_from_rule(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=FakeLLM(label="feature_request", confidence=0.95),
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_SHADOW),
        )
        result = s.classify({"title": "App keeps crashing"})
        # Rule says "bug"; LLM disagrees with "feature_request".
        # In shadow, the rule's verdict is what the caller sees.
        assert result.label == "bug"
        assert result.source == "rule"
        assert result.phase is Phase.MODEL_SHADOW

    def test_llm_is_invoked_in_shadow(self):
        llm = FakeLLM(label="bug", confidence=0.8)
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=llm,
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_SHADOW),
        )
        s.classify({"title": "App keeps crashing"})
        assert llm.calls == 1

    def test_shadow_without_llm_raises(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_SHADOW),
        )
        with pytest.raises(ValueError, match="model"):
            s.classify({"title": "App keeps crashing"})

    def test_rule_phase_ignores_missing_llm(self):
        # Phase 0 should still work without an LLM configured.
        s = LearnedSwitch(name="triage", rule=_rule, author="alice")
        r = s.classify({"title": "App keeps crashing"})
        assert r.source == "rule"
        assert r.phase is Phase.RULE

    def test_llm_failure_does_not_block_rule_decision(self):
        class BrokenLLM:
            def classify(self, input, labels):
                raise RuntimeError("provider unavailable")

        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=BrokenLLM(),
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_SHADOW),
        )
        # Shadow failure must NOT break the user's decision. Rule still wins.
        result = s.classify({"title": "App keeps crashing"})
        assert result.label == "bug"
        assert result.source == "rule"


# ---------------------------------------------------------------------------
# ClassificationRecord extension
# ---------------------------------------------------------------------------


class TestOutcomeRecordLLMFields:
    def test_llm_fields_default_to_none(self):
        r = ClassificationRecord(
            timestamp=1.0,
            input={"x": 1},
            label="bug",
            outcome=Verdict.CORRECT.value,
            source="rule",
            confidence=1.0,
        )
        assert r.model_output is None
        assert r.model_confidence is None

    def test_llm_fields_populate_on_shadow_record(self):
        store = InMemoryStorage()
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=FakeLLM(label="feature_request", confidence=0.77),
            storage=store,
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_SHADOW),
        )
        s.classify({"title": "App keeps crashing"})
        s.record_verdict(
            input={"title": "App keeps crashing"},
            label="bug",
            outcome=Verdict.CORRECT.value,
        )
        records = store.load_records("triage")
        # Phase 1 auto-captures the LLM prediction alongside each classify()
        # so the next record carries the shadow observation.
        assert len(records) == 1
        assert records[0].model_output == "feature_request"
        assert records[0].model_confidence == pytest.approx(0.77)


# ---------------------------------------------------------------------------
# SwitchStatus — shadow agreement rate
# ---------------------------------------------------------------------------


class TestShadowAgreementRate:
    def test_reports_agreement_rate_in_shadow(self):
        llm = FakeLLM(label="bug", confidence=0.9)  # agrees with rule
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=llm,
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_SHADOW),
        )
        for title in ("crash 1", "crash 2", "crash 3"):
            s.classify({"title": title})
            s.record_verdict(
                input={"title": title},
                label="bug",
                outcome=Verdict.CORRECT.value,
            )
        st = s.status()
        # All three LLM predictions matched the rule → 100% agreement.
        assert st.phase is Phase.MODEL_SHADOW
        assert st.shadow_agreement_rate == pytest.approx(1.0)

    def test_disagreement_lowers_rate(self):
        llm = FakeLLM(label="feature_request", confidence=0.6)
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            model=llm,
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_SHADOW),
        )
        # Rule returns "bug" (crash keyword); LLM returns "feature_request".
        for title in ("crash 1", "crash 2"):
            s.classify({"title": title})
            s.record_verdict(
                input={"title": title},
                label="bug",
                outcome=Verdict.CORRECT.value,
            )
        st = s.status()
        assert st.shadow_agreement_rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# @ml_switch decorator with model= kwarg
# ---------------------------------------------------------------------------


class TestDecoratorLLMKwarg:
    def test_decorator_accepts_llm(self):
        @ml_switch(
            labels=["bug", "feature_request"],
            author="alice",
            model=FakeLLM(label="bug", confidence=0.9),
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_SHADOW),
        )
        def triage(ticket):
            return _rule(ticket)

        result = triage.switch.classify({"title": "App keeps crashing"})
        assert isinstance(result, ClassificationResult)
        assert result.source == "rule"
        assert triage.switch.phase() is Phase.MODEL_SHADOW
