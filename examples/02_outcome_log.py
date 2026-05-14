# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""Record outcomes and read them back — durable across restarts.

Run: `python examples/02_outcome_log.py`

``persist=True`` wraps a ``FileStorage`` in ``ResilientStorage`` so
outcomes survive process restart and feed later phase-transition
analysis, ROI reports, and the dashboard. Records land under
``./runtime/postrule/<name>/outcomes.jsonl``.

For scratch workflows, omit ``persist`` — the default
``BoundedInMemoryStorage`` keeps the most recent 10 000 records
per switch.
"""

from __future__ import annotations

from postrule import LearnedSwitch, Verdict


def triage_rule(ticket: dict) -> str:
    """Classify one ticket into bug / feature_request."""
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    return "feature_request"


if __name__ == "__main__":
    switch = LearnedSwitch(rule=triage_rule, persist=True)

    # Each case pairs an input with the ground-truth verdict — the
    # label a human reviewer (or a downstream signal) would assign
    # after seeing the classification. It is NOT the rule's
    # prediction; the rule's prediction is compared against this
    # ground truth when we record the outcome below. Phase
    # graduation, drift detection, and ROI estimation all read off
    # the outcome log.
    cases = [
        ({"title": "app crashes on login"}, Verdict.CORRECT),
        ({"title": "add dark mode"}, Verdict.CORRECT),
        ({"title": "error in checkout flow"}, Verdict.CORRECT),
        # Rule assumes "crash" → bug; reality is a user question.
        ({"title": "can I download my crash reports?"}, Verdict.INCORRECT),
    ]

    for case, ground_truth in cases:
        result = switch.classify(case)
        switch.record_verdict(
            input=case,
            label=result.label,
            outcome=ground_truth.value,
        )
        print(f"{case['title']:45s}  predicted={result.label:18s}  actual={ground_truth.name}")

    print()
    records = switch.storage.load_records(switch.name)
    print(f"Recorded {len(records)} outcomes  (persisted to ./runtime/postrule/{switch.name}/).")
    correct = sum(1 for r in records if r.outcome == Verdict.CORRECT.value)
    print(f"Rule accuracy so far: {correct}/{len(records)}  ({correct / len(records):.0%})")
