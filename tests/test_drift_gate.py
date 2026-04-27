# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""DriftGate: symmetric counterpart of McNemarGate for autonomous demotion.

Phase A tests cover the gate's evaluate() contract in isolation.
LearnedSwitch.demote() and the auto-demote loop are tested in
test_drift_demote.py and test_drift_auto.py respectively.
"""

from __future__ import annotations

import time

import pytest

from dendra import (
    ClassificationRecord,
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
    """Build a set of correct-outcome records with controlled paired correctness.

    - rule_right: count where rule_output == label, ml_output != label
    - current_right: count where ml_output == label, rule_output != label
    - both_right: count where both predictions == label
    - n: total target count; padding (both wrong) fills the rest
    """
    records = []
    for _ in range(rule_right):
        records.append(
            _rec(label="A", rule_output="A", ml_output="B")
        )
    for _ in range(current_right):
        records.append(
            _rec(label="A", rule_output="B", ml_output="A")
        )
    for _ in range(both_right):
        records.append(
            _rec(label="A", rule_output="A", ml_output="A")
        )
    while len(records) < n:
        records.append(
            _rec(label="A", rule_output="B", ml_output="C")
        )
    return records


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestDriftGateConstruction:
    def test_default_alpha_and_min_paired(self):
        from dendra import DriftGate

        gate = DriftGate()
        assert gate.alpha == 0.01
        assert gate.min_paired == 200

    def test_custom_alpha(self):
        from dendra import DriftGate

        gate = DriftGate(alpha=0.05)
        assert gate.alpha == 0.05

    def test_custom_min_paired(self):
        from dendra import DriftGate

        gate = DriftGate(min_paired=100)
        assert gate.min_paired == 100

    def test_alpha_bounds(self):
        from dendra import DriftGate

        with pytest.raises(ValueError):
            DriftGate(alpha=0.0)
        with pytest.raises(ValueError):
            DriftGate(alpha=1.0)
        with pytest.raises(ValueError):
            DriftGate(alpha=-0.1)

    def test_min_paired_must_be_positive(self):
        from dendra import DriftGate

        with pytest.raises(ValueError):
            DriftGate(min_paired=0)
        with pytest.raises(ValueError):
            DriftGate(min_paired=-1)


# ---------------------------------------------------------------------------
# Evaluation contract
# ---------------------------------------------------------------------------


class TestDriftGateInsufficientData:
    def test_below_min_paired_holds(self):
        from dendra import DriftGate

        gate = DriftGate(min_paired=100)
        records = _drift_records(rule_right=10, current_right=2, both_right=0, n=20)
        decision = gate.evaluate(records, Phase.ML_PRIMARY, Phase.ML_WITH_FALLBACK)

        assert decision.advance is False
        assert "insufficient" in decision.rationale.lower()

    def test_zero_records_holds(self):
        from dendra import DriftGate

        gate = DriftGate()
        decision = gate.evaluate([], Phase.ML_PRIMARY, Phase.ML_WITH_FALLBACK)

        assert decision.advance is False


class TestDriftGateDetectsDrift:
    """When the rule reliably beats the current phase's decision-maker,
    the gate fires demotion (advance=True meaning "yes, demote to target")."""

    def test_rule_dominates_ml_at_p5_demotes(self):
        from dendra import DriftGate

        # 90 rows where rule was right + ML wrong; 5 where ML was right
        # + rule wrong; 5 padding. n_min_paired clears at 95.
        records = _drift_records(
            rule_right=90,
            current_right=5,
            both_right=5,
            n=200,
        )
        gate = DriftGate(alpha=0.01, min_paired=50)
        decision = gate.evaluate(records, Phase.ML_PRIMARY, Phase.ML_WITH_FALLBACK)

        assert decision.advance is True
        assert decision.p_value is not None
        assert decision.p_value < 0.01

    def test_rule_dominates_ml_at_p4_demotes(self):
        from dendra import DriftGate

        records = _drift_records(
            rule_right=80,
            current_right=4,
            both_right=10,
            n=200,
        )
        gate = DriftGate(alpha=0.01, min_paired=50)
        decision = gate.evaluate(records, Phase.ML_WITH_FALLBACK, Phase.ML_SHADOW)

        assert decision.advance is True


class TestDriftGateNoDrift:
    """When the current phase is still winning or tied, the gate holds."""

    def test_current_still_beats_rule_holds(self):
        from dendra import DriftGate

        # Inverse of the drift case: ML right, rule wrong on 90 rows.
        records = _drift_records(
            rule_right=5,
            current_right=90,
            both_right=5,
            n=200,
        )
        gate = DriftGate(alpha=0.01, min_paired=50)
        decision = gate.evaluate(records, Phase.ML_PRIMARY, Phase.ML_WITH_FALLBACK)

        assert decision.advance is False

    def test_tie_holds(self):
        from dendra import DriftGate

        records = _drift_records(
            rule_right=20,
            current_right=20,
            both_right=60,
            n=200,
        )
        gate = DriftGate(alpha=0.01, min_paired=50)
        decision = gate.evaluate(records, Phase.ML_PRIMARY, Phase.ML_WITH_FALLBACK)

        assert decision.advance is False


class TestDriftGatePerPhaseSource:
    """The gate uses the right field per current_phase: model_output for
    MODEL_PRIMARY, ml_output for ML_WITH_FALLBACK / ML_PRIMARY."""

    def test_uses_model_output_at_model_primary(self):
        from dendra import DriftGate

        # Build records where rule beats model (note: model_output, not
        # ml_output). DriftGate at MODEL_PRIMARY should compare rule vs
        # model and demote when rule wins.
        records = []
        for _ in range(90):
            records.append(
                _rec(
                    label="A",
                    rule_output="A",
                    model_output="B",
                )
            )
        for _ in range(5):
            records.append(
                _rec(
                    label="A",
                    rule_output="B",
                    model_output="A",
                )
            )
        while len(records) < 200:
            records.append(
                _rec(label="A", rule_output="B", model_output="C")
            )

        gate = DriftGate(alpha=0.01, min_paired=50)
        decision = gate.evaluate(records, Phase.MODEL_PRIMARY, Phase.MODEL_SHADOW)

        assert decision.advance is True

    def test_no_demote_at_rule_phase(self):
        """At Phase.RULE the rule IS the current decision-maker; the
        DriftGate has nothing to compare against and must not demote."""
        from dendra import DriftGate

        records = _drift_records(rule_right=50, current_right=50, both_right=100, n=200)
        gate = DriftGate(alpha=0.01, min_paired=50)
        # No prev phase below RULE; demotion target is None. Gate
        # returns advance=False with a "no demotion target" rationale.
        decision = gate.evaluate(records, Phase.RULE, Phase.RULE)

        assert decision.advance is False
