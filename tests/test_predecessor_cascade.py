# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Locks in the recursive predecessor-cascade rule across phases.

The lifecycle's intended semantics (paper §3.1) are:

    Each phase's low-confidence fallback is its predecessor's routing,
    recursively.

Concretely:
    P0: R
    P1: R               (M shadow only)
    P2: M else R
    P3: M else R        (H shadow only)
    P4: H else (M else R)
    P5: H               (no confidence fallback; circuit breaker only)

This test asserts the full cascade depth at each gated phase. Drift
between paper and code must break this test before reaching main.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from dendra import (
    LearnedSwitch,
    MLPrediction,
    ModelPrediction,
    Phase,
    SwitchConfig,
)


@dataclass
class _Mod:
    label: str
    confidence: float
    classify_calls: int = 0

    def classify(self, input, labels):
        self.classify_calls += 1
        return ModelPrediction(label=self.label, confidence=self.confidence)


@dataclass
class _ML:
    label: str
    confidence: float
    predict_calls: int = 0

    def fit(self, records): ...

    def predict(self, input, labels):
        self.predict_calls += 1
        return MLPrediction(label=self.label, confidence=self.confidence)

    def model_version(self):
        return "v1"


def _rule(_):
    return "R_LABEL"


THRESHOLD = 0.85


class TestPredecessorCascadeAtP4:
    """P4 is the load-bearing case: H must cascade through M to R."""

    def _switch(self, ml_conf: float, m_conf: float):
        return LearnedSwitch(
            name="cascade",
            rule=_rule,
            author="t",
            ml_head=_ML(label="H_LABEL", confidence=ml_conf),
            model=_Mod(label="M_LABEL", confidence=m_conf),
            config=SwitchConfig(
                phase=Phase.ML_WITH_FALLBACK,
                confidence_threshold=THRESHOLD,
            ),
        )

    def test_h_taken_when_h_confident(self):
        r = self._switch(ml_conf=0.95, m_conf=0.95).classify("x")
        assert r.label == "H_LABEL"
        assert r.source == "ml"

    def test_m_taken_when_h_low_m_confident(self):
        r = self._switch(ml_conf=0.30, m_conf=0.95).classify("x")
        assert r.label == "M_LABEL"
        assert r.source == "model"

    def test_r_taken_when_h_low_m_low(self):
        r = self._switch(ml_conf=0.30, m_conf=0.30).classify("x")
        assert r.label == "R_LABEL"
        assert r.source == "rule_fallback"

    def test_m_skipped_when_h_confident(self):
        # When H is taken, M must NOT be called (latency contract: H is
        # microseconds, M is an LLM call).
        m = _Mod(label="M_LABEL", confidence=0.95)
        s = LearnedSwitch(
            name="cascade",
            rule=_rule,
            author="t",
            ml_head=_ML(label="H_LABEL", confidence=0.95),
            model=m,
            config=SwitchConfig(
                phase=Phase.ML_WITH_FALLBACK,
                confidence_threshold=THRESHOLD,
            ),
        )
        s.classify("x")
        assert m.classify_calls == 0


class TestPredecessorCascadeAtP2:
    """P2 cascade was already correct; this nails it down so the recursive
    rule is exercised at every gated phase, not just P4."""

    def _switch(self, m_conf: float):
        return LearnedSwitch(
            name="p2",
            rule=_rule,
            author="t",
            model=_Mod(label="M_LABEL", confidence=m_conf),
            config=SwitchConfig(
                phase=Phase.MODEL_PRIMARY,
                confidence_threshold=THRESHOLD,
            ),
        )

    def test_m_taken_when_m_confident(self):
        r = self._switch(m_conf=0.95).classify("x")
        assert r.label == "M_LABEL"
        assert r.source == "model"

    def test_r_taken_when_m_low(self):
        r = self._switch(m_conf=0.30).classify("x")
        assert r.label == "R_LABEL"
        assert r.source == "rule_fallback"


class TestCascadeCollapsesGracefullyWithoutModel:
    """When M is absent, the cascade collapses to H -> R, identical to v1.0
    pre-cascade behavior. No surprises for installs that retire M."""

    def test_h_low_no_model_falls_to_r(self):
        s = LearnedSwitch(
            name="cascade",
            rule=_rule,
            author="t",
            ml_head=_ML(label="H_LABEL", confidence=0.30),
            config=SwitchConfig(
                phase=Phase.ML_WITH_FALLBACK,
                confidence_threshold=THRESHOLD,
            ),
        )
        r = s.classify("x")
        assert r.label == "R_LABEL"
        assert r.source == "rule_fallback"
