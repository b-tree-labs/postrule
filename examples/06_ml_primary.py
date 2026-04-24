# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""End-state: ML decides, the rule is the circuit-breaker target.

Run: `python examples/06_ml_primary.py`

Phase.ML_PRIMARY is the final phase of Dendra's lifecycle. The ML
head makes the decision on every call. There is no runtime
fallback to the rule *on normal traffic*. But the rule has not
been removed — it waits, as the circuit-breaker target. When the
ML head fails (raises, times out, returns nonsense), the breaker
trips and routing falls back to the rule until an operator
resets.

The rule floor is never deleted. It is the thing the system
falls back to when the ML head is unhealthy — which is, in the
end, the whole safety story.
"""

from __future__ import annotations

from dendra import (
    InMemoryStorage,
    LearnedSwitch,
    MLHead,
    MLPrediction,
    Outcome,
    Phase,
    SwitchConfig,
)


def rule(ticket: dict) -> str:
    """The safety-floor classifier. Plain Python, no ML."""
    title = (ticket.get("title") or "").lower()
    if "crash" in title or "error" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


class HealthyMLHead:
    """An ML head that's been trained + is working. Decides confidently.
    The `labels` arg comes from the switch's declared label set — most
    heads ignore it, but production heads often use it to constrain
    predictions or for sanity checks."""

    def fit(self, records): pass

    def predict(self, input, labels=None) -> MLPrediction:
        title = (input.get("title") or "").lower()
        if "crash" in title or "error" in title:
            return MLPrediction(label="bug", confidence=0.98)
        if "add" in title or "feature" in title or "support" in title:
            return MLPrediction(label="feature_request", confidence=0.93)
        return MLPrediction(label="question", confidence=0.90)


class FlakyMLHead:
    """An ML head that's started failing. Simulates a degraded model."""

    def __init__(self) -> None:
        self.raise_on_next = False

    def fit(self, records): pass

    def predict(self, input, labels=None) -> MLPrediction:
        if self.raise_on_next:
            raise RuntimeError("model server returned 503")
        return MLPrediction(label="question", confidence=0.95)


if __name__ == "__main__":
    # ------------------------------------------------------------
    # Part 1: happy path — ML_PRIMARY is working, every call is ML.
    # ------------------------------------------------------------
    print("--- Part 1: ML_PRIMARY happy path ---\n")

    storage = InMemoryStorage()
    switch = LearnedSwitch(
        name="triage",
        rule=rule,
        author="@triage:ml-primary",
        ml_head=HealthyMLHead(),
        storage=storage,
        config=SwitchConfig(
            starting_phase=Phase.ML_PRIMARY,
            phase_limit=Phase.ML_PRIMARY,  # explicit — no further advancement
        ),
    )

    cases = [
        {"title": "app crashes on login"},
        {"title": "add dark mode"},
        {"title": "is my account suspended?"},
    ]
    for ticket in cases:
        result = switch.classify(ticket)
        print(f"  {ticket['title']:40s}  → {result.output:18s}  source={result.source}")

    # Every call was decided by the ML head.
    assert all(r.source == "ml" for r in [switch.classify(c) for c in cases])

    # ------------------------------------------------------------
    # Part 2: circuit breaker trip — ML fails, rule takes over.
    # ------------------------------------------------------------
    print("\n--- Part 2: ML head fails; breaker trips; rule takes over ---\n")

    flaky = FlakyMLHead()
    switch2 = LearnedSwitch(
        name="triage-flaky",
        rule=rule,
        author="@triage:ml-primary",
        ml_head=flaky,
        config=SwitchConfig(starting_phase=Phase.ML_PRIMARY),
    )

    # Warm-up — ML is fine.
    switch2.classify({"title": "what is the status of my ticket?"})

    # Simulate the ML server failing. Dendra's breaker trips after
    # consecutive failures (see SwitchConfig for tuning); with the
    # default the first raise trips it.
    flaky.raise_on_next = True
    for _ in range(3):
        try:
            switch2.classify({"title": "app crashes on login"})
        except Exception:
            pass  # breaker swallows; routing falls back

    # Subsequent calls route to the rule — even though we're still
    # in ML_PRIMARY. Phase didn't change; the breaker is just
    # sitting between the call and the ML head until reset. The
    # source string is "rule_fallback" to distinguish "rule because
    # breaker is tripped" from "rule because that's this phase's
    # normal decision-maker."
    flaky.raise_on_next = False  # ML is actually healthy again now
    result = switch2.classify({"title": "app crashes on login"})
    print(f"  After breaker trip → decided by: {result.source}")
    assert result.source == "rule_fallback", "breaker should route to rule_fallback"

    # Operator decides the ML head is healthy enough to re-enable.
    switch2.reset_circuit_breaker()
    result = switch2.classify({"title": "add dark mode"})
    print(f"  After reset_circuit_breaker()   → decided by: {result.source}")
    assert result.source == "ml"

    print("\n(The rule was never removed. It is the safety floor the")
    print(" circuit breaker falls back to. That's the whole story.)")
