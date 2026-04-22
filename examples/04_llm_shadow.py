# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Phase 1 — LLM shadow. The rule still decides; the LLM watches.

Run: `python examples/04_llm_shadow.py`

Phase 1 is where Dendra starts learning *without* yet risking
production behavior. The rule remains the sole decision-maker;
the LLM runs in shadow, predicting what it *would* decide, and
Dendra records both predictions side-by-side. Later, a
statistical transition gate (McNemar's paired-proportion test)
can decide when the LLM is reliably better than the rule — and
only then do you advance to Phase 2 (LLM_PRIMARY).

This example uses a stub LLM so the script runs with zero
external API calls. In production you'd swap for one of the
bundled adapters — `OpenAIAdapter`, `AnthropicAdapter`,
`OllamaAdapter`, `LlamafileAdapter`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from dendra import (
    InMemoryStorage,
    LearnedSwitch,
    LLMPrediction,
    Outcome,
    Phase,
    SwitchConfig,
)


class StubLLM:
    """A deterministic stand-in for a real LLM adapter.

    Implements the same `classify(input, labels)` interface that
    `OpenAIAdapter` / `AnthropicAdapter` / `OllamaAdapter` expose,
    so swapping to a real provider is a one-line change.
    """

    def classify(self, input: Any, labels: Iterable[str]) -> LLMPrediction:
        title = (input.get("title") or "").lower()
        # Toy: the LLM is slightly more discerning than the rule —
        # it recognizes "question about X" as a question even when
        # the title doesn't end in '?'.
        if "question about" in title or title.endswith("?"):
            return LLMPrediction(label="question", confidence=0.88)
        if "crash" in title or "error" in title:
            return LLMPrediction(label="bug", confidence=0.95)
        return LLMPrediction(label="feature_request", confidence=0.72)


def rule(ticket: dict) -> str:
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


if __name__ == "__main__":
    storage = InMemoryStorage()
    switch = LearnedSwitch(
        name="triage",
        rule=rule,
        author="@triage:llm-shadow",
        llm=StubLLM(),
        storage=storage,
        config=SwitchConfig(phase=Phase.LLM_SHADOW),
    )

    tickets = [
        {"title": "app crashes on login"},
        {"title": "add dark mode"},
        {"title": "question about account deletion"},
        {"title": "error in checkout flow"},
    ]

    print(f"Phase: {switch.config.phase.name} — rule decides, LLM shadows")
    print()
    for ticket in tickets:
        # classify() runs both the rule (decision) and the LLM (shadow).
        result = switch.classify(ticket)
        # record_outcome() persists the paired prediction to storage —
        # the SwitchResult alone only surfaces the decision; the
        # shadow lives in the OutcomeRecord fields llm_output /
        # llm_confidence so it can feed the transition gate later.
        switch.record_outcome(
            input=ticket,
            output=result.output,
            outcome=Outcome.CORRECT.value,
        )

    records = storage.load_outcomes(switch.name)
    print(f"{'Input':40s}  {'Rule (decision)':18s}  {'LLM (shadow)':18s}")
    print("-" * 82)
    for rec in records:
        title = rec.input.get("title", "")
        rule_out = rec.rule_output or rec.output
        llm_out = rec.llm_output or "-"
        print(f"{title:40s}  {rule_out:18s}  {llm_out:18s}")

    # Apply a paired-proportion test over these records (McNemar's
    # exact for small samples; normal-approx for large) to decide
    # when the LLM is ready to advance to Phase.LLM_PRIMARY.
    print()
    print(f"Stored {len(records)} outcome records with paired (rule, llm) predictions.")
