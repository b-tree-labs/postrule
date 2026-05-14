# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Hoisted SwitchConfig kwargs + autogen name + collision detection."""

from __future__ import annotations

import pytest

from postrule import (
    InMemoryStorage,
    LearnedSwitch,
    Phase,
    SwitchConfig,
    ml_switch,
)


def _rule(ticket):
    return "bug" if "crash" in ticket.get("title", "") else "feature"


# ---------------------------------------------------------------------------
# Hoisted SwitchConfig kwargs
# ---------------------------------------------------------------------------


class TestHoistedConfigKwargs:
    def test_starting_phase_kwarg(self):
        s = LearnedSwitch(rule=_rule, starting_phase=Phase.MODEL_SHADOW)
        assert s.config.starting_phase is Phase.MODEL_SHADOW

    def test_phase_limit_kwarg(self):
        s = LearnedSwitch(
            rule=_rule,
            phase_limit=Phase.ML_WITH_FALLBACK,
        )
        assert s.config.phase_limit is Phase.ML_WITH_FALLBACK

    def test_safety_critical_kwarg(self):
        s = LearnedSwitch(rule=_rule, safety_critical=True)
        assert s.config.safety_critical is True
        # safety_critical caps phase_limit automatically.
        assert s.config.phase_limit is Phase.ML_WITH_FALLBACK

    def test_confidence_threshold_kwarg(self):
        s = LearnedSwitch(rule=_rule, confidence_threshold=0.92)
        assert s.config.confidence_threshold == pytest.approx(0.92)

    def test_default_config_applied_when_no_kwargs(self):
        s = LearnedSwitch(rule=_rule)
        assert s.config.starting_phase is Phase.RULE
        assert s.config.phase_limit is Phase.ML_PRIMARY
        assert s.config.safety_critical is False

    def test_combined_kwargs(self):
        s = LearnedSwitch(
            rule=_rule,
            starting_phase=Phase.MODEL_PRIMARY,
            phase_limit=Phase.ML_WITH_FALLBACK,
            confidence_threshold=0.95,
        )
        assert s.config.starting_phase is Phase.MODEL_PRIMARY
        assert s.config.phase_limit is Phase.ML_WITH_FALLBACK
        assert s.config.confidence_threshold == pytest.approx(0.95)

    def test_explicit_config_still_works(self):
        s = LearnedSwitch(
            rule=_rule,
            config=SwitchConfig(starting_phase=Phase.MODEL_SHADOW),
        )
        assert s.config.starting_phase is Phase.MODEL_SHADOW

    def test_config_and_hoisted_kwargs_conflict(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            LearnedSwitch(
                rule=_rule,
                starting_phase=Phase.MODEL_SHADOW,
                config=SwitchConfig(),
            )

    def test_hoisted_kwargs_work_on_ml_switch_decorator(self):
        @ml_switch(
            labels=["bug", "feature"],
            starting_phase=Phase.MODEL_SHADOW,
            confidence_threshold=0.9,
        )
        def triage(ticket):
            return "bug" if "crash" in ticket.get("title", "") else "feature"

        assert triage.switch.config.starting_phase is Phase.MODEL_SHADOW
        assert triage.switch.config.confidence_threshold == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Autogen name from rule.__name__
# ---------------------------------------------------------------------------


class TestNameAutoderivation:
    def test_name_defaults_to_rule_name(self):
        def triage_rule(ticket):
            return "bug"

        s = LearnedSwitch(rule=triage_rule)
        assert s.name == "triage_rule"

    def test_explicit_name_wins(self):
        def triage_rule(ticket):
            return "bug"

        s = LearnedSwitch(rule=triage_rule, name="custom-name")
        assert s.name == "custom-name"

    def test_lambda_rule_requires_explicit_name(self):
        with pytest.raises(ValueError, match="no stable __name__"):
            LearnedSwitch(rule=lambda t: "bug")

    def test_lambda_rule_with_explicit_name_works(self):
        s = LearnedSwitch(rule=lambda t: "bug", name="lambda-switch")
        assert s.name == "lambda-switch"

    def test_empty_name_rejected(self):
        def triage_rule(t):
            return "bug"

        with pytest.raises(ValueError, match="cannot be empty"):
            LearnedSwitch(rule=triage_rule, name="")


# ---------------------------------------------------------------------------
# Collision detection — same (storage, name) across two switches
# ---------------------------------------------------------------------------


class TestNameCollisionDetection:
    def test_shared_storage_same_autoderived_name_raises(self):
        def triage_rule(t):
            return "bug"

        shared = InMemoryStorage()
        # The first switch must be held in a local so the weakref
        # registry doesn't reclaim it before we try to collide.
        first = LearnedSwitch(rule=triage_rule, storage=shared)
        assert first is not None  # keep the ref alive
        with pytest.raises(ValueError, match="already using this storage"):
            LearnedSwitch(rule=triage_rule, storage=shared)

    def test_error_mentions_autogen_hint_when_name_not_explicit(self):
        def triage_rule(t):
            return "bug"

        shared = InMemoryStorage()
        first = LearnedSwitch(rule=triage_rule, storage=shared)
        assert first is not None
        with pytest.raises(ValueError, match="auto-derived from rule.__name__"):
            LearnedSwitch(rule=triage_rule, storage=shared)

    def test_shared_storage_distinct_names_ok(self):
        def triage_rule(t):
            return "bug"

        shared = InMemoryStorage()
        s1 = LearnedSwitch(rule=triage_rule, storage=shared, name="triage-a")
        s2 = LearnedSwitch(rule=triage_rule, storage=shared, name="triage-b")
        assert s1.name != s2.name

    def test_distinct_storages_same_name_ok(self):
        def triage_rule(t):
            return "bug"

        LearnedSwitch(rule=triage_rule)  # default storage
        # Second switch gets its own default BoundedInMemoryStorage → distinct.
        LearnedSwitch(rule=triage_rule)

    def test_gced_switch_frees_registry_slot(self):
        import gc

        def triage_rule(t):
            return "bug"

        shared = InMemoryStorage()
        s = LearnedSwitch(rule=triage_rule, storage=shared)
        del s
        gc.collect()

        # After GC, the name/storage pair is free for a fresh switch.
        s2 = LearnedSwitch(rule=triage_rule, storage=shared)
        assert s2.name == "triage_rule"

    def test_explicit_collision_hints_at_different_names(self):
        def triage_rule(t):
            return "bug"

        shared = InMemoryStorage()
        first = LearnedSwitch(rule=triage_rule, storage=shared, name="shared-name")
        assert first is not None
        with pytest.raises(ValueError, match="different name="):
            LearnedSwitch(rule=triage_rule, storage=shared, name="shared-name")
