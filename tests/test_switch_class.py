# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Native authoring pattern: ``dendra.Switch`` class convention.

TDD spec for the foundational v1 feature. The class lets users author a
classifier as a Python class with three method-name conventions
(``_evidence_*`` / ``_rule`` or ``_when_<label>`` / ``_on_<label>``)
instead of as a decorated function. Same primitive as ``@ml_switch``
underneath; more idiomatic and refactor-friendly on top.

Each test states one contract claim. The tests don't probe internals.
"""

from __future__ import annotations

import asyncio
from dataclasses import is_dataclass

import pytest

from dendra import Phase
from dendra.switch_class import Switch

# ------------------------------------------------------------------
# Test fixtures: a realistic single-arg switch with three labels.
# ------------------------------------------------------------------


def _make_router_class():
    """Factory so each test gets a fresh subclass + fresh handler tape."""
    handler_tape: list[tuple[str, str]] = []

    class RouteUser(Switch):
        # Evidence: each method's return-type hint becomes a dataclass field
        def _evidence_user_tier(self, text: str) -> str:
            return (
                "vip"
                if text.endswith("_vip")
                else ("free" if text.endswith("_free") else "regular")
            )

        def _evidence_fast_lane(self, text: str) -> bool:
            return text.startswith("fast_")

        # Rule: takes packed evidence, returns a label name
        def _rule(self, evidence) -> str:
            if evidence.user_tier == "vip" and evidence.fast_lane:
                return "premium"
            if evidence.user_tier == "free":
                return "basic"
            return "standard"

        # Action handlers: receive the original input the switch was called with
        def _on_premium(self, text: str):
            handler_tape.append(("premium", text))
            return "served-premium"

        def _on_basic(self, text: str):
            handler_tape.append(("basic", text))
            return "served-basic"

        # Note: no _on_standard — that label fires no action

    return RouteUser, handler_tape


# ------------------------------------------------------------------
# Core contract tests — each one a single claim.
# ------------------------------------------------------------------


class TestEvidenceDataclassConstruction:
    """The class auto-builds an evidence dataclass from _evidence_* return type hints."""

    def test_evidence_dataclass_exists_and_is_a_dataclass(self):
        RouteUser, _ = _make_router_class()
        switch = RouteUser()
        ev = switch._evidence_class
        assert is_dataclass(ev)

    def test_evidence_dataclass_has_one_field_per_evidence_method(self):
        RouteUser, _ = _make_router_class()
        switch = RouteUser()
        fields = {f.name for f in switch._evidence_class.__dataclass_fields__.values()}
        assert fields == {"user_tier", "fast_lane"}

    def test_evidence_field_types_match_return_annotations(self):
        RouteUser, _ = _make_router_class()
        switch = RouteUser()
        types = {f.name: f.type for f in switch._evidence_class.__dataclass_fields__.values()}
        assert types == {"user_tier": str, "fast_lane": bool}


class TestClassifyAtRulePhase:
    """At Phase.RULE the rule's label is what the user gets back."""

    def test_classify_returns_premium_for_vip_fast_lane(self):
        RouteUser, _ = _make_router_class()
        switch = RouteUser()
        result = switch.classify("fast_user_vip")
        assert result.label == "premium"
        assert result.phase is Phase.RULE
        assert result.source == "rule"

    def test_classify_returns_basic_for_free_user(self):
        RouteUser, _ = _make_router_class()
        switch = RouteUser()
        result = switch.classify("user_free")
        assert result.label == "basic"

    def test_classify_returns_standard_default(self):
        RouteUser, _ = _make_router_class()
        switch = RouteUser()
        result = switch.classify("plain_user")
        assert result.label == "standard"


class TestDispatchFiresMatchingHandler:
    """dispatch() runs the chosen label's _on_<label> with the original input."""

    def test_dispatch_fires_on_premium(self):
        RouteUser, tape = _make_router_class()
        switch = RouteUser()
        result = switch.dispatch("fast_user_vip")
        assert result.label == "premium"
        assert tape == [("premium", "fast_user_vip")]
        assert result.action_result == "served-premium"

    def test_dispatch_fires_on_basic(self):
        RouteUser, tape = _make_router_class()
        switch = RouteUser()
        switch.dispatch("user_free")
        assert tape == [("basic", "user_free")]

    def test_dispatch_no_action_for_standard_label(self):
        """No _on_standard defined — dispatch returns the label without firing anything."""
        RouteUser, tape = _make_router_class()
        switch = RouteUser()
        result = switch.dispatch("plain_user")
        assert result.label == "standard"
        assert tape == []
        assert result.action_result is None

    def test_classify_does_not_fire_handler(self):
        """classify() returns the label but never invokes _on_<label>."""
        RouteUser, tape = _make_router_class()
        switch = RouteUser()
        switch.classify("fast_user_vip")
        assert tape == []


class TestWhenStyleAlternativeToRule:
    """Per-label _when_<label> methods are an alternative to one big _rule."""

    def test_when_methods_evaluate_in_declaration_order(self):
        tape: list[str] = []

        class TriageSwitch(Switch):
            def _evidence_severity(self, ticket: dict) -> str:
                return ticket.get("severity", "low")

            def _when_critical(self, evidence) -> bool:
                return evidence.severity == "critical"

            def _when_high(self, evidence) -> bool:
                return evidence.severity == "high"

            class Meta:
                default_label = "low"

            def _on_critical(self, ticket):
                tape.append("critical")

            def _on_high(self, ticket):
                tape.append("high")

        switch = TriageSwitch()
        assert switch.classify({"severity": "critical"}).label == "critical"
        assert switch.classify({"severity": "high"}).label == "high"
        assert switch.classify({"severity": "low"}).label == "low"

    def test_when_methods_first_true_wins(self):
        """If two _when_ predicates would both match, the one declared first wins."""

        class AmbiguousSwitch(Switch):
            def _evidence_payload(self, x: int) -> int:
                return x

            def _when_first(self, evidence) -> bool:
                return evidence.payload > 0  # matches when positive

            def _when_second(self, evidence) -> bool:
                return evidence.payload > 0  # also matches when positive

            class Meta:
                default_label = "neither"

            def _on_first(self, x):
                pass

            def _on_second(self, x):
                pass

        switch = AmbiguousSwitch()
        assert switch.classify(5).label == "first"


class TestStructuralValidation:
    """Misconfigured Switch subclasses fail loudly at class-definition
    time (in __init_subclass__), not silently at dispatch."""

    def test_neither_rule_nor_when_methods_raises(self):
        with pytest.raises(TypeError, match=r"_rule.*_when_"):

            class BrokenSwitch(Switch):
                def _evidence_x(self, x: int) -> int:
                    return x

                # No _rule, no _when_*

    def test_both_rule_and_when_methods_raises(self):
        with pytest.raises(TypeError, match=r"both.*_rule.*_when_"):

            class BrokenSwitch(Switch):
                def _evidence_x(self, x: int) -> int:
                    return x

                def _rule(self, evidence) -> str:
                    return "a"

                def _when_a(self, evidence) -> bool:
                    return True

    def test_evidence_method_without_return_annotation_raises(self):
        with pytest.raises(TypeError, match=r"return.*annotation"):

            class BrokenSwitch(Switch):
                def _evidence_x(self, x):  # no return annotation
                    return x

                def _rule(self, evidence) -> str:
                    return "a"

                def _on_a(self, x):
                    pass

    def test_orphaned_on_handler_raises(self):
        """An _on_<label> with no corresponding label in _when_'s set."""
        with pytest.raises(TypeError, match=r"_on_orphaned"):

            class BrokenSwitch(Switch):
                def _evidence_x(self, x: int) -> int:
                    return x

                def _when_a(self, evidence) -> bool:
                    return True

                class Meta:
                    default_label = "default"

                def _on_a(self, x):
                    pass

                def _on_orphaned(self, x):
                    pass  # no _when_orphaned


class TestProxiedLearnedSwitchAPI:
    """The Switch class exposes the existing LearnedSwitch surface."""

    def test_phase_property_proxies(self):
        RouteUser, _ = _make_router_class()
        switch = RouteUser()
        assert switch.phase() is Phase.RULE

    def test_name_defaults_to_class_name_snake_case(self):
        RouteUser, _ = _make_router_class()
        switch = RouteUser()
        # Either "RouteUser" or "route_user" is fine — pin a contract.
        assert switch.name == "RouteUser"

    def test_advance_method_exists(self):
        RouteUser, _ = _make_router_class()
        switch = RouteUser()
        # We don't actually advance here (no model); just confirm the method exists.
        assert callable(switch.advance)


class TestAsyncDispatchSurface:
    """adispatch() works on a Switch instance with sync handlers."""

    def test_adispatch_fires_handler(self):
        RouteUser, tape = _make_router_class()
        switch = RouteUser()
        result = asyncio.run(switch.adispatch("fast_user_vip"))
        assert result.label == "premium"
        assert tape == [("premium", "fast_user_vip")]


class TestInheritance:
    """A subclass of a Switch subclass inherits evidence and overrides cleanly."""

    def test_subclass_overrides_evidence_method(self):
        class Base(Switch):
            def _evidence_tier(self, x: str) -> str:
                return "base-tier"

            def _rule(self, evidence) -> str:
                return evidence.tier

            class Meta:
                # Both 'base-tier' and 'overridden-tier' are possible labels at
                # different points; declare them both as no-action defaults.
                no_action = ("base-tier", "overridden-tier")

        class Child(Base):
            def _evidence_tier(self, x: str) -> str:
                return "overridden-tier"

        base = Base()
        child = Child()
        assert base.classify("anything").label == "base-tier"
        assert child.classify("anything").label == "overridden-tier"
