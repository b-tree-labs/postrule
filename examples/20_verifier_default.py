# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Configuring a language model verifier so verdicts are recorded automatically.

Run: `python examples/20_verifier_default.py`

Pass a verdict source via ``verifier=``. Every ``classify()``
call runs the verifier on the (input, label) pair and records
a verdict-bearing row directly. No manual ``record_verdict()``
call is needed.

Production swap-ins for the verifier (any one works):

    from dendra import default_verifier
    verifier = default_verifier()
    # Auto-detects a running local Ollama; raises if not reachable.

    from dendra import JudgeSource, OllamaAdapter
    verifier = JudgeSource(OllamaAdapter(model="qwen2.5:7b"))

    from dendra import JudgeSource, OpenAIAdapter
    verifier = JudgeSource(OpenAIAdapter(model="gpt-4o-mini"))

The example uses ``FakeJudgeLM`` from ``_stubs.py`` to keep
the demo offline-runnable.

What ``verifier=`` buys you, in one line each:

- *Time to first verdict:* < 1 s per classify with the
  shipped default vs hours-to-weeks for reviewer queues or
  downstream signals.
- *Faster graduation:* roughly 5–10× speedup on
  calendar-time-to-``ML_PRIMARY`` over typical reviewer-
  queue verdict rates.
- *Format compliance of the shipped default:* 97% on
  Dendra's verdict task (n=30,
  ``docs/benchmarks/slm-verifier-results.md``). Failures
  log as ``UNKNOWN``; the rule floor is unaffected.

Situations where each of these is most impactful — and
where they aren't — are in ``docs/scenarios.md``.
"""

from __future__ import annotations

from _stubs import FakeJudgeLM

from dendra import LearnedSwitch, JudgeSource, Verdict


def production_rule(ticket: dict) -> str:
    """The classifier you already have."""
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


def main() -> None:
    sw = LearnedSwitch(
        rule=production_rule,
        labels=["bug", "feature_request", "question"],
        verifier=JudgeSource(FakeJudgeLM()),
        auto_advance=False,  # deterministic demo output
    )

    samples = [
        {"title": "app crashes on login"},
        {"title": "how do I export data?"},
        {"title": "add dark mode"},
        {"title": "feature: bulk-upload UI"},
    ]
    for ticket in samples:
        result = sw.classify(ticket)
        print(f"  {ticket['title']:40s} -> {result.label}")

    # Each row carries an outcome from the verifier rather than the
    # UNKNOWN auto-log entry that fires when no verifier is set.
    print("\nOutcome log:")
    for r in sw.storage.load_records(sw.name):
        marker = "✓" if r.outcome == Verdict.CORRECT.value else "✗"
        print(
            f"  {marker} input={r.input['title']:32s} "
            f"label={r.label:18s} outcome={r.outcome:9s} source={r.source}"
        )


if __name__ == "__main__":
    main()
