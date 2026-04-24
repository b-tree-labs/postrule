# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""VerdictSource family — CallableVerdictSource, LLMJudgeSource,
LLMCommitteeSource, bias guardrails, audit-stamp conventions."""

from __future__ import annotations

import pytest

from dendra import ModelPrediction, Verdict
from dendra.verdicts import (
    CallableVerdictSource,
    LLMCommitteeSource,
    LLMJudgeSource,
    VerdictSource,
)


class _StubLLM:
    """Stub ModelClassifier that returns a fixed verdict label."""

    def __init__(self, verdict: str = "correct", model: str = "stub-1") -> None:
        self._reply = verdict
        self._model = model

    def classify(self, input, labels):
        return ModelPrediction(label=self._reply, confidence=0.95)


class _FailingLLM:
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

    def test_llm_judge_satisfies_protocol(self):
        s = LLMJudgeSource(_StubLLM())
        assert isinstance(s, VerdictSource)

    def test_committee_satisfies_protocol(self):
        s = LLMCommitteeSource([_StubLLM(model="a"), _StubLLM(model="b")])
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
        def _bad(i, l):
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
# LLMJudgeSource — behavior + bias guardrails
# ---------------------------------------------------------------------------


class TestLLMJudgeSource:
    def test_correct_verdict(self):
        s = LLMJudgeSource(_StubLLM(verdict="correct"))
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_incorrect_verdict(self):
        s = LLMJudgeSource(_StubLLM(verdict="incorrect"))
        assert s.judge("x", "y") is Verdict.INCORRECT

    def test_unknown_verdict_on_unparseable_response(self):
        s = LLMJudgeSource(_StubLLM(verdict="maybe"))
        assert s.judge("x", "y") is Verdict.UNKNOWN

    def test_judge_exception_absorbed_as_unknown(self):
        """A judge-side outage must not break the caller's audit loop."""
        s = LLMJudgeSource(_FailingLLM())
        assert s.judge("x", "y") is Verdict.UNKNOWN

    def test_audit_stamp_includes_model(self):
        s = LLMJudgeSource(_StubLLM(model="gpt-4o-mini"))
        assert s.source_name == "llm-judge:gpt-4o-mini"

    # --- Bias guardrail ----------------------------------------------------

    def test_guard_blocks_same_llm_construction(self):
        """Classifier and judge of the same class + model raises."""
        classifier = _StubLLM(model="same-model")
        judge = _StubLLM(model="same-model")
        with pytest.raises(ValueError, match="same LLM"):
            LLMJudgeSource(judge, require_distinct_from=classifier)

    def test_guard_blocks_same_instance(self):
        shared = _StubLLM(model="shared")
        with pytest.raises(ValueError, match="same LLM"):
            LLMJudgeSource(shared, require_distinct_from=shared)

    def test_guard_permits_distinct_models(self):
        classifier = _StubLLM(model="gpt-4o-mini")
        judge = _StubLLM(model="claude-opus-4")
        # Must not raise.
        s = LLMJudgeSource(judge, require_distinct_from=classifier)
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_guard_opt_out(self):
        """Explicit opt-out allows same-LLM construction — user takes bias risk."""
        shared = _StubLLM(model="shared")
        s = LLMJudgeSource(
            shared,
            require_distinct_from=shared,
            guard_against_same_llm=False,
        )
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_non_modelclassifier_rejected(self):
        with pytest.raises(TypeError, match="classify"):
            LLMJudgeSource("not a model")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# LLMCommitteeSource
# ---------------------------------------------------------------------------


class TestLLMCommitteeSource:
    def test_majority_verdict(self):
        judges = [
            _StubLLM(verdict="correct", model="a"),
            _StubLLM(verdict="correct", model="b"),
            _StubLLM(verdict="incorrect", model="c"),
        ]
        s = LLMCommitteeSource(judges, mode="majority")
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_majority_tie_returns_unknown(self):
        judges = [
            _StubLLM(verdict="correct", model="a"),
            _StubLLM(verdict="incorrect", model="b"),
        ]
        s = LLMCommitteeSource(judges, mode="majority")
        assert s.judge("x", "y") is Verdict.UNKNOWN

    def test_unanimous_all_agree(self):
        judges = [
            _StubLLM(verdict="correct", model="a"),
            _StubLLM(verdict="correct", model="b"),
            _StubLLM(verdict="correct", model="c"),
        ]
        s = LLMCommitteeSource(judges, mode="unanimous")
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_unanimous_one_disagrees_returns_unknown(self):
        judges = [
            _StubLLM(verdict="correct", model="a"),
            _StubLLM(verdict="correct", model="b"),
            _StubLLM(verdict="incorrect", model="c"),
        ]
        s = LLMCommitteeSource(judges, mode="unanimous")
        assert s.judge("x", "y") is Verdict.UNKNOWN

    def test_failing_judge_counted_as_unknown(self):
        judges = [
            _StubLLM(verdict="correct", model="a"),
            _FailingLLM(model="b"),
        ]
        s = LLMCommitteeSource(judges, mode="majority")
        # a votes correct (1), b votes unknown (1). UNKNOWN is dropped
        # as a contender, so correct wins.
        assert s.judge("x", "y") is Verdict.CORRECT

    def test_audit_stamp_lists_members_and_mode(self):
        judges = [_StubLLM(model="alpha"), _StubLLM(model="beta")]
        s = LLMCommitteeSource(judges, mode="unanimous")
        assert s.source_name == "llm-committee:alpha|beta(unanimous)"

    # --- Construction-time validation -------------------------------------

    def test_single_judge_rejected(self):
        with pytest.raises(ValueError, match="at least 2 judges"):
            LLMCommitteeSource([_StubLLM(model="only")])

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match="mode must be"):
            LLMCommitteeSource(
                [_StubLLM(model="a"), _StubLLM(model="b")],
                mode="consensus",
            )

    def test_non_modelclassifier_in_committee_rejected(self):
        with pytest.raises(TypeError, match="classify"):
            LLMCommitteeSource([_StubLLM(), "not-a-model"])  # type: ignore[list-item]

    def test_committee_clone_of_classifier_refused(self):
        classifier = _StubLLM(model="gpt-4")
        judges = [
            _StubLLM(model="claude-opus"),
            _StubLLM(model="gpt-4"),  # same as classifier — should refuse
        ]
        with pytest.raises(ValueError, match="same LLM"):
            LLMCommitteeSource(judges, require_distinct_from=classifier)
