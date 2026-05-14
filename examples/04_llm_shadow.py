# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""Phase 1 — language model shadow. The rule still decides; the language model watches.

Run: `python examples/04_llm_shadow.py`

In MODEL_SHADOW the rule is the sole decision-maker. The language model runs
on every call in shadow, its prediction captured on the outcome
record next to the rule's. That paired log is what the evidence
gate (a head-to-head significance test on the same inputs) later
consumes to decide whether advancing to MODEL_PRIMARY is
statistically justified — graduation is evidence-driven, not
gut-feel.

Uses a stub language model so the script runs with zero external API calls;
in production swap for ``OpenAIAdapter`` / ``AnthropicAdapter`` /
``OllamaAdapter`` / ``LlamafileAdapter``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from postrule import LearnedSwitch, ModelPrediction, Phase, Verdict


class StubLM:
    """Deterministic stand-in for a real language-model adapter.

    Implements the ``classify(input, labels)`` interface shared by
    the bundled adapters, so swapping is a one-line change.
    """

    def classify(self, ticket: Any, _labels: Iterable[str]) -> ModelPrediction:
        """Return a deterministic prediction for the given ticket."""
        heading = (ticket.get("title") or "").lower()
        # Toy: language model catches "question about X" that the rule's
        # ends-with-'?' test would miss.
        if "question about" in heading or heading.endswith("?"):
            return ModelPrediction(label="question", confidence=0.88)
        if "crash" in heading or "error" in heading:
            return ModelPrediction(label="bug", confidence=0.95)
        return ModelPrediction(label="feature_request", confidence=0.72)


def triage_rule(ticket: dict) -> str:
    """Classify one ticket into bug / feature_request / question."""
    heading = (ticket.get("title") or "").lower()
    if "crash" in heading:
        return "bug"
    if heading.endswith("?"):
        return "question"
    return "feature_request"


if __name__ == "__main__":
    # For a real shadow deployment pass ``persist=True`` so the
    # paired (rule, model) predictions survive restart. Demo keeps
    # the default bounded in-memory storage.
    switch = LearnedSwitch(
        rule=triage_rule,
        model=StubLM(),
        starting_phase=Phase.MODEL_SHADOW,
    )

    tickets = [
        {"title": "app crashes on login"},
        {"title": "add dark mode"},
        {"title": "question about account deletion"},
        {"title": "error in checkout flow"},
    ]

    print(f"Phase: {switch.phase().name} — rule decides, language model shadows\n")
    for sample in tickets:
        # Minimum required: one `classify()` or `dispatch()` call per
        # input. record_verdict() below is OPTIONAL — call it only
        # when you want the paired (rule, model) predictions in the
        # outcome log for later transition-gate analysis. See
        # docs/api-reference.md for the full required-vs-optional
        # surface.
        result = switch.classify(sample)
        switch.record_verdict(
            input=sample,
            label=result.label,
            outcome=Verdict.CORRECT.value,
        )

    records = switch.storage.load_records(switch.name)
    print(f"{'Input':40s}  {'Rule (decision)':18s}  {'language model (shadow)':18s}")
    print("-" * 82)
    for rec in records:
        title = rec.input.get("title", "")
        rule_out = rec.rule_output or rec.label
        model_out = rec.model_output or "-"
        print(f"{title:40s}  {rule_out:18s}  {model_out:18s}")

    print()
    print(f"Stored {len(records)} outcome records with paired (rule, model) predictions.")
