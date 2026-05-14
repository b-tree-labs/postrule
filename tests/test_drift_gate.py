# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Drift detection via direction-agnostic Gate.

There is no separate "DriftGate" or "DemotionGate" type. Drift detection
is the same Gate machinery used in the demotion direction: pass
``target_phase=Phase.RULE`` so the test compares the current decision-
maker against the rule. A ``target_better=True`` decision means "the
rule is the better target" — which the caller (LearnedSwitch.demote)
interprets as "demote one phase."

These tests pin the comparator semantics for the drift use-case so we
catch regressions in the underlying paired-correctness machinery.
LearnedSwitch.demote() and the auto-demote loop are tested in
test_drift_demote.py and test_drift_auto.py respectively.
"""

from __future__ import annotations

import time

from postrule import (
    AccuracyMarginGate,
    ClassificationRecord,
    McNemarGate,
    Phase,
    Verdict,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _rec(
    *,
    label: str,
    outcome: str = Verdict.CORRECT.value,
    source: str = "ml",
    rule_output: str | None = None,
    model_output: str | None = None,
    ml_output: str | None = None,
) -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=time.time(),
        input={"x": 1},
        label=label,
        outcome=outcome,
        source=source,
        confidence=1.0,
        rule_output=rule_output,
        model_output=model_output,
        ml_output=ml_output,
    )


def _drift_records(rule_right: int, current_right: int, both_right: int, n: int) -> list:
    """Build a set of correct-outcome records with controlled paired correctness."""
    records = []
    for _ in range(rule_right):
        records.append(_rec(label="A", rule_output="A", ml_output="B"))
    for _ in range(current_right):
        records.append(_rec(label="A", rule_output="B", ml_output="A"))
    for _ in range(both_right):
        records.append(_rec(label="A", rule_output="A", ml_output="A"))
    while len(records) < n:
        records.append(_rec(label="A", rule_output="B", ml_output="C"))
    return records


# ---------------------------------------------------------------------------
# McNemar in the demotion direction
# ---------------------------------------------------------------------------


class TestMcNemarDemotionDirection:
    """McNemarGate.evaluate(records, current=ML_PRIMARY, target=Phase.RULE)
    answers 'is the rule reliably better than ML on these records?'.
    target_better=True ⇒ 'yes, the rule is the winner' ⇒ caller demotes.
    """

    def test_rule_dominates_ml_at_p5_target_better_true(self):
        records = _drift_records(rule_right=90, current_right=5, both_right=5, n=200)
        gate = McNemarGate(alpha=0.01, min_paired=50)
        decision = gate.evaluate(records, Phase.ML_PRIMARY, Phase.RULE)

        assert decision.target_better is True
        assert decision.p_value is not None
        assert decision.p_value < 0.01

    def test_rule_dominates_model_at_p2_target_better_true(self):
        records = []
        for _ in range(90):
            records.append(_rec(label="A", rule_output="A", model_output="B"))
        for _ in range(5):
            records.append(_rec(label="A", rule_output="B", model_output="A"))
        while len(records) < 200:
            records.append(_rec(label="A", rule_output="B", model_output="C"))

        gate = McNemarGate(alpha=0.01, min_paired=50)
        decision = gate.evaluate(records, Phase.MODEL_PRIMARY, Phase.RULE)

        assert decision.target_better is True

    def test_current_still_beats_rule_target_better_false(self):
        records = _drift_records(rule_right=5, current_right=90, both_right=5, n=200)
        gate = McNemarGate(alpha=0.01, min_paired=50)
        decision = gate.evaluate(records, Phase.ML_PRIMARY, Phase.RULE)

        assert decision.target_better is False

    def test_tie_target_better_false(self):
        records = _drift_records(rule_right=20, current_right=20, both_right=60, n=200)
        gate = McNemarGate(alpha=0.01, min_paired=50)
        decision = gate.evaluate(records, Phase.ML_PRIMARY, Phase.RULE)

        assert decision.target_better is False

    def test_below_min_paired_target_better_false(self):
        records = _drift_records(rule_right=10, current_right=2, both_right=0, n=20)
        gate = McNemarGate(alpha=0.01, min_paired=100)
        decision = gate.evaluate(records, Phase.ML_PRIMARY, Phase.RULE)

        assert decision.target_better is False
        assert "insufficient" in decision.rationale.lower()


# ---------------------------------------------------------------------------
# AccuracyMargin in the demotion direction (any AdvanceGate works)
# ---------------------------------------------------------------------------


class TestAccuracyMarginDemotionDirection:
    """The direction-agnostic Gate protocol means any concrete gate can
    drive drift detection — not just McNemar."""

    def test_margin_exceeded_target_better_true(self):
        records = _drift_records(rule_right=80, current_right=10, both_right=10, n=200)
        gate = AccuracyMarginGate(margin=0.10, min_paired=50)
        decision = gate.evaluate(records, Phase.ML_PRIMARY, Phase.RULE)

        assert decision.target_better is True

    def test_margin_not_exceeded_target_better_false(self):
        # Rule barely ahead; under the 0.10 margin.
        records = _drift_records(rule_right=15, current_right=10, both_right=70, n=200)
        gate = AccuracyMarginGate(margin=0.10, min_paired=50)
        decision = gate.evaluate(records, Phase.ML_PRIMARY, Phase.RULE)

        assert decision.target_better is False
