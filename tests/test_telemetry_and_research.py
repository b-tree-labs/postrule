# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for telemetry emitters and the transition-curve runner."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from dendra import (
    LearnedSwitch,
    LLMPrediction,
    Outcome,
    Phase,
    SwitchConfig,
)
from dendra.research import BenchmarkExample, Checkpoint, run_transition_curve
from dendra.telemetry import ListEmitter, NullEmitter, StdoutEmitter


def _rule(ticket: dict) -> str:
    if "crash" in (ticket.get("title", "") or "").lower():
        return "bug"
    return "feature_request"


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


class TestTelemetry:
    def test_default_emitter_is_null(self):
        s = LearnedSwitch(name="t", rule=_rule, author="alice")
        s.classify({"title": "crash"})
        # NullEmitter captures nothing and does not crash.
        assert isinstance(s._telemetry, NullEmitter)

    def test_list_emitter_captures_classify_events(self):
        em = ListEmitter()
        s = LearnedSwitch(name="t", rule=_rule, author="alice", telemetry=em)
        s.classify({"title": "crash"})
        events = [(name, p) for name, p in em.events if name == "classify"]
        assert len(events) == 1
        assert events[0][1]["switch"] == "t"
        assert events[0][1]["source"] == "rule"

    def test_list_emitter_captures_outcome_events(self):
        em = ListEmitter()
        s = LearnedSwitch(name="t", rule=_rule, author="alice", telemetry=em)
        s.classify({"title": "crash"})
        s.record_outcome(
            input={"title": "crash"}, output="bug", outcome=Outcome.CORRECT.value,
        )
        names = [n for n, _ in em.events]
        assert "classify" in names
        assert "outcome" in names

    def test_broken_emitter_does_not_crash_decision(self):
        class BrokenEmitter:
            def emit(self, event, payload):
                raise RuntimeError("emitter down")

        s = LearnedSwitch(
            name="t", rule=_rule, author="alice", telemetry=BrokenEmitter(),
        )
        r = s.classify({"title": "crash"})
        assert r.output == "bug"


# ---------------------------------------------------------------------------
# Research instrumentation — transition-curve runner
# ---------------------------------------------------------------------------


@dataclass
class AgreesWithRuleLLM:
    """LLM that mirrors the rule — drives 100% agreement for tests."""

    def classify(self, input, labels):
        label = _rule(input)
        return LLMPrediction(label=label, confidence=0.95)


class TestTransitionCurve:
    def test_runs_checkpoints(self):
        s = LearnedSwitch(name="t", rule=_rule, author="alice")
        examples = [
            BenchmarkExample(input={"title": f"crash {i}"}, label="bug")
            for i in range(10)
        ]
        checkpoints = run_transition_curve(s, examples, checkpoint_every=5)
        assert len(checkpoints) == 2
        assert all(isinstance(c, Checkpoint) for c in checkpoints)
        assert checkpoints[-1].outcomes == 10
        # Every example matched the rule.
        assert checkpoints[-1].rule_accuracy == pytest.approx(1.0)
        assert checkpoints[-1].decision_accuracy == pytest.approx(1.0)

    def test_captures_llm_shadow_accuracy(self):
        s = LearnedSwitch(
            name="t",
            rule=_rule,
            author="alice",
            llm=AgreesWithRuleLLM(),
            config=SwitchConfig(phase=Phase.LLM_SHADOW),
        )
        examples = [
            BenchmarkExample(input={"title": "crash"}, label="bug"),
            BenchmarkExample(input={"title": "add feature"}, label="feature_request"),
        ] * 5
        checkpoints = run_transition_curve(s, examples, checkpoint_every=5)
        last = checkpoints[-1]
        assert last.llm_accuracy == pytest.approx(1.0)
        assert last.ml_accuracy is None  # no ML head at Phase 1

    def test_tail_checkpoint_appended(self):
        s = LearnedSwitch(name="t", rule=_rule, author="alice")
        examples = [
            BenchmarkExample(input={"title": "crash"}, label="bug")
            for _ in range(7)
        ]
        # 5-step checkpoint gives one at t=5; tail fires at t=7.
        checkpoints = run_transition_curve(s, examples, checkpoint_every=5)
        assert [c.outcomes for c in checkpoints] == [5, 7]
