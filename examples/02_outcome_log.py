# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Record outcomes and read them back.

Run: `python examples/02_outcome_log.py`

Dendra's value starts accumulating the moment you record outcomes.
Each call to `record_outcome()` writes a structured record into the
configured storage — here we use `InMemoryStorage` for easy
inspection; a production deployment would use `FileStorage` or a
custom backend.
"""

from __future__ import annotations

from dendra import InMemoryStorage, LearnedSwitch, Outcome


def rule(ticket: dict) -> str:
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    return "feature_request"


if __name__ == "__main__":
    storage = InMemoryStorage()
    switch = LearnedSwitch(
        name="triage",
        rule=rule,
        author="@triage:outcome-log",
        storage=storage,
    )

    cases = [
        ({"title": "app crashes on login"}, Outcome.CORRECT),
        ({"title": "add dark mode"}, Outcome.CORRECT),
        ({"title": "error in checkout flow"}, Outcome.CORRECT),
        # A mislabel — rule thinks "crash" is always a bug, even if
        # the ticket is actually a user question about crash reports.
        ({"title": "can I download my crash reports?"}, Outcome.INCORRECT),
    ]

    for ticket, ground_truth in cases:
        result = switch.classify(ticket)
        switch.record_outcome(
            input=ticket,
            output=result.output,
            outcome=ground_truth.value,
        )
        title = ticket["title"]
        pred = result.output
        print(f"{title:45s}  predicted={pred:18s}  actual={ground_truth.name}")

    print()
    records = storage.load_outcomes(switch.name)
    print(f"Recorded {len(records)} outcomes.")
    correct = sum(1 for r in records if r.outcome == Outcome.CORRECT.value)
    print(f"Rule accuracy so far: {correct}/{len(records)}  ({correct / len(records):.0%})")
