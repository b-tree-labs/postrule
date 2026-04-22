# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Dendra hello-world — wrap a rule with `@ml_switch`.

Run: `python examples/01_hello_world.py`

Zero dependencies beyond dendra itself. Demonstrates the smallest
possible Dendra integration: wrap an existing rule function, call it,
observe that behavior is identical to the un-wrapped rule.
"""

from __future__ import annotations

from dendra import Phase, SwitchConfig, ml_switch


@ml_switch(
    labels=["bug", "feature_request", "question"],
    author="@triage:hello-world",
    config=SwitchConfig(phase=Phase.RULE),
)
def triage(ticket: dict) -> str:
    title = (ticket.get("title") or "").lower()
    if "crash" in title or "error" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


if __name__ == "__main__":
    samples = [
        {"title": "app crashes on login"},
        {"title": "how do I reset my password?"},
        {"title": "add dark mode"},
    ]
    for ticket in samples:
        label = triage(ticket)
        print(f"{ticket['title']:40s}  ->  {label}")
