# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Label() + dict-labels action dispatch."""

from __future__ import annotations

import pytest

from dendra import Label, LearnedSwitch, Verdict


def _rule(ticket):
    return "bug" if "crash" in ticket.get("title", "") else "feature"


class TestLabelNormalization:
    def test_str_list_coerces_to_label_objects(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            labels=["bug", "feature"],
        )
        assert [label.name for label in s.labels] == ["bug", "feature"]
        assert all(label.on is None for label in s.labels)

    def test_label_objects_round_trip(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            labels=[Label("bug"), Label("feature", on=lambda x: "handled")],
        )
        names = [label.name for label in s.labels]
        assert names == ["bug", "feature"]
        assert s.labels[0].on is None
        assert s.labels[1].on is not None

    def test_dict_labels_shorthand(self):
        def on_bug(x):
            return "bug-handled"

        def on_feature(x):
            return "feature-handled"

        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            labels={"bug": on_bug, "feature": on_feature},
        )
        assert [label.name for label in s.labels] == ["bug", "feature"]
        assert s.labels[0].on is on_bug
        assert s.labels[1].on is on_feature

    def test_mixed_list_str_and_label_accepted(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            labels=["bug", Label("feature", on=lambda x: "ok")],
        )
        assert s.labels[0].on is None
        assert s.labels[1].on is not None

    def test_bad_entry_raises(self):
        with pytest.raises(TypeError, match="str or Label"):
            LearnedSwitch(
                name="triage",
                rule=_rule,
                author="alice",
                labels=[123],  # type: ignore[list-item]
            )


class TestClassifyIsPure:
    """classify() must NEVER fire label actions — least-surprise invariant."""

    def test_classify_does_not_fire_action(self):
        fired = []

        def on_bug(ticket):
            fired.append(ticket)
            return "should-not-appear"

        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            labels={"bug": on_bug},
        )
        r = s.classify({"title": "App keeps crashing"})
        assert r.label == "bug"
        assert r.action_result is None
        assert r.action_raised is None
        assert r.action_elapsed_ms is None
        assert fired == []


class TestActionDispatch:
    def test_matching_label_fires_action(self):
        received = []

        def on_bug(ticket):
            received.append(ticket)
            return "engineering-ticket-created"

        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            labels={"bug": on_bug, "feature": lambda x: "n/a"},
        )
        r = s.dispatch({"title": "App keeps crashing"})
        assert r.label == "bug"
        assert r.action_result == "engineering-ticket-created"
        assert r.action_raised is None
        assert r.action_elapsed_ms is not None
        assert r.action_elapsed_ms >= 0.0
        assert received == [{"title": "App keeps crashing"}]

    def test_label_without_on_does_not_dispatch(self):
        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            labels=[Label("bug"), Label("feature")],
        )
        r = s.dispatch({"title": "App keeps crashing"})
        assert r.label == "bug"
        assert r.action_result is None
        assert r.action_raised is None
        assert r.action_elapsed_ms is None

    def test_action_exception_is_captured_not_raised(self):
        def boom(x):
            raise RuntimeError("downstream unavailable")

        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            labels={"bug": boom},
        )
        r = s.dispatch({"title": "App keeps crashing"})
        assert r.label == "bug"
        assert r.action_result is None
        assert r.action_raised is not None
        assert "RuntimeError" in r.action_raised
        assert "downstream unavailable" in r.action_raised

    def test_unmatched_output_no_dispatch(self):
        def on_bug(x):
            return "should-not-fire"

        s = LearnedSwitch(
            name="triage",
            rule=lambda _: "unknown_label",
            author="alice",
            labels={"bug": on_bug},
        )
        r = s.dispatch({"title": "whatever"})
        assert r.label == "unknown_label"
        assert r.action_result is None

    def test_action_info_propagates_to_outcome_record(self):
        def on_bug(x):
            return "ok"

        s = LearnedSwitch(
            name="triage",
            rule=_rule,
            author="alice",
            labels={"bug": on_bug},
            auto_record=False,  # isolate verdict-record path
        )
        r = s.dispatch({"title": "App keeps crashing"})
        r.mark_correct()
        recs = s.storage.load_records("triage")
        assert len(recs) == 1
        assert recs[0].action_result == "ok"
        assert recs[0].action_raised is None
        assert recs[0].action_elapsed_ms is not None
