# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Autonomous-verification default — `pip install`, drop a verifier, done.

Run: `python examples/20_verifier_default.py`

The biggest adoption barrier in graduated-classifier systems is
the implicit expectation that *somebody* will supply verdicts —
human reviewers, downstream signals, labeled validation queues.
For most teams adopting a new library, that's a non-starter.

This example shows the autonomous mode: pass a verifier, and
every classification gets routed through it automatically. No
manual ``mark_correct()``. No reviewer queues. The verdict-
bearing record lands in the outcome log, the gate accumulates
evidence, the phase graduates when the McNemar gate clears it.

Three configurations:

1. **Auto-detected (recommended)** — ``verifier=default_verifier()``
   probes for a local Ollama, then OpenAI, then Anthropic,
   picking whichever is reachable. Single line, zero config.
2. **Explicit local SLM** — ``OllamaAdapter(model="llama3.2:1b")``.
   Zero cost, no API key, runs on your laptop.
3. **Explicit cloud** — ``OpenAIAdapter(model="gpt-4o-mini")``.
   Cheapest competent OpenAI judge.

The example uses a stub LLM so it runs without network or keys.
Swap the ``StubVerifier`` for any of the above to wire a real
LLM in.
"""

from __future__ import annotations

from dendra import (
    LearnedSwitch,
    LLMJudgeSource,
    ModelPrediction,
    SwitchConfig,
    Verdict,
)


# Stub LLM that always agrees with the rule. In production you'd
# pass an OllamaAdapter / OpenAIAdapter / AnthropicAdapter or
# call default_verifier() to auto-pick.
class _StubLLM:
    _model = "stub-judge-1.0"

    def classify(self, input, labels):
        # The judge sees a rendered prompt (string) containing
        # the original input + the classifier's label. Toy rule:
        # agree ("correct") when the prompt mentions "crash" or
        # ends with the question mark from the original input;
        # disagree ("incorrect") otherwise.
        text = str(input).lower()
        if "crash" in text or "how do i" in text:
            return ModelPrediction(label="correct", confidence=0.92)
        return ModelPrediction(label="incorrect", confidence=0.85)


def production_rule(ticket: dict) -> str:
    """The classifier you already have."""
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


def main() -> None:
    # The autonomous setup: rule + verifier. That's the whole
    # public surface most users need. The verdict-bearing rows
    # accumulate; the McNemar gate fires automatically when the
    # auto_advance_interval is reached.
    sw = LearnedSwitch(
        rule=production_rule,
        name="ticket_triage_autonomous",
        author="@examples:20",
        labels=["bug", "feature_request", "question"],
        config=SwitchConfig(
            verifier=LLMJudgeSource(_StubLLM()),
            # Optional: sample only some classifications. Use this
            # when the verifier is expensive (cloud LLM, large
            # committee) and you don't need full coverage for the
            # gate's statistical power.
            verifier_sample_rate=1.0,
            auto_advance=False,  # off for the demo's deterministic output
        ),
    )

    # Drop traffic in. Every classify() runs the verifier inline
    # and records a verdict-bearing row. No manual mark_correct().
    samples = [
        {"title": "app crashes on login"},
        {"title": "how do I export data?"},
        {"title": "add dark mode"},
        {"title": "feature: bulk-upload UI"},
    ]
    for ticket in samples:
        result = sw.classify(ticket)
        print(f"  {ticket['title']:40s} -> {result.label}")

    # Inspect the outcome log. The records are verdict-bearing
    # (CORRECT / INCORRECT), not UNKNOWN — the verifier supplied
    # the verdict on every call.
    print("\nOutcome log:")
    for r in sw.storage.load_records(sw.name):
        verdict_marker = "✓" if r.outcome == Verdict.CORRECT.value else "✗"
        print(
            f"  {verdict_marker} input={r.input['title']:32s} "
            f"label={r.label:18s} outcome={r.outcome:9s} source={r.source}"
        )

    # Production swap-in: replace _StubLLM with one of:
    #
    #   from dendra import default_verifier
    #   verifier = default_verifier()  # auto-detect Ollama → OpenAI → Anthropic
    #
    #   from dendra import OllamaAdapter, LLMJudgeSource
    #   verifier = LLMJudgeSource(OllamaAdapter(model="llama3.2:1b"))
    #
    #   from dendra import OpenAIAdapter, LLMJudgeSource
    #   verifier = LLMJudgeSource(OpenAIAdapter(model="gpt-4o-mini"))
    #
    # The rest of the code is unchanged.


if __name__ == "__main__":
    main()
