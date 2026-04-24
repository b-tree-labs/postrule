# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for telemetry emitters."""

from __future__ import annotations

from dendra import LearnedSwitch, Verdict
from dendra.telemetry import ListEmitter, NullEmitter


def _rule(ticket: dict) -> str:
    if "crash" in (ticket.get("title", "") or "").lower():
        return "bug"
    return "feature_request"


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
        s.record_verdict(
            input={"title": "crash"},
            label="bug",
            outcome=Verdict.CORRECT.value,
        )
        names = [n for n, _ in em.events]
        assert "classify" in names
        assert "outcome" in names

    def test_broken_emitter_does_not_crash_decision(self):
        class BrokenEmitter:
            def emit(self, event, payload):
                raise RuntimeError("emitter down")

        s = LearnedSwitch(
            name="t",
            rule=_rule,
            author="alice",
            telemetry=BrokenEmitter(),
        )
        r = s.classify({"title": "crash"})
        assert r.label == "bug"
