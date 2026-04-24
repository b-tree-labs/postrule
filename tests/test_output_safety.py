# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Output-safety demonstration — Dendra applied to LLM output classification.

Proves that output-safety filtering is Dendra's primitive applied to
a new input class (LLM-generated text rather than user input). No
API change, no new feature — the same :func:`ml_switch` decorator,
the same phase progression, the same ``safety_critical=True`` cap.

Scenarios exercised:

1. **Phase 0 rule-floor** — regex-based PII + blocklist catches the
   obvious cases at sub-microsecond cost.
2. **Phase 1 MODEL_SHADOW** — a commodity moderator runs alongside the
   rule; disagreements are logged for later graduation analysis.
3. **Safety-critical cap** — ``safety_critical=True`` refuses
   construction at Phase 5 (ML_PRIMARY).
4. **Graceful degradation** — when the LLM moderator raises, rule
   still decides and the user-visible path is unaffected.

This is both a test and a working reference for the "LLM output
moderation" category in ``docs/marketing/industry-applicability.md``
§3 Tier 1 item 7.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from dendra import (
    LearnedSwitch,
    ModelPrediction,
    Phase,
    SwitchConfig,
    Verdict,
    ml_switch,
)

# ---------------------------------------------------------------------------
# Minimal output-safety rule — Phase 0 worked example
# ---------------------------------------------------------------------------


_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_PATTERN = re.compile(r"\b\d{3}[- ]?\d{3}[- ]?\d{4}\b")
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w{2,}\b")

_BLOCKED_TERMS = (
    "kill yourself",
    "i hate",
    "offensive slur stub",
)

_CONFIDENTIAL_MARKERS = (
    "INTERNAL USE ONLY",
    "EXPORT_CONTROLLED",
    "SECRET//",
    "CONFIDENTIAL//",
)

OUTPUT_LABELS = ["safe", "pii", "toxic", "confidential", "refusal"]


def _output_rule(response: str) -> str:
    """Phase-0 safety rule over LLM outputs. Deterministic, sub-µs."""
    if not response:
        return "safe"
    if (
        _SSN_PATTERN.search(response)
        or _PHONE_PATTERN.search(response)
        or _EMAIL_PATTERN.search(response)
    ):
        return "pii"
    lower = response.lower()
    if any(term in lower for term in _BLOCKED_TERMS):
        return "toxic"
    if any(marker in response for marker in _CONFIDENTIAL_MARKERS):
        return "confidential"
    if response.strip().lower().startswith(("i cannot", "i can't", "i'm unable")):
        return "refusal"
    return "safe"


# ---------------------------------------------------------------------------
# 1. Phase 0 rule-floor
# ---------------------------------------------------------------------------


class TestPhase0RuleFloor:
    """The rule alone catches obvious unsafe outputs at sub-µs cost."""

    def test_pii_detected_in_ssn(self):
        @ml_switch(
            labels=OUTPUT_LABELS,
            author="@safety:output-gate",
            config=SwitchConfig(auto_record=False, phase=Phase.RULE, safety_critical=True),
        )
        def gate(response: str) -> str:
            return _output_rule(response)

        assert gate("your SSN is 123-45-6789") == "pii"

    def test_toxic_blocklist_match(self):
        @ml_switch(
            labels=OUTPUT_LABELS,
            author="@safety:output-gate",
            config=SwitchConfig(auto_record=False, phase=Phase.RULE, safety_critical=True),
        )
        def gate(response: str) -> str:
            return _output_rule(response)

        assert gate("kill yourself you loser") == "toxic"

    def test_confidential_marker_detected(self):
        @ml_switch(
            labels=OUTPUT_LABELS,
            author="@safety:output-gate",
            config=SwitchConfig(auto_record=False, phase=Phase.RULE, safety_critical=True),
        )
        def gate(response: str) -> str:
            return _output_rule(response)

        assert gate("per memo: INTERNAL USE ONLY") == "confidential"

    def test_benign_output_classified_safe(self):
        @ml_switch(
            labels=OUTPUT_LABELS,
            author="@safety:output-gate",
            config=SwitchConfig(auto_record=False, phase=Phase.RULE, safety_critical=True),
        )
        def gate(response: str) -> str:
            return _output_rule(response)

        assert gate("the weather is nice today") == "safe"

    def test_refusal_passes_through_separately(self):
        @ml_switch(
            labels=OUTPUT_LABELS,
            author="@safety:output-gate",
            config=SwitchConfig(auto_record=False, phase=Phase.RULE, safety_critical=True),
        )
        def gate(response: str) -> str:
            return _output_rule(response)

        assert gate("I cannot help with that request.") == "refusal"


# ---------------------------------------------------------------------------
# 2. Phase 1 — MODEL_SHADOW over outputs
# ---------------------------------------------------------------------------


@dataclass
class FakeModeratorLLM:
    """Stand-in for Perspective / OpenAI Moderation — deterministic."""

    always: str = "safe"
    conf: float = 0.9

    def classify(self, input, labels):
        return ModelPrediction(label=self.always, confidence=self.conf)


class TestPhase1ShadowOverOutputs:
    """LLM moderator observes; rule is still the source of truth."""

    def test_llm_shadow_records_disagreement(self):
        sw = LearnedSwitch(
            name="output_gate",
            rule=_output_rule,
            author="@safety:output-gate",
            model=FakeModeratorLLM(always="toxic", conf=0.98),
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_SHADOW, safety_critical=True),
        )
        # Rule says "safe" for this text; the LLM moderator says "toxic".
        # In shadow mode the rule wins; the disagreement lands in the
        # outcome log once recorded.
        r = sw.classify("the weather is nice today")
        assert r.label == "safe"
        assert r.source == "rule"
        r.mark_correct()
        [row] = sw.storage.load_records("output_gate")
        assert row.rule_output == "safe"
        assert row.model_output == "toxic"  # moderator observed
        assert row.model_confidence == pytest.approx(0.98)

    def test_broken_moderator_cannot_block_output(self):
        class BrokenModerator:
            def classify(self, input, labels):
                raise RuntimeError("provider down")

        sw = LearnedSwitch(
            name="output_gate",
            rule=_output_rule,
            author="@safety:output-gate",
            model=BrokenModerator(),
            config=SwitchConfig(auto_record=False, phase=Phase.MODEL_SHADOW, safety_critical=True),
        )
        # The moderator blows up; rule must still decide.
        r = sw.classify("your SSN is 123-45-6789")
        assert r.label == "pii"
        assert r.source == "rule"


# ---------------------------------------------------------------------------
# 3. Safety-critical cap
# ---------------------------------------------------------------------------


class TestSafetyCriticalCap:
    """Output-safety is ALWAYS safety_critical — never ML_PRIMARY."""

    def test_ml_primary_refused_at_construction(self):
        with pytest.raises(ValueError, match="safety_critical"):
            LearnedSwitch(
                name="output_gate",
                rule=_output_rule,
                author="@safety:output-gate",
                config=SwitchConfig(auto_record=False, phase=Phase.ML_PRIMARY, safety_critical=True),
            )

    def test_ml_with_fallback_is_the_cap(self):
        # safety_critical + Phase 4 is the intended "graduated" state.
        from dendra import MLPrediction

        class FakeML:
            def fit(self, records): ...
            def predict(self, input, labels):
                return MLPrediction(label="safe", confidence=0.99)

            def model_version(self):
                return "fake"

        sw = LearnedSwitch(
            name="output_gate",
            rule=_output_rule,
            author="@safety:output-gate",
            ml_head=FakeML(),
            config=SwitchConfig(
                phase=Phase.ML_WITH_FALLBACK,
                safety_critical=True,
                confidence_threshold=0.85,
            ),
        )
        # ML says "safe" with 0.99 — above threshold, so ML decides.
        r = sw.classify("the weather is nice today")
        assert r.label == "safe"
        assert r.source == "ml"


# ---------------------------------------------------------------------------
# 4. The decorator path — proves zero-code-change wiring
# ---------------------------------------------------------------------------


class TestDecoratorWiring:
    """The user-facing ergonomics for output safety are identical to
    input classification. This is the whole point of the correction."""

    def test_callable_still_works_as_before(self):
        @ml_switch(
            labels=OUTPUT_LABELS,
            author="@safety:output-gate",
            name="llm_output_gate",
            config=SwitchConfig(auto_record=False, phase=Phase.RULE, safety_critical=True),
        )
        def gate(response: str) -> str:
            return _output_rule(response)

        # Invoke like a normal function — no API shape change.
        assert gate("all good here") == "safe"
        assert gate("you can reach me at 555-123-4567") == "pii"
        # Dendra affordances available on the wrapper.
        assert gate.switch.author == "@safety:output-gate"
        assert gate.phase() is Phase.RULE
