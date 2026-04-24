# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Async verdict ingestion — feedback arriving from outside the process.

Run: `python examples/09_verdict_webhook.py`

In production, the classification happens in one place and the
verdict often arrives somewhere else — a human reviewer
resolving a ticket hours later, a downstream service detecting
that a routing was wrong, a Slack slash-command, a CRM callback.

This example simulates an async verdict source (a
``queue.Queue`` populated by a fake "reviewer" thread) and
shows three ingestion patterns:

1. Direct ``switch.record_verdict(...)`` — the plain method.
2. ``switch.verdict_for(input)`` as a try/except-scoped context.
3. ``on_verdict=`` hook mirroring verdicts to an external audit
   sink.

Real deployments would wire ``record_verdict`` into a FastAPI /
Flask endpoint, a Kafka consumer, a Slack handler — anywhere
outside-the-process feedback arrives.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

from dendra import Verdict, ml_switch

# A tiny audit log — in real systems this would be an append-only
# database table, an S3 bucket, a Kafka topic.
AUDIT_LOG: list[dict[str, Any]] = []


def mirror_to_audit(record) -> None:
    """on_verdict hook: mirror every verdict into an external store."""
    AUDIT_LOG.append(
        {
            "timestamp": record.timestamp,
            "input": record.input,
            "label": record.label,
            "outcome": record.outcome,
            "source": record.source,
        }
    )


@ml_switch(
    labels=["bug", "feature_request", "question"],
    on_verdict=mirror_to_audit,
)
def triage_rule(ticket: dict) -> str:
    """Classify a ticket into one of the three labels."""
    title = (ticket.get("title") or "").lower()
    if "crash" in title or "error" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


def fake_reviewer_loop(
    pending: queue.Queue,
    verdicts: queue.Queue,
    stop_event: threading.Event,
) -> None:
    """A background thread standing in for an async feedback source.

    Pulls (ticket, label) pairs off ``pending``, sleeps briefly to
    simulate human-review latency, then emits a verdict onto
    ``verdicts`` for the main loop to consume.
    """
    while not stop_event.is_set():
        try:
            ticket, label = pending.get(timeout=0.1)
        except queue.Empty:
            continue
        time.sleep(0.02)  # "reviewer is reading the ticket"
        # Toy truth: the reviewer agrees with the rule on obvious
        # bugs and feature requests; disagrees on ambiguous tickets
        # about crashes-that-are-actually-questions.
        title = ticket.get("title", "").lower()
        if "crash report" in title and label == "bug":
            outcome = Verdict.INCORRECT
        else:
            outcome = Verdict.CORRECT
        verdicts.put((ticket, label, outcome))


if __name__ == "__main__":
    pending: queue.Queue = queue.Queue()
    verdicts: queue.Queue = queue.Queue()
    stop = threading.Event()

    reviewer = threading.Thread(
        target=fake_reviewer_loop,
        args=(pending, verdicts, stop),
        daemon=True,
    )
    reviewer.start()

    # --- Pattern 1: direct record_verdict from the main loop.
    print("Pattern 1: direct record_verdict\n")
    tickets = [
        {"title": "app crashes on login"},
        {"title": "add dark mode"},
        {"title": "how do I export data?"},
        {"title": "can I download my crash reports?"},  # ambiguous
    ]
    for ticket in tickets:
        result = triage_rule.classify(ticket)
        pending.put((ticket, result.label))
        print(f"  classified: {ticket['title']:45s} → {result.label}")

    # Drain verdicts as they arrive (simulated async feedback).
    collected = 0
    while collected < len(tickets):
        try:
            ticket, label, outcome = verdicts.get(timeout=1.0)
        except queue.Empty:
            break
        triage_rule.record_verdict(
            input=ticket, label=label, outcome=outcome.value
        )
        collected += 1
        print(f"  verdict:    {ticket['title']:45s} → {outcome.name}")

    # --- Pattern 2: fluent .mark_*() on the result.
    print("\nPattern 2: fluent .mark_*() on the result\n")
    result = triage_rule.classify({"title": "payment page is broken"})
    result.mark_correct()
    print(f"  {result.label}: marked correct inline")

    # --- Pattern 3: context manager with default-on-exception.
    print("\nPattern 3: verdict_for() context manager\n")
    with triage_rule.switch.verdict_for({"title": "add CSV export"}) as v:
        try:
            # Simulate the handler doing some work that may fail...
            _ = v.result.label
            v.correct()
            print(f"  {v.result.label}: handler succeeded, marked correct")
        except RuntimeError:
            # Replace with your real downstream-handler exception type.
            v.incorrect()

    # --- Show the audit log populated by the on_verdict hook.
    print(f"\nAudit-log mirror captured {len(AUDIT_LOG)} verdicts:")
    for entry in AUDIT_LOG:
        print(f"  {entry['outcome']:>10s}  label={entry['label']}  input={entry['input']['title']}")

    stop.set()
    reviewer.join(timeout=1.0)
