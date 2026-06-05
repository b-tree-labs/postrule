# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for CostToleranceGate — the non-inferiority ("close-enough") gate.

The gate graduates to a cheaper-to-operate target tier when the target is
within ``margin`` *below* the current tier (delta >= -margin), rather than
requiring strict improvement. This is the product knob for trading a small,
bounded accuracy give-up for shedding the LLM's perpetual per-call cost.
"""

from __future__ import annotations

from postrule.core import ClassificationRecord, Phase
from postrule.gates import CostToleranceGate


def _records(n: int, current_acc: float, target_acc: float) -> list[ClassificationRecord]:
    """All correct-outcome rows; rule_output (current) and model_output (target)
    match the label at the requested per-source accuracies (deterministic)."""
    recs: list[ClassificationRecord] = []
    n_cur = round(current_acc * n)
    n_tgt = round(target_acc * n)
    for i in range(n):
        recs.append(
            ClassificationRecord(
                timestamp=1_700_000_000.0 + i,
                input="x",
                label="a",
                outcome="correct",
                source="rule",
                confidence=1.0,
                rule_output="a" if i < n_cur else "b",  # current correct on first n_cur
                model_output="a" if i < n_tgt else "b",  # target correct on first n_tgt
                model_confidence=0.9,
            )
        )
    return recs


def test_advances_when_target_within_margin():
    # target 3% worse than current; margin 5% -> graduate (non-inferior)
    recs = _records(400, current_acc=0.80, target_acc=0.77)
    decision = CostToleranceGate(margin=0.05).evaluate(recs, Phase.RULE, Phase.MODEL_PRIMARY)
    assert decision.target_better is True


def test_refuses_when_target_beyond_margin():
    # target 3% worse; margin 2% -> meaningfully worse, refuse
    recs = _records(400, current_acc=0.80, target_acc=0.77)
    decision = CostToleranceGate(margin=0.02).evaluate(recs, Phase.RULE, Phase.MODEL_PRIMARY)
    assert decision.target_better is False


def test_advances_when_target_strictly_better():
    recs = _records(400, current_acc=0.75, target_acc=0.82)
    decision = CostToleranceGate(margin=0.02).evaluate(recs, Phase.RULE, Phase.MODEL_PRIMARY)
    assert decision.target_better is True


def test_zero_margin_requires_tie_or_better():
    recs_tie = _records(400, current_acc=0.80, target_acc=0.80)
    assert (
        CostToleranceGate(margin=0.0)
        .evaluate(recs_tie, Phase.RULE, Phase.MODEL_PRIMARY)
        .target_better
        is True
    )
    recs_worse = _records(400, current_acc=0.80, target_acc=0.7975)
    assert (
        CostToleranceGate(margin=0.0)
        .evaluate(recs_worse, Phase.RULE, Phase.MODEL_PRIMARY)
        .target_better
        is False
    )


def test_refuses_below_min_paired():
    recs = _records(50, current_acc=0.80, target_acc=0.80)
    decision = CostToleranceGate(margin=0.05, min_paired=200).evaluate(
        recs, Phase.RULE, Phase.MODEL_PRIMARY
    )
    assert decision.target_better is False
    assert "insufficient paired samples" in decision.rationale


def test_rejects_invalid_margin():
    import pytest

    with pytest.raises(ValueError, match="margin"):
        CostToleranceGate(margin=1.0)
    with pytest.raises(ValueError, match="margin"):
        CostToleranceGate(margin=-0.01)


def test_rejects_invalid_min_paired():
    import pytest

    with pytest.raises(ValueError, match="min_paired"):
        CostToleranceGate(min_paired=0)


def test_exposes_config_via_properties():
    gate = CostToleranceGate(margin=0.03, min_paired=150)
    assert gate.margin == 0.03
    assert gate.min_paired == 150
