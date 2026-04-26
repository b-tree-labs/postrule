# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Bulk verdict ingestion — cold-start preload + periodic reviewer queue.

Run: `python examples/10_bulk_verdict_ingestion.py`

Two workflows:

1. **Cold-start preload.** You have a month of historical
   tickets, pre-labeled in your existing CRM. Feed them all
   through the switch in one call so the outcome log has
   signal from day one — no need to wait for production traffic
   to accumulate before the first gate evaluation can fire.

2. **Periodic reviewer queue.** Production traffic auto-logs
   UNKNOWN records. Once a shift, a human reviewer pulls
   ``export_for_review()``, labels the queue in their tool,
   and pushes the annotated reviews back via ``apply_reviews()``.
   The switch's outcome log grows with verified verdicts the
   gate can use for graduation math.

Both paths go through :meth:`LearnedSwitch.bulk_record_verdicts`
— one storage call instead of N, auto-advance fires at most
once at end-of-batch rather than on every interval boundary the
batch crosses.
"""

from __future__ import annotations

from dendra import (
    BulkVerdict,
    LearnedSwitch,
    Verdict,
    ml_switch,
)


@ml_switch(
    labels=["bug", "feature_request", "question"],
    # auto_advance off so the example's output is deterministic.
    auto_advance=False,
)
def triage_rule(ticket: dict) -> str:
    """Route a ticket into one of the three labels."""
    title = (ticket.get("title") or "").lower()
    if "crash" in title or "error" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


def main() -> None:
    sw: LearnedSwitch = triage_rule.switch

    # --- Pattern 1: cold-start preload from a labeled history ----------------
    print("Pattern 1: cold-start preload\n")
    history = [
        ({"title": "app crashes on login"}, "bug"),
        ({"title": "add dark mode"}, "feature_request"),
        ({"title": "how do I export data?"}, "question"),
        ({"title": "ui error on save"}, "bug"),
        ({"title": "please add CSV export"}, "feature_request"),
    ]
    preload = [
        BulkVerdict(
            input=ticket,
            label=label,
            outcome=Verdict.CORRECT.value,
            source="crm-export",
            confidence=1.0,
        )
        for ticket, label in history
    ]
    summary = sw.bulk_record_verdicts(preload)
    print(f"  recorded={summary.recorded}, failed={summary.failed}")
    print(f"  outcome log now holds {len(sw.storage.load_records(sw.name))} records")

    # --- Pattern 2: live traffic + periodic reviewer queue -------------------
    print("\nPattern 2: live traffic + reviewer round-trip\n")
    live_tickets = [
        {"title": "notifications are noisy"},
        {"title": "dark mode crashes on Windows"},
        {"title": "can you add SAML?"},
        {"title": "search is slow"},
    ]
    for t in live_tickets:
        r = triage_rule.classify(t)  # auto_record=True → UNKNOWN row lands
        print(f"  classified: {t['title']:45s} -> {r.label}")

    # Later: a human reviewer pulls the UNKNOWN queue.
    queue = sw.export_for_review(limit=10)
    print(f"\n  reviewer queue has {len(queue)} UNKNOWN rows ready for labeling")

    # Reviewer does their thing — attaches outcome + optional label override.
    reviewed = [
        {**queue[0], "outcome": Verdict.CORRECT.value},
        {**queue[1], "outcome": Verdict.INCORRECT.value, "label": "bug"},
        {**queue[2], "outcome": Verdict.CORRECT.value},
        # queue[3] intentionally not reviewed this shift.
    ]
    s = sw.apply_reviews(reviewed)
    print(f"  applied {s.recorded} reviews, {s.failed} skipped (unmatched).")

    # The log now holds: preloaded CORRECTs + live UNKNOWNs + reviewer verdicts.
    total = len(sw.storage.load_records(sw.name))
    print(f"\nfinal outcome-log size: {total} records")


if __name__ == "__main__":
    main()
