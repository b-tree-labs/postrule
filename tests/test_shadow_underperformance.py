# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for downward (shadow-underperformance) detection — issue #133, core.

The upward McNemar gate says when a shadow is good enough to graduate. This is
the missing other direction: detect when a MODEL (LLM) shadow perennially loses
to the rule, so the operator can stop burning tokens on a shadow that will never
graduate (the isValidJSON-shaped switch). This module ships the statistical
detector only; the SHADOW_AUTO_DISABLED phase + SDK short-circuit + CLI/dashboard
are deferred follow-ups that need a design decision.
"""

from __future__ import annotations

from postrule.core import ClassificationRecord, Verdict
from postrule.gates import ShadowAssessment, ShadowUnderperformanceGate


def _rec(rule_out, model_out, label):
    return ClassificationRecord(
        timestamp=0.0,
        input=None,
        label=label,
        outcome=Verdict.CORRECT.value,
        source="rule",
        confidence=1.0,
        rule_output=rule_out,
        model_output=model_out,
    )


def _records(rule_right, model_right, both_right=0, both_wrong=0):
    """Build records: rule_right = rule correct & model wrong, etc. label='a'."""
    recs = []
    recs += [_rec("a", "b", "a") for _ in range(rule_right)]  # rule wins
    recs += [_rec("b", "a", "a") for _ in range(model_right)]  # model wins
    recs += [_rec("a", "a", "a") for _ in range(both_right)]  # concordant
    recs += [_rec("b", "b", "a") for _ in range(both_wrong)]  # concordant wrong
    return recs


def test_recommends_disable_when_rule_dominates():
    # Rule wins 40 disagreements, model wins 2 — strongly rule-favoring.
    recs = _records(rule_right=40, model_right=2, both_right=160)
    gate = ShadowUnderperformanceGate(alpha=0.05, min_paired=50)
    a = gate.assess(recs)
    assert isinstance(a, ShadowAssessment)
    assert a.recommend_disable
    assert a.p_value is not None and a.p_value < 0.05
    assert a.rule_accuracy > a.model_accuracy
    assert a.paired_sample_size == 202


def test_keeps_shadow_when_model_competitive():
    # Balanced disagreements — no significant rule dominance.
    recs = _records(rule_right=20, model_right=22, both_right=160)
    gate = ShadowUnderperformanceGate(alpha=0.05, min_paired=50)
    a = gate.assess(recs)
    assert not a.recommend_disable


def test_keeps_shadow_when_model_wins():
    # Model dominates — definitely do not disable.
    recs = _records(rule_right=2, model_right=40, both_right=160)
    a = ShadowUnderperformanceGate(alpha=0.05, min_paired=50).assess(recs)
    assert not a.recommend_disable
    assert a.model_accuracy > a.rule_accuracy


def test_holds_off_below_warmup_window():
    recs = _records(rule_right=15, model_right=0, both_right=20)
    a = ShadowUnderperformanceGate(alpha=0.05, min_paired=200).assess(recs)
    assert not a.recommend_disable
    assert "warm-up" in a.rationale.lower() or "insufficient" in a.rationale.lower()


def test_estimates_tokens_saved_when_disabling():
    recs = _records(rule_right=40, model_right=2, both_right=160)
    gate = ShadowUnderperformanceGate(alpha=0.05, min_paired=50)
    a = gate.assess(recs, shadow_cost_per_call_usd=0.0006)
    # Per-call shadow cost surfaced so the operator sees forward savings.
    assert a.shadow_cost_per_call_usd == 0.0006


def test_rejects_invalid_alpha():
    import pytest

    with pytest.raises(ValueError, match="alpha"):
        ShadowUnderperformanceGate(alpha=0.0)
    with pytest.raises(ValueError, match="alpha"):
        ShadowUnderperformanceGate(alpha=1.0)


def test_custom_output_fields():
    # Compare rule vs ML head instead of rule vs model, via field override.
    recs = [
        ClassificationRecord(
            timestamp=0.0,
            input=None,
            label="a",
            outcome=Verdict.CORRECT.value,
            source="rule",
            confidence=1.0,
            rule_output="a",
            ml_output="b",
        )
        for _ in range(60)
    ]
    gate = ShadowUnderperformanceGate(alpha=0.05, min_paired=50, shadow_field="ml_output")
    a = gate.assess(recs)
    assert a.recommend_disable  # rule beats the ML shadow every time


# --- Restart detection: changed conditions revive a disabled shadow (#133) --

from postrule.gates import ShadowRestartAssessment  # noqa: E402


def test_restart_when_rule_changed():
    # A rule edit invalidates the prior rule-vs-shadow comparison -> restart,
    # regardless of recent accuracy (even with no recent records).
    gate = ShadowUnderperformanceGate()
    a = gate.should_restart(
        [],
        rule_fingerprint_at_disable="hashA",
        rule_fingerprint_now="hashB",
        rule_accuracy_at_disable=0.98,
    )
    assert isinstance(a, ShadowRestartAssessment)
    assert a.recommend_restart
    assert a.rule_changed
    assert "rule" in a.rationale.lower()


def test_no_restart_when_rule_stable_and_accuracy_holds():
    gate = ShadowUnderperformanceGate(min_paired=50)
    recent = _records(rule_right=2, model_right=2, both_right=196)  # rule acc ~0.99
    a = gate.should_restart(
        recent,
        rule_fingerprint_at_disable="hashA",
        rule_fingerprint_now="hashA",
        rule_accuracy_at_disable=0.98,
    )
    assert not a.recommend_restart
    assert not a.rule_changed
    assert not a.drift_detected


def test_restart_when_rule_accuracy_drifts_down():
    # The rule was disabled-against at 0.98; recent traffic shows it at ~0.70,
    # so it is no longer near the ceiling -> the shadow deserves another look.
    gate = ShadowUnderperformanceGate(min_paired=50)
    recent = _records(rule_right=10, model_right=60, both_right=130)  # rule acc 0.70
    a = gate.should_restart(
        recent,
        rule_fingerprint_at_disable="hashA",
        rule_fingerprint_now="hashA",
        rule_accuracy_at_disable=0.98,
        drift_drop=0.05,
    )
    assert a.recommend_restart
    assert a.drift_detected
    assert a.rule_accuracy_recent is not None and a.rule_accuracy_recent < 0.8
    assert "drift" in a.rationale.lower()


def test_no_restart_below_recent_window_when_rule_unchanged():
    # Too few recent records to call drift, and the rule is unchanged -> hold.
    gate = ShadowUnderperformanceGate(min_paired=200)
    recent = _records(rule_right=5, model_right=10, both_right=5)  # n=20 < 200
    a = gate.should_restart(
        recent,
        rule_fingerprint_at_disable="hashA",
        rule_fingerprint_now="hashA",
        rule_accuracy_at_disable=0.98,
    )
    assert not a.recommend_restart


def test_restart_drift_needs_baseline():
    # Without a recorded at-disable accuracy, drift can't be judged; rule
    # unchanged -> no restart.
    gate = ShadowUnderperformanceGate(min_paired=50)
    recent = _records(rule_right=10, model_right=60, both_right=130)
    a = gate.should_restart(
        recent,
        rule_fingerprint_at_disable="hashA",
        rule_fingerprint_now="hashA",
        rule_accuracy_at_disable=None,
    )
    assert not a.recommend_restart
