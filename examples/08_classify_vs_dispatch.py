# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""classify() vs dispatch() — two verbs, two contracts.

Run: `python examples/08_classify_vs_dispatch.py`

- ``classify(input)`` is **pure**: returns a ClassificationResult,
  never fires the label's ``on=`` callable. Safe from tests,
  benchmarks, dashboards.
- ``dispatch(input)`` classifies AND fires the matched handler.
  Exceptions inside the handler are captured on the result
  (``action_raised``), not propagated — the classification
  decision survives handler bugs.
"""

from __future__ import annotations

from postrule import ClassificationResult, ml_switch


def send_to_engineering(ticket: dict) -> str:
    """Action fired when the classifier returns ``label="bug"``."""
    return f"engineering ← {ticket['title']}"


def send_to_support(ticket: dict) -> str:
    """Action fired when the classifier returns ``label="question"``.

    Raises on the poison-pill ticket to demonstrate graceful capture.
    """
    if "poison" in ticket["title"].lower():
        raise RuntimeError("support API unavailable")
    return f"support ← {ticket['title']}"


def send_to_product(ticket: dict) -> str:
    """Action fired when the classifier returns ``label="feature_request"``."""
    return f"product ← {ticket['title']}"


@ml_switch(
    labels={
        "bug": send_to_engineering,
        "question": send_to_support,
        "feature_request": send_to_product,
    },
)
def triage_rule(ticket: dict) -> str:
    """Classify one ticket into bug / feature_request / question."""
    heading = (ticket.get("title") or "").lower()
    if "crash" in heading or "error" in heading:
        return "bug"
    if heading.endswith("?"):
        return "question"
    return "feature_request"


def _print_dispatch(ticket: dict, result: ClassificationResult) -> None:
    """Format one dispatch outcome so humans and agents can read it
    and act — when the action raised, point at the remedy inline."""
    header = f"{ticket['title']:45s}  →  label={result.label:18s}"
    if result.action_raised:
        print(header + "  →  action=DISPATCH_FAILED")
        print(f"    handler raised:  {result.action_raised}")
        print("    remedy:          catch the exception inside your `on=`")
        print("                     callable, or set `on=None` to disable")
        print("                     dispatch for this label.")
    elif result.action_result is not None:
        print(header + f"  →  action={result.action_result}")
    else:
        print(header + "  →  (no action registered for this label)")


if __name__ == "__main__":
    happy = [
        {"title": "app crashes on login"},
        {"title": "how do I reset my password?"},
        {"title": "add dark mode"},
    ]

    print("\n── classify() is pure — no handlers fire ──────────────────────────────")
    for case in happy:
        outcome = triage_rule.classify(case)
        print(
            f"  {case['title']:43s}  →  "
            f"label={outcome.label:18s}  action_result={outcome.action_result}"
        )

    print("\n── dispatch() — classify AND fire the action ──────────────────────────")
    for case in happy:
        _print_dispatch(case, triage_rule.dispatch(case))

    print("\n── dispatch() with a failing handler — graceful capture ───────────────")
    # Poison-pill routes to 'question' → send_to_support raises.
    # Postrule captures; the classification decision is preserved; the
    # caller can branch on action_raised to retry / log / escalate.
    poison = {"title": "poison: is support even online?"}
    outcome = triage_rule.dispatch(poison)
    _print_dispatch(poison, outcome)

    if outcome.action_raised:
        print()
        print("Programmatic fallback (the caller chose this):")
        print(
            f"  would have retried label={outcome.label!r} out-of-band, but not today — skipping."
        )

    print()
