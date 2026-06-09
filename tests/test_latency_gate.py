# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for LatencyGate — the third graduation axis (issue #131).

A real-time stream (voice/video/mobile) has a hard per-call latency budget that
a cloud LLM violates. LatencyGate makes that budget a first-class gate
constraint: it refuses advancement to a tier whose measured per-call latency
exceeds the SLO, and composes with the statistical gate so a switch graduates
only when the target is BOTH reliably better AND fast enough.
"""

from __future__ import annotations

import pytest

from postrule.core import Phase
from postrule.gates import (
    CompositeGate,
    GateDecision,
    LatencyGate,
)


class _AlwaysAdvance:
    """Stub statistical gate that always says the target is better."""

    def evaluate(self, records, current_phase, target_phase, /):  # noqa: ARG002
        return GateDecision(target_better=True, rationale="stub: always advance")


PROFILE = {
    Phase.RULE: 0.003,
    Phase.MODEL_PRIMARY: 530.0,  # LLM
    Phase.ML_WITH_FALLBACK: 0.1,
    Phase.ML_PRIMARY: 0.1,
}


def test_refuses_target_over_slo():
    gate = LatencyGate(PROFILE, slo_ms=50.0)
    d = gate.evaluate([], Phase.RULE, Phase.MODEL_PRIMARY)
    assert not d.target_better
    assert "530" in d.rationale or "SLO" in d.rationale


def test_allows_target_within_slo():
    gate = LatencyGate(PROFILE, slo_ms=50.0)
    d = gate.evaluate([], Phase.MODEL_PRIMARY, Phase.ML_WITH_FALLBACK)
    assert d.target_better


def test_unknown_target_latency_is_unconstrained():
    # A tier with no measured latency cannot be proven to breach the budget,
    # so it is allowed (matches oracle_terminal's per_call_ms=None behavior).
    gate = LatencyGate({Phase.RULE: 0.003}, slo_ms=50.0)
    d = gate.evaluate([], Phase.RULE, Phase.MODEL_PRIMARY)
    assert d.target_better
    assert "no measured latency" in d.rationale.lower()


def test_rejects_invalid_slo():
    with pytest.raises(ValueError, match="slo_ms"):
        LatencyGate(PROFILE, slo_ms=0.0)
    with pytest.raises(ValueError, match="slo_ms"):
        LatencyGate(PROFILE, slo_ms=-5.0)


def test_exposes_config():
    gate = LatencyGate(PROFILE, slo_ms=50.0)
    assert gate.slo_ms == 50.0
    assert gate.tier_latency_ms[Phase.MODEL_PRIMARY] == 530.0


def test_composes_with_statistical_gate_all_of():
    # Even when the statistical gate says "advance", a latency breach vetoes it.
    always_yes = _AlwaysAdvance()
    latency = LatencyGate(PROFILE, slo_ms=50.0)
    composite = CompositeGate.all_of([always_yes, latency])

    # LLM target breaches the 50ms budget -> composite refuses.
    breach = composite.evaluate([], Phase.RULE, Phase.MODEL_PRIMARY)
    assert not breach.target_better

    # Local ML target is within budget -> composite advances.
    ok = composite.evaluate([], Phase.MODEL_PRIMARY, Phase.ML_WITH_FALLBACK)
    assert ok.target_better


def test_returns_gate_decision_type():
    gate = LatencyGate(PROFILE, slo_ms=50.0)
    d = gate.evaluate([], Phase.RULE, Phase.ML_PRIMARY)
    assert isinstance(d, GateDecision)


def test_switchconfig_declares_latency_budget():
    from postrule.core import SwitchConfig

    assert SwitchConfig().latency_slo_ms is None  # default unconstrained
    assert SwitchConfig(latency_slo_ms=50.0).latency_slo_ms == 50.0
    with pytest.raises(ValueError, match="latency_slo_ms"):
        SwitchConfig(latency_slo_ms=0.0)
    with pytest.raises(ValueError, match="latency_slo_ms"):
        SwitchConfig(latency_slo_ms=-1.0)
