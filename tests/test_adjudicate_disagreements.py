# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# Independent adjudication of shadow disagreements — the fix for the
# incumbent-bias trap where a verifier-equipped switch could NEVER graduate
# no matter how much better the challenger was, because the gate only scored a
# shadow on records where the decision was already correct.
#
# Modality-agnostic by construction (plain text inputs): the bug and fix are
# in the gate's evidence model, not anything modality-specific.

from __future__ import annotations

from postrule import Phase, ml_switch
from postrule.core import ClassificationRecord, Verdict
from postrule.gates import McNemarGate, _source_correct_for
from postrule.models import ModelPrediction
from postrule.verdicts import CallableVerdictSource

LABELS = ["a", "b"]


class _PerfectModel:
    """A shadow model that's always right. The driver sets ``.truth`` before
    each classify (the model is invoked inside the switch's shadow path)."""

    def __init__(self) -> None:
        self.truth = None

    def classify(self, inp, labels):
        return ModelPrediction(label=self.truth, confidence=0.9)


def _drive(adjudicate, n=120, advance_every=10):
    # Rule always answers "a"; truth alternates → rule is ~50%. A perfect model
    # is right exactly on the records where the rule is wrong — the records the
    # un-fixed gate discards.
    stream = [(f"x{i}", "a" if i % 2 == 0 else "b") for i in range(n)]
    truth = dict(stream)
    verifier = CallableVerdictSource(
        lambda inp, label: Verdict.CORRECT if label == truth[inp] else Verdict.INCORRECT
    )
    model = _PerfectModel()

    kwargs = {
        "labels": LABELS,
        "model": model,
        "gate": McNemarGate(alpha=0.05, min_paired=20),
        "verifier": verifier,
        "verifier_sample_rate": 1.0,
        "starting_phase": Phase.MODEL_SHADOW,
    }
    if adjudicate is not None:
        kwargs["adjudicate_disagreements"] = adjudicate

    @ml_switch(**kwargs)
    def sw(x):
        return "a"

    s = sw.switch
    for i, (x, true) in enumerate(stream, 1):
        model.truth = true
        s.classify(x)
        if i % advance_every == 0:
            s.advance()
    return s.phase()


def test_default_on_lets_a_better_challenger_graduate():
    # No explicit flag → default (True). The perfect model must graduate past
    # MODEL_SHADOW (this is the bug fix: it could not, before).
    assert _drive(adjudicate=None) == Phase.MODEL_PRIMARY


def test_explicit_off_reproduces_the_incumbent_bias():
    # With adjudication disabled, the same perfect model is structurally
    # unprovable and the switch stays stuck — documents the trap + that the
    # flag controls it.
    assert _drive(adjudicate=False) == Phase.MODEL_SHADOW


def test_no_verifier_is_a_safe_noop():
    # Without a verifier there's nothing to adjudicate; default-on must not
    # crash and simply records as before.
    @ml_switch(labels=LABELS, starting_phase=Phase.MODEL_SHADOW)
    def sw(x):
        return "a"

    sw.switch.record_verdict(input="x", label="a", outcome="incorrect")
    assert sw.phase() == Phase.MODEL_SHADOW


# ---------------------------------------------------------------------------
# _source_correct_for — the per-layer correctness used by every paired gate
# ---------------------------------------------------------------------------


def _rec(**kw):
    base = {
        "timestamp": 0.0,
        "input": "x",
        "label": "a",
        "outcome": "correct",
        "source": "rule",
        "confidence": 1.0,
    }
    base.update(kw)
    return ClassificationRecord(**base)


def test_independent_verdict_is_preferred():
    # model disagreed with a wrong decision, but was independently judged right.
    r = _rec(outcome="incorrect", model_output="b", model_outcome=Verdict.CORRECT.value)
    assert _source_correct_for(r, "model_output") is True
    r2 = _rec(outcome="incorrect", model_output="b", model_outcome=Verdict.INCORRECT.value)
    assert _source_correct_for(r2, "model_output") is False


def test_incumbent_scored_false_on_its_own_failure():
    # The decision was the rule's output and it was judged wrong → rule is
    # wrong (False), not None. This is the approximation the old code's
    # docstring described but never implemented.
    r = _rec(outcome="incorrect", rule_output="a")  # rule produced the chosen (wrong) label
    assert _source_correct_for(r, "rule_output") is False


def test_unadjudicated_disagreement_stays_indeterminate():
    # No independent verdict + the layer disagreed with a wrong decision → we
    # genuinely can't tell → None (record dropped from pairing).
    r = _rec(outcome="incorrect", model_output="b")  # no model_outcome
    assert _source_correct_for(r, "model_output") is None


def test_correct_outcome_path_unchanged():
    r = _rec(outcome="correct", rule_output="a", model_output="a")
    assert _source_correct_for(r, "rule_output") is True
    assert _source_correct_for(r, "model_output") is True
    r2 = _rec(outcome="correct", model_output="b")  # disagreed with a right decision → wrong
    assert _source_correct_for(r2, "model_output") is False
