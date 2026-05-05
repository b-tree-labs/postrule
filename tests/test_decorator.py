# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for the @ml_switch decorator.

Public decorator API is ``@ml_switch`` — brand-neutral so code readers
don't have to learn "Dendra" to read the code. Brand identity lives at
the import boundary (``from dendra import ml_switch``).
"""

from __future__ import annotations

import pytest

from dendra import InMemoryStorage, LearnedSwitch, Phase, Verdict, ml_switch

# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------


class TestDecoratorBehavior:
    def test_wrapped_function_still_callable(self):
        @ml_switch(labels=["bug", "feature"], author="alice")
        def triage(ticket):
            return "bug" if "crash" in ticket.get("title", "") else "feature"

        assert triage({"title": "App keeps crashing"}) == "bug"
        assert triage({"title": "add dark mode"}) == "feature"

    def test_exposes_underlying_switch_attributes(self):
        @ml_switch(labels=["a", "b"], author="alice")
        def f(x):
            return "a"

        # status() delegates to the wrapped LearnedSwitch
        st = f.status()
        assert st.phase is Phase.RULE
        assert st.outcomes_total == 0

    def test_record_outcome_flows_through(self):
        @ml_switch(labels=["a", "b"], author="alice")
        def f(x):
            return "a"

        f({"input": 1})
        f.record_verdict(
            input={"input": 1},
            label="a",
            outcome=Verdict.CORRECT.value,
        )
        assert f.status().outcomes_total == 1
        assert f.status().outcomes_correct == 1

    def test_name_defaults_to_function_name(self):
        @ml_switch(labels=["a"], author="alice")
        def my_classifier(x):
            return "a"

        assert my_classifier.name == "my_classifier"

    def test_name_override(self):
        @ml_switch(labels=["a"], author="alice", name="custom-name")
        def f(x):
            return "a"

        assert f.name == "custom-name"

    def test_exposes_wrapped_LearnedSwitch(self):
        @ml_switch(labels=["a"], author="alice")
        def f(x):
            return "a"

        assert isinstance(f.switch, LearnedSwitch)

    def test_accepts_custom_storage(self):
        store = InMemoryStorage()

        @ml_switch(labels=["a"], author="alice", storage=store)
        def f(x):
            return "a"

        assert f.switch.storage is store


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestDecoratorValidation:
    def test_requires_author(self):
        with pytest.raises(ValueError, match="author"):

            @ml_switch(labels=["a"], author="")
            def f(x):
                return "a"

    def test_labels_can_be_empty_in_phase_0(self):
        """Phase 0 (RULE-only) doesn't need labels; they're for ML head
        configuration in later phases. But an explicit empty list is
        accepted so callers can pre-declare when convenient."""

        @ml_switch(labels=[], author="alice")
        def f(x):
            return "a"

        assert f({}) == "a"

    def test_preserves_function_metadata(self):
        @ml_switch(labels=["a"], author="alice")
        def triage(ticket):
            """Classify a ticket."""
            return "a"

        # Expose the wrapped function's __doc__ and __name__.
        assert triage.__name__ == "triage"
        assert triage.__doc__ == "Classify a ticket."
