# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""LearnedSwitch.demote() — manual operator escape hatch.

Tests the manual demote API in isolation. The auto-demote loop is
covered separately in test_drift_auto.py.
"""

from __future__ import annotations

import pytest

from dendra import (
    LearnedSwitch,
    ListEmitter,
    Phase,
    SwitchConfig,
)


def _rule(_x: dict) -> str:
    return "A"


# ---------------------------------------------------------------------------
# Manual demote happy paths
# ---------------------------------------------------------------------------


class TestManualDemoteSteps:
    """demote() walks the lifecycle backward one phase per call."""

    def test_demote_from_ml_primary_to_ml_with_fallback(self):
        sw = LearnedSwitch(
            rule=_rule,
            name="d_p5",
            author="t",
            config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
        )
        decision = sw.demote(reason="manual ops verification")

        assert decision.target_better is True
        assert sw.phase() is Phase.ML_WITH_FALLBACK

    def test_demote_from_ml_with_fallback_to_ml_shadow(self):
        sw = LearnedSwitch(
            rule=_rule,
            name="d_p4",
            author="t",
            config=SwitchConfig(starting_phase=Phase.ML_WITH_FALLBACK),
        )
        decision = sw.demote(reason="rule has drifted ahead per offline check")

        assert decision.target_better is True
        assert sw.phase() is Phase.ML_SHADOW

    def test_demote_from_model_primary_to_model_shadow(self):
        sw = LearnedSwitch(
            rule=_rule,
            name="d_p2",
            author="t",
            config=SwitchConfig(starting_phase=Phase.MODEL_PRIMARY),
        )
        decision = sw.demote(reason="LLM provider behavior shifted")

        assert decision.target_better is True
        assert sw.phase() is Phase.MODEL_SHADOW


# ---------------------------------------------------------------------------
# Boundary
# ---------------------------------------------------------------------------


class TestManualDemoteAtRule:
    """At Phase.RULE there's nothing below; demote is a no-op."""

    def test_demote_at_rule_holds(self):
        sw = LearnedSwitch(
            rule=_rule,
            name="d_p0",
            author="t",
            config=SwitchConfig(starting_phase=Phase.RULE),
        )
        decision = sw.demote(reason="anything")

        assert decision.target_better is False
        assert sw.phase() is Phase.RULE
        assert "RULE" in decision.rationale or "floor" in decision.rationale.lower()


# ---------------------------------------------------------------------------
# Telemetry + rationale
# ---------------------------------------------------------------------------


class TestManualDemoteTelemetry:
    def test_demote_emits_event_with_reason(self):
        emitter = ListEmitter()
        sw = LearnedSwitch(
            rule=_rule,
            name="d_telemetry",
            author="t",
            config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
            telemetry=emitter,
        )
        sw.demote(reason="rule drift detected by offline check")

        # ListEmitter exposes a list of (event, payload) tuples.
        events = [(name, payload) for name, payload in emitter.events]
        demote_events = [(n, p) for n, p in events if n == "demote"]
        assert len(demote_events) == 1
        _, payload = demote_events[0]
        assert payload["from"] == Phase.ML_PRIMARY.value
        assert payload["to"] == Phase.ML_WITH_FALLBACK.value
        assert "drift" in payload.get("rationale", "").lower()

    def test_demote_rationale_includes_reason(self):
        sw = LearnedSwitch(
            rule=_rule,
            name="d_reason",
            author="t",
            config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
        )
        decision = sw.demote(reason="my-specific-reason-string")
        assert "my-specific-reason-string" in decision.rationale


# ---------------------------------------------------------------------------
# Safety_critical does not block demote
# ---------------------------------------------------------------------------


class TestSafetyCriticalDoesNotBlockDemote:
    """safety_critical=True caps the FORWARD ceiling at ML_WITH_FALLBACK
    but must not block demotion. Demoting strengthens the safety floor."""

    def test_demote_works_with_safety_critical(self):
        sw = LearnedSwitch(
            rule=_rule,
            name="d_safety",
            author="t",
            config=SwitchConfig(
                starting_phase=Phase.ML_WITH_FALLBACK,
                safety_critical=True,
            ),
        )
        decision = sw.demote(reason="conservative roll-back")

        assert decision.target_better is True
        assert sw.phase() is Phase.ML_SHADOW


# ---------------------------------------------------------------------------
# demote requires a reason (non-empty string) by API contract
# ---------------------------------------------------------------------------


class TestDemoteReasonRequired:
    def test_empty_reason_raises(self):
        sw = LearnedSwitch(
            rule=_rule,
            name="d_emptyreason",
            author="t",
            config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
        )
        with pytest.raises(ValueError, match="reason"):
            sw.demote(reason="")
