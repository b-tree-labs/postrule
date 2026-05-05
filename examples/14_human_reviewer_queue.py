# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""HumanReviewerSource — queue-backed human-in-the-loop verdict source.

Run: `python examples/14_human_reviewer_queue.py`

The shape: a classifier decides, a human reviews, the verdict
lands in the audit log. Two queues in the middle:

- ``pending``: the classifier pushes ``(input, label)`` tuples.
  The reviewer's tool (Slack bot, web UI, CLI) pops from this
  and presents the row to a human.
- ``verdicts``: the reviewer's tool pushes the resulting verdict
  onto this. ``judge()`` blocks on it (with a timeout), then
  returns.

For v1 the queues are stdlib ``queue.Queue`` (in-process). For
production, subclass :class:`HumanReviewerSource` and override
``_push`` / ``_pop_verdict`` to route through Redis, SQS, Kafka,
or your reviewer tool's own webhook.

Timeout is the safety net: if no reviewer is on shift, ``judge()``
returns :class:`Verdict.UNKNOWN` after ``timeout`` seconds rather
than blocking the classifier indefinitely.
"""

from __future__ import annotations

import queue
import threading
import time

from dendra import Verdict
from dendra.verdicts import HumanReviewerSource


def _fake_reviewer_loop(
    pending: queue.Queue,
    verdicts: queue.Queue,
    stop: threading.Event,
) -> None:
    """Stand-in for a real reviewer tool. Pops from ``pending``,
    pretends to consult a human, pushes a verdict."""
    while not stop.is_set():
        try:
            input_obj, label = pending.get(timeout=0.1)
        except queue.Empty:
            continue
        time.sleep(0.02)  # reviewer is reading the row
        title = str(input_obj.get("title", "")).lower()
        # Toy rule: reviewer agrees on obvious bugs; unsure on crashes-
        # that-are-actually-questions; disagrees on spam.
        if "crash" in title and "?" not in title:
            verdicts.put(Verdict.CORRECT)
        elif "?" in title:
            verdicts.put(Verdict.UNKNOWN)
        else:
            verdicts.put(Verdict.INCORRECT)


def main() -> None:
    pending: queue.Queue = queue.Queue()
    verdicts: queue.Queue = queue.Queue()
    stop = threading.Event()

    reviewer_thread = threading.Thread(
        target=_fake_reviewer_loop,
        args=(pending, verdicts, stop),
        daemon=True,
    )
    reviewer_thread.start()

    src = HumanReviewerSource(
        pending=pending,
        verdicts=verdicts,
        timeout=2.0,
        name="ops-team",
    )
    print(f"source stamp: {src.source_name}\n")

    tickets = [
        {"title": "app crashes on login"},
        {"title": "can I download my crash reports?"},
        {"title": "buy my product!"},
    ]
    for t in tickets:
        verdict = src.judge(t, "bug")
        print(f"  {t['title']:42s} -> {verdict.value}")

    stop.set()
    reviewer_thread.join(timeout=1.0)


if __name__ == "__main__":
    main()
