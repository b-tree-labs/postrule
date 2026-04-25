# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Autonomous-verification — drop a rule, drop a verifier, done.

Run: `python examples/20_verifier_default.py`

The biggest adoption barrier in graduated-classifier systems is
the implicit expectation that *somebody* will supply verdicts —
human reviewers, downstream signals, labeled validation queues.
For most teams adopting a new library, that's a non-starter.

This example shows the autonomous mode: pass a verifier, every
classification gets routed through it automatically, verdict-
bearing records land in the outcome log, the McNemar gate
graduates phases on accumulated evidence. **No reviewer queues.
No labeled-data prerequisite. No manual ``mark_correct()``.**

Production swap-ins for the verifier (any one works):

    from dendra import default_verifier
    verifier = default_verifier()
    # Local-only by default — auto-detects Ollama, raises if not running.

    from dendra import LLMJudgeSource, OllamaAdapter
    verifier = LLMJudgeSource(OllamaAdapter(model="llama3.2:3b"))
    # Explicit local. Zero cost, no API key, privacy-preserving.

    from dendra import LLMJudgeSource, OpenAIAdapter
    verifier = LLMJudgeSource(OpenAIAdapter(model="gpt-4o-mini"))
    # Cloud. Faster gate graduation; costs tokens.

This file uses ``FakeLLMJudge`` from ``_stubs.py`` so the demo
runs offline. The fake's specific output values are NOT load-
bearing for the teaching point — they're deterministic so the
example output reproduces.
"""

from __future__ import annotations

from _stubs import FakeLLMJudge

from dendra import LearnedSwitch, LLMJudgeSource, SwitchConfig, Verdict


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
        labels=["bug", "feature_request", "question"],
        config=SwitchConfig(
            verifier=LLMJudgeSource(FakeLLMJudge()),
            # Off here so the demo output is reproducible.
            # Production: defaults are fine — the gate fires every
            # 500 verdicts and you don't think about it.
            auto_advance=False,
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

    # The outcome log is verdict-bearing (CORRECT / INCORRECT),
    # not UNKNOWN — the verifier supplied the verdict on every call.
    print("\nOutcome log:")
    for r in sw.storage.load_records(sw.name):
        marker = "✓" if r.outcome == Verdict.CORRECT.value else "✗"
        print(
            f"  {marker} input={r.input['title']:32s} "
            f"label={r.label:18s} outcome={r.outcome:9s} source={r.source}"
        )


if __name__ == "__main__":
    main()
