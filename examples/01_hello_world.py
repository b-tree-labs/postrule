# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""Postrule hello-world — rule + dispatch in the smallest form.

Run: `python examples/01_hello_world.py`

Pass ``labels=`` as a dict mapping label → action, call
``rule.dispatch(input)``, done. Each dict entry is a
**label-based conditional expression** — "when the classifier's
output equals this label, evaluate this action."
"""

from __future__ import annotations

from postrule import ml_switch


def send_to_engineering(ticket: dict) -> str:
    """Action fired when the classifier returns ``label="bug"``."""
    return f"engineering ← {ticket['title']}"


def send_to_product(ticket: dict) -> str:
    """Action fired when the classifier returns ``label="feature_request"``."""
    return f"product ← {ticket['title']}"


def send_to_support(ticket: dict) -> str:
    """Action fired when the classifier returns ``label="question"``."""
    return f"support ← {ticket['title']}"


@ml_switch(
    labels={
        "bug": send_to_engineering,
        "feature_request": send_to_product,
        "question": send_to_support,
    },
    # `author` omitted — auto-derives to "@__main__:triage_rule".
)
def triage_rule(ticket: dict) -> str:
    """Classify one ticket into exactly one of the declared labels."""
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
    for sample in samples:
        c = triage_rule.dispatch(sample)
        print(f"{sample['title']:40s}  →  label={c.label:18s}  →  action={c.action_result}")
