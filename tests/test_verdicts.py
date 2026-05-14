# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""VerdictSource family — CallableVerdictSource, JudgeSource,
JudgeCommittee, bias guardrails, audit-stamp conventions."""

from __future__ import annotations

import pytest

from postrule import ModelPrediction, Verdict
from postrule.verdicts import (
    CallableVerdictSource,
    JudgeCommittee,
    JudgeSource,
    VerdictSource,
)


class _StubLM:
    """Stub ModelClassifier that returns a fixed verdict label."""

    def __init__(self, verdict: str = "correct", model: str = "stub-1") -> None:
        self._reply = verdict
        self._model = model

    def classify(self, input, labels):
        return ModelPrediction(label=self._reply, confidence=0.95)


class _FailingLM:
    def __init__(self, model: str = "failing") -> None:
        self._model = model

    def classify(self, input, labels):
        raise RuntimeError("judge is down")


def _double_input_judge(input, label):
    """Truth oracle for a simple test case: correct iff label == str(input)."""
    return Verdict.CORRECT if label == str(input) else Verdict.INCORRECT


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_callable_source_satisfies_protocol(self):
        s = CallableVerdictSource(_double_input_judge, name="test")
        assert isinstance(s, VerdictSource)

    def test_model_judge_satisfies_protocol(self):
        s = JudgeSource(_StubLM())
        assert isinstance(s, VerdictSource)

    def test_committee_satisfies_protocol(self):
        s = JudgeCommittee([_StubLM(model="a"), _StubLM(model="b")])
        assert isinstance(s, VerdictSource)


# ---------------------------------------------------------------------------
# CallableVerdictSource
# ---------------------------------------------------------------------------


class TestCallableVerdictSource:
    def test_dispatches_to_callable(self):
        s = CallableVerdictSource(_double_input_judge, name="echo")
        assert s.judge("x", "x") is Verdict.CORRECT
        assert s.judge("x", "y") is Verdict.INCORRECT

    def test_audit_stamp_includes_name(self):
        s = CallableVerdictSource(_double_input_judge, name="echo")
        assert s.source_name == "callable:echo"

    def test_non_verdict_return_raises(self):
        def _bad(i, lbl):
            return "correct"  # str, not Verdict

        s = CallableVerdictSource(_bad, name="bad")
        with pytest.raises(TypeError, match="must return a Verdict"):
            s.judge("x", "y")

    def test_rejects_non_callable(self):
        with pytest.raises(TypeError, match="fn must be callable"):
            CallableVerdictSource("not a callable", name="x")  # type: ignore[arg-type]

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name cannot be empty"):
            CallableVerdictSource(_double_input_judge, name="")


# ---------------------------------------------------------------------------
# JudgeSource — behavior + bias guardrails
# ---------------------------------------------------------------------------


class TestJudgeSource:
    def test_correct_verdict(self):
        s = JudgeSource(_StubLM(verdict="correct"))
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_incorrect_verdict(self):
        s = JudgeSource(_StubLM(verdict="incorrect"))
        assert s.judge("x", "y") is Verdict.INCORRECT

    def test_unknown_verdict_on_unparseable_response(self):
        s = JudgeSource(_StubLM(verdict="maybe"))
        assert s.judge("x", "y") is Verdict.UNKNOWN

    def test_judge_exception_absorbed_as_unknown(self):
        """A judge-side outage must not break the caller's audit loop."""
        s = JudgeSource(_FailingLM())
        assert s.judge("x", "y") is Verdict.UNKNOWN

    def test_audit_stamp_includes_model(self):
        s = JudgeSource(_StubLM(model="gpt-4o-mini"))
        assert s.source_name == "judge:gpt-4o-mini"

    # --- Bias guardrail ----------------------------------------------------

    def test_guard_blocks_same_model_construction(self):
        """Classifier and judge of the same class + model raises."""
        classifier = _StubLM(model="same-model")
        judge = _StubLM(model="same-model")
        with pytest.raises(ValueError, match="same language model"):
            JudgeSource(judge, require_distinct_from=classifier)

    def test_guard_blocks_same_instance(self):
        shared = _StubLM(model="shared")
        with pytest.raises(ValueError, match="same language model"):
            JudgeSource(shared, require_distinct_from=shared)

    def test_guard_permits_distinct_models(self):
        classifier = _StubLM(model="gpt-4o-mini")
        judge = _StubLM(model="claude-opus-4")
        # Must not raise.
        s = JudgeSource(judge, require_distinct_from=classifier)
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_guard_opt_out(self):
        """Explicit opt-out allows same-LLM construction — user takes bias risk."""
        shared = _StubLM(model="shared")
        s = JudgeSource(
            shared,
            require_distinct_from=shared,
            guard_against_same_llm=False,
        )
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_non_modelclassifier_rejected(self):
        with pytest.raises(TypeError, match="classify"):
            JudgeSource("not a model")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# JudgeCommittee
# ---------------------------------------------------------------------------


class TestJudgeCommittee:
    def test_majority_verdict(self):
        judges = [
            _StubLM(verdict="correct", model="a"),
            _StubLM(verdict="correct", model="b"),
            _StubLM(verdict="incorrect", model="c"),
        ]
        s = JudgeCommittee(judges, mode="majority")
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_majority_tie_returns_unknown(self):
        judges = [
            _StubLM(verdict="correct", model="a"),
            _StubLM(verdict="incorrect", model="b"),
        ]
        s = JudgeCommittee(judges, mode="majority")
        assert s.judge("x", "y") is Verdict.UNKNOWN

    def test_unanimous_all_agree(self):
        judges = [
            _StubLM(verdict="correct", model="a"),
            _StubLM(verdict="correct", model="b"),
            _StubLM(verdict="correct", model="c"),
        ]
        s = JudgeCommittee(judges, mode="unanimous")
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_unanimous_one_disagrees_returns_unknown(self):
        judges = [
            _StubLM(verdict="correct", model="a"),
            _StubLM(verdict="correct", model="b"),
            _StubLM(verdict="incorrect", model="c"),
        ]
        s = JudgeCommittee(judges, mode="unanimous")
        assert s.judge("x", "y") is Verdict.UNKNOWN

    def test_failing_judge_counted_as_unknown(self):
        judges = [
            _StubLM(verdict="correct", model="a"),
            _FailingLM(model="b"),
        ]
        s = JudgeCommittee(judges, mode="majority")
        # a votes correct (1), b votes unknown (1). UNKNOWN is dropped
        # as a contender, so correct wins.
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_audit_stamp_lists_members_and_mode(self):
        judges = [_StubLM(model="alpha"), _StubLM(model="beta")]
        s = JudgeCommittee(judges, mode="unanimous")
        assert s.source_name == "committee:alpha|beta(unanimous)"

    # --- Construction-time validation -------------------------------------

    def test_single_judge_rejected(self):
        with pytest.raises(ValueError, match="at least 2 judges"):
            JudgeCommittee([_StubLM(model="only")])

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match="mode must be"):
            JudgeCommittee(
                [_StubLM(model="a"), _StubLM(model="b")],
                mode="consensus",
            )

    def test_non_modelclassifier_in_committee_rejected(self):
        with pytest.raises(TypeError, match="classify"):
            JudgeCommittee([_StubLM(), "not-a-model"])  # type: ignore[list-item]

    def test_committee_clone_of_classifier_refused(self):
        classifier = _StubLM(model="gpt-4")
        judges = [
            _StubLM(model="claude-opus"),
            _StubLM(model="gpt-4"),  # same as classifier — should refuse
        ]
        with pytest.raises(ValueError, match="same language model"):
            JudgeCommittee(judges, require_distinct_from=classifier)
