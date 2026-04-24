# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Cold-start: bootstrap from LLM_PRIMARY, then graduate to ML.

Run: `python examples/07_llm_as_teacher.py`

The cold-start problem: a brand-new product has zero labeled data
and no hand-written rule for routing. You can't write the if/else
chain because you don't know the patterns yet. You can't train an
ML head because you have no labels.

The LLM-as-teacher pattern solves this. Start the switch at
Phase.LLM_PRIMARY — the LLM makes every decision. Every
classification is logged as (input, llm_label) — the LLM is
labeling your data for you, in production. After enough outcomes
accumulate, train a local ML head on the LLM's labels, then
graduate to Phase.ML_WITH_FALLBACK: the ML head decides on its
confident cases, the rule catches the rest, the LLM is retired
from the hot path.

This example uses a stub LLM so the script runs without API
credentials. In production you'd swap for OpenAIAdapter /
AnthropicAdapter / OllamaAdapter.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Iterable

from dendra import (
    InMemoryStorage,
    LearnedSwitch,
    LLMPrediction,
    MLPrediction,
    Outcome,
    Phase,
    SwitchConfig,
)


def minimal_rule(ticket: dict) -> str:
    """The eventual safety floor — written LATER, after the LLM has
    taught us what the patterns are. At bootstrap time we don't have
    one; we define it here so the code runs."""
    title = (ticket.get("title") or "").lower()
    if "crash" in title or "error" in title:
        return "bug"
    return "feature_request"


class StubLLM:
    """Stand-in for OpenAIAdapter / AnthropicAdapter / OllamaAdapter.
    Implements the LLMClassifier protocol: `classify(input, labels)`."""

    def classify(self, input: Any, labels: Iterable[str]) -> LLMPrediction:
        title = (input.get("title") or "").lower()
        # The LLM is more discerning than what any small rule could
        # capture: it reads context, recognizes paraphrases, handles
        # patterns the rule-writer hasn't imagined yet.
        if any(kw in title for kw in ("crash", "error", "broken", "hang")):
            return LLMPrediction(label="bug", confidence=0.94)
        if any(kw in title for kw in ("add", "support for", "option to", "feature")):
            return LLMPrediction(label="feature_request", confidence=0.90)
        if title.endswith("?") or title.startswith("how ") or title.startswith("can i"):
            return LLMPrediction(label="question", confidence=0.88)
        return LLMPrediction(label="feature_request", confidence=0.72)


class LocalMLHead:
    """A simple ML head we'll train on the LLM's accumulated labels.
    In production: scikit-learn TF-IDF + LogisticRegression, a
    sentence-transformer + linear probe, or a small ONNX classifier."""

    def __init__(self) -> None:
        self._label_counts: dict[str, int] = {}

    def fit(self, records):
        # Minimalist: use the most-common LLM label as a fallback.
        # A real head would train a text classifier on (title, label)
        # pairs and learn actual patterns.
        self._label_counts = {}
        for r in records:
            if r.llm_output is None:
                continue
            self._label_counts[r.llm_output] = self._label_counts.get(r.llm_output, 0) + 1

    def predict(self, input, labels=None) -> MLPrediction:
        title = (input.get("title") or "").lower()
        # Learned pattern from the LLM's teaching data
        if any(kw in title for kw in ("crash", "error", "broken")):
            return MLPrediction(label="bug", confidence=0.91)
        if any(kw in title for kw in ("add", "support")):
            return MLPrediction(label="feature_request", confidence=0.85)
        # Fallback to most-common label
        if self._label_counts:
            most_common = max(self._label_counts, key=self._label_counts.get)
            return MLPrediction(label=most_common, confidence=0.55)
        return MLPrediction(label="question", confidence=0.50)


if __name__ == "__main__":
    # ------------------------------------------------------------
    # Phase 1: Bootstrap. Start at LLM_PRIMARY. The LLM decides.
    # Every call is labeled by the LLM and stored as training data.
    # ------------------------------------------------------------
    print("--- Bootstrap: starting at LLM_PRIMARY with no ML head ---\n")

    storage = InMemoryStorage()
    switch = LearnedSwitch(
        name="triage-bootstrap",
        rule=minimal_rule,  # present but not on the decision path yet
        author="@triage:bootstrap",
        llm=StubLLM(),
        storage=storage,
        config=SwitchConfig(
            starting_phase=Phase.LLM_PRIMARY,
            phase_limit=Phase.ML_PRIMARY,  # room to graduate
        ),
    )

    # Simulated early production traffic
    early_tickets = [
        ("app crashes on login", "bug"),
        ("how do I reset my password?", "question"),
        ("error in checkout flow", "bug"),
        ("add dark mode option", "feature_request"),
        ("can i download my data?", "question"),
        ("payment page is broken", "bug"),
        ("support for markdown in comments", "feature_request"),
    ]
    for title, ground_truth in early_tickets:
        ticket = {"title": title}
        result = switch.classify(ticket)
        # In production, ground_truth comes from support agent review.
        # We include it here to measure how well the LLM labeled.
        outcome = (
            Outcome.CORRECT if result.output == ground_truth else Outcome.INCORRECT
        )
        switch.record_outcome(input=ticket, output=result.output, outcome=outcome.value)

    records = storage.load_outcomes("triage-bootstrap")
    correct = sum(1 for r in records if r.outcome == Outcome.CORRECT.value)
    print(f"  LLM decided {len(records)} tickets; {correct} correct ({correct/len(records):.0%})")
    print(f"  Every ticket is now (input, llm_label) training data.\n")

    # ------------------------------------------------------------
    # Phase 2: Graduate. Train an ML head on the LLM's labels, run
    # it in shadow for a while, then make it primary with the rule
    # as fallback for low-confidence cases.
    # ------------------------------------------------------------
    print("--- Graduate: train an ML head on LLM-labeled data ---\n")

    ml_head = LocalMLHead()
    ml_head.fit(records)  # uses the accumulated llm_output as labels

    # New switch that starts at ML_WITH_FALLBACK (skipping ML_SHADOW
    # for brevity; in production you'd do the shadow phase first).
    switch2 = LearnedSwitch(
        name="triage-graduated",
        rule=minimal_rule,
        author="@triage:graduated",
        llm=StubLLM(),  # kept for occasional LLM calls, no longer primary
        ml_head=ml_head,
        storage=storage,
        config=SwitchConfig(
            starting_phase=Phase.ML_WITH_FALLBACK,
            phase_limit=Phase.ML_PRIMARY,
        ),
    )

    # Production traffic — now the ML head decides on confident cases;
    # the rule catches the rest. LLM is no longer on every call.
    new_tickets = [
        {"title": "app crashed during onboarding"},
        {"title": "how to integrate with Slack?"},
        {"title": "add support for CSV export"},
    ]
    for ticket in new_tickets:
        result = switch2.classify(ticket)
        print(f"  {ticket['title']:40s}  → {result.output:18s}  source={result.source}")

    print("\n(The LLM taught the ML head by labeling production traffic.")
    print(" The rule was never on the hot path — but it's still there as")
    print(" the safety floor. The LLM is retired from the hot path now;")
    print(" brought back only when confidence is below threshold or the")
    print(" ML head needs retraining.)")
