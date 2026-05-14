# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""End-state: ML decides, the rule is the circuit-breaker target.

Run: `python examples/06_ml_primary.py`

At Phase.ML_PRIMARY the ML head decides on every call. The rule
isn't on the hot path — but it's not deleted either: it sits as
the circuit-breaker target, ready to take over the moment the ML
head raises, times out, or returns nonsense. That's the whole
safety story at the end-state.

What ML_PRIMARY is worth:

- Accuracy lift on the four shipped NLU benchmarks ranges
  from **+18.7 pp** (26 labels) to **+86.5 pp** (77 labels)
  at final training depth — bigger label space, bigger gap
  (see ``docs/papers/2026-when-should-a-rule-learn/``).
- The rule-floor circuit breaker means an ML failure routes
  back to the rule's accuracy, not to a 500.

For the situations where each of these matters most — and
where rule-vs-ML lift is small enough that ML_PRIMARY
isn't worth it — see ``docs/scenarios.md``.
"""

from __future__ import annotations

from postrule import LearnedSwitch, MLPrediction, Phase


def triage_rule(ticket: dict) -> str:
    """Classify one ticket into bug / feature_request / question."""
    heading = (ticket.get("title") or "").lower()
    if "crash" in heading or "error" in heading:
        return "bug"
    if heading.endswith("?"):
        return "question"
    return "feature_request"


class HealthyMLHead:
    """Stub ML head that's trained and healthy.

    Production would wrap an sklearn / ONNX / HuggingFace model;
    we hardcode the predictions so the example is deterministic.
    """

    def fit(self, _records):
        """No-op — the stub is hard-coded and doesn't learn."""

    def predict(self, ticket, _labels=None) -> MLPrediction:
        """Deterministic prediction for the given ticket."""
        heading = (ticket.get("title") or "").lower()
        if "crash" in heading or "error" in heading:
            return MLPrediction(label="bug", confidence=0.98)
        if "add" in heading or "feature" in heading or "support" in heading:
            return MLPrediction(label="feature_request", confidence=0.93)
        return MLPrediction(label="question", confidence=0.90)

    def model_version(self) -> str:
        """Version string surfaced in ``SwitchStatus.model_version``."""
        return "stub-healthy-1.0"


class FlakyMLHead:
    """Stub that can be forced to raise on the next predict() call."""

    def __init__(self) -> None:
        self.raise_on_next = False

    def fit(self, _records):
        """No-op — the stub is hard-coded and doesn't learn."""

    def predict(self, _ticket, _labels=None) -> MLPrediction:
        """Raise on demand, else return a dummy 'question' prediction."""
        if self.raise_on_next:
            raise RuntimeError("model server returned 503")
        return MLPrediction(label="question", confidence=0.95)

    def model_version(self) -> str:
        """Version string surfaced in ``SwitchStatus.model_version``."""
        return "stub-flaky-0.1"


if __name__ == "__main__":
    # ------------------------------------------------------------
    # Part 1: happy path — ML_PRIMARY is working, every call is ML.
    # ------------------------------------------------------------
    print("--- Part 1: ML_PRIMARY happy path ---\n")

    # Real ML_PRIMARY deployments should pass ``persist=True`` so
    # breaker-trip events land in the audit log; we skip that here
    # to keep the demo self-contained.
    switch = LearnedSwitch(
        name="triage",
        rule=triage_rule,
        ml_head=HealthyMLHead(),
        starting_phase=Phase.ML_PRIMARY,
        phase_limit=Phase.ML_PRIMARY,
    )

    cases = [
        {"title": "app crashes on login"},
        {"title": "add dark mode"},
        {"title": "is my account suspended?"},
    ]
    for case in cases:
        result = switch.classify(case)
        print(f"  {case['title']:40s}  → {result.label:18s}  source={result.source}")

    assert all(r.source == "ml" for r in [switch.classify(c) for c in cases])

    # ------------------------------------------------------------
    # Part 2: circuit breaker trip — ML fails, rule takes over.
    # ------------------------------------------------------------
    print("\n--- Part 2: ML head fails; breaker trips; rule takes over ---\n")

    flaky = FlakyMLHead()
    switch2 = LearnedSwitch(
        name="triage-flaky",
        rule=triage_rule,
        ml_head=flaky,
        starting_phase=Phase.ML_PRIMARY,
    )

    switch2.classify({"title": "what is the status of my ticket?"})  # warm-up

    # Drive the breaker past its trip threshold. The ML head raises
    # RuntimeError on each failing call; we catch that specific
    # class (not bare Exception) to keep the demo honest about what
    # the breaker is handling.
    flaky.raise_on_next = True
    for _ in range(3):
        try:
            switch2.classify({"title": "app crashes on login"})
        except RuntimeError:
            pass

    # Phase is still ML_PRIMARY; the breaker sits between classify()
    # and the ML head until reset. source="rule_fallback" (not
    # "rule") distinguishes breaker-trip from phase-normal rule use.
    flaky.raise_on_next = False
    result = switch2.classify({"title": "app crashes on login"})
    print(f"  After breaker trip → decided by: {result.source}")
    assert result.source == "rule_fallback"

    switch2.reset_circuit_breaker()
    result = switch2.classify({"title": "add dark mode"})
    print(f"  After reset_circuit_breaker()   → decided by: {result.source}")
    assert result.source == "ml"

    print("\n(The rule was never removed. It is the safety floor the")
    print(" circuit breaker falls back to. That's the whole story.)")
