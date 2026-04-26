# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""auto_record=True default + .mark_*() fluent + verdict_for context manager + on_verdict hook."""

from __future__ import annotations

import pytest

from dendra import (
    ClassificationResult,
    LearnedSwitch,
    Phase,
    Verdict,
)


def _rule(ticket: dict) -> str:
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    return "feature_request"


class TestAutoRecordDefault:
    def test_classify_auto_appends_unknown_record(self):
        s = LearnedSwitch(rule=_rule)
        s.classify({"title": "app crashes"})
        recs = s.storage.load_records(s.name)
        assert len(recs) == 1
        assert recs[0].outcome == Verdict.UNKNOWN.value
        assert recs[0].label == "bug"

    def test_dispatch_auto_appends_unknown_record(self):
        def handler(t):
            return "handled"

        s = LearnedSwitch(rule=_rule, labels={"bug": handler, "feature_request": handler})
        s.dispatch({"title": "app crashes"})
        recs = s.storage.load_records(s.name)
        assert len(recs) == 1
        assert recs[0].outcome == Verdict.UNKNOWN.value
        assert recs[0].action_result == "handled"

    def test_auto_record_false_skips_auto_log(self):
        s = LearnedSwitch(rule=_rule, auto_record=False)
        s.classify({"title": "app crashes"})
        assert s.storage.load_records(s.name) == []

    def test_verdict_then_auto_produces_two_rows(self):
        s = LearnedSwitch(rule=_rule)
        r = s.classify({"title": "crash"})
        r.mark_correct()
        recs = s.storage.load_records(s.name)
        # 1 UNKNOWN from classify + 1 CORRECT from mark_correct
        assert len(recs) == 2
        outcomes = sorted(r.outcome for r in recs)
        assert outcomes == [Verdict.CORRECT.value, Verdict.UNKNOWN.value]


class TestMarkFluentShortcuts:
    def test_mark_correct(self):
        s = LearnedSwitch(rule=_rule, auto_record=False)
        r = s.classify({"title": "crash"})
        r.mark_correct()
        recs = s.storage.load_records(s.name)
        assert len(recs) == 1
        assert recs[0].outcome == Verdict.CORRECT.value
        assert recs[0].label == "bug"

    def test_mark_incorrect(self):
        s = LearnedSwitch(rule=_rule, auto_record=False)
        r = s.classify({"title": "crash"})
        r.mark_incorrect()
        recs = s.storage.load_records(s.name)
        assert recs[0].outcome == Verdict.INCORRECT.value

    def test_mark_unknown(self):
        s = LearnedSwitch(rule=_rule, auto_record=False)
        r = s.classify({"title": "crash"})
        r.mark_unknown()
        recs = s.storage.load_records(s.name)
        assert recs[0].outcome == Verdict.UNKNOWN.value

    def test_detached_result_raises(self):
        r = ClassificationResult(label="bug", source="rule", confidence=1.0, phase=Phase.RULE)
        with pytest.raises(RuntimeError, match="no switch back-reference"):
            r.mark_correct()


class TestVerdictForContextManager:
    def test_mark_inside_block_records_verdict(self):
        s = LearnedSwitch(rule=_rule, auto_record=False)
        with s.verdict_for({"title": "crash"}) as v:
            assert v.result.label == "bug"
            v.correct()
        recs = s.storage.load_records(s.name)
        assert len(recs) == 1
        assert recs[0].outcome == Verdict.CORRECT.value

    def test_no_mark_defaults_to_unknown(self):
        s = LearnedSwitch(rule=_rule, auto_record=False)
        with s.verdict_for({"title": "crash"}) as v:
            _ = v.result.label  # consumer looked but didn't mark
        recs = s.storage.load_records(s.name)
        assert len(recs) == 1
        assert recs[0].outcome == Verdict.UNKNOWN.value

    def test_exception_inside_block_still_defaults_to_unknown(self):
        s = LearnedSwitch(rule=_rule, auto_record=False)
        with pytest.raises(RuntimeError), s.verdict_for({"title": "crash"}) as _v:
            raise RuntimeError("downstream failed")
        recs = s.storage.load_records(s.name)
        assert recs[-1].outcome == Verdict.UNKNOWN.value

    def test_double_mark_is_idempotent(self):
        s = LearnedSwitch(rule=_rule, auto_record=False)
        with s.verdict_for({"title": "crash"}) as v:
            v.correct()
            v.incorrect()  # ignored — only the first verdict sticks
        recs = s.storage.load_records(s.name)
        assert len(recs) == 1
        assert recs[0].outcome == Verdict.CORRECT.value


class TestOnVerdictHook:
    def test_hook_fires_for_each_verdict(self):
        captured = []
        s = LearnedSwitch(
            rule=_rule,
            auto_record=False,
            on_verdict=lambda record: captured.append(record.label),
        )
        s.record_verdict(input={"t": 1}, label="bug", outcome=Verdict.CORRECT.value)
        s.record_verdict(input={"t": 2}, label="feature_request", outcome=Verdict.INCORRECT.value)
        assert captured == ["bug", "feature_request"]

    def test_hook_does_not_fire_on_auto_log(self):
        captured = []
        s = LearnedSwitch(
            rule=_rule,
            on_verdict=lambda record: captured.append(record.outcome),
        )
        s.classify({"title": "crash"})  # auto-logs UNKNOWN; hook should NOT fire
        assert captured == []

    def test_hook_exceptions_do_not_break_record_verdict(self):
        def bad_hook(_record):
            raise RuntimeError("hook blew up")

        s = LearnedSwitch(rule=_rule, auto_record=False, on_verdict=bad_hook)
        # Must not raise.
        s.record_verdict(input={"t": 1}, label="bug", outcome=Verdict.CORRECT.value)
