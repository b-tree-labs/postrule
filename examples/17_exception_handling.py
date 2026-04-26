# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Using a Dendra switch as an exception-handling dispatcher.

Run: `python examples/17_exception_handling.py`

A try/except tree that picks among retry, fallback, escalate,
and drop is a classifier — input is ``(exception, context)``,
output is one of a fixed strategy set. Wrapping the dispatch in
a :class:`LearnedSwitch` records every decision on the outcome
log and lets a learned policy graduate against the hand-written
rule once enough outcomes accumulate.

Day 0 (RULE)
    Hand-written dispatch on exception type + HTTP status.
    Conservative defaults — always retry on 5xx, never retry on
    4xx, escalate ``RuntimeError`` to the queue.

Day N (MODEL_SHADOW → ML_PRIMARY)
    As outcomes accumulate (did the retry succeed? did the
    fallback produce an acceptable answer?), the ML head learns
    endpoint-specific patterns the rule can't see:
    "endpoint X's 503s clear in 2 s; endpoint Y's are permanent"
    or "this auth error on tenant Z is actually a billing
    suspension, route to CS." The rule remains the floor (paper
    §7.1) — a buggy learned policy cannot remove the
    "503 → retry" baseline.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from dendra import LearnedSwitch, Verdict


@dataclass
class FailureContext:
    """Everything a classifier sees about a failure."""

    exception_type: str
    http_status: int | None = None
    endpoint: str = ""
    attempt: int = 1
    elapsed_ms: float = 0.0


# The four strategies the classifier chooses between. Each is a
# distinct post-failure action. "retry" means loop + backoff;
# "fallback" means route to cache/default; "escalate" means push
# onto the ops queue; "drop" means log and continue (non-critical).
STRATEGIES = ["retry", "fallback", "escalate", "drop"]


def handling_rule(ctx: FailureContext) -> str:
    """Day-0 exception-handling dispatch.

    Keyword-matched on exception type + HTTP status. Conservative
    by design — anything ambiguous escalates.
    """
    # Transient server-side — retry bounded.
    if ctx.http_status in (502, 503, 504) and ctx.attempt < 3:
        return "retry"
    # Client-side auth / permission — not retryable; needs a human.
    if ctx.http_status in (401, 403):
        return "escalate"
    # Validation errors on submitted data — the caller's problem.
    if ctx.exception_type == "ValueError":
        return "drop"
    # Anything timing-out — fall back to a cached answer rather than
    # burning more budget.
    if ctx.exception_type == "TimeoutError":
        return "fallback"
    # Runtime / programmer errors — don't retry; a human should look.
    if ctx.exception_type in ("RuntimeError", "KeyError", "AttributeError"):
        return "escalate"
    # Default: one-shot retry then give up.
    return "retry" if ctx.attempt < 2 else "escalate"


def main() -> None:
    # Dispatch table: each strategy has a real action. In production
    # these would call your retry loop, cache lookup, pager queue, etc.
    def do_retry(ctx: FailureContext) -> str:
        return f"requeued {ctx.endpoint} (attempt {ctx.attempt + 1})"

    def do_fallback(ctx: FailureContext) -> str:
        return f"served cached response for {ctx.endpoint}"

    def do_escalate(ctx: FailureContext) -> str:
        return f"pushed {ctx.endpoint} + {ctx.exception_type} to ops queue"

    def do_drop(ctx: FailureContext) -> str:
        return f"logged {ctx.exception_type} and continued"

    sw = LearnedSwitch(
        rule=handling_rule,
        labels={
            "retry": do_retry,
            "fallback": do_fallback,
            "escalate": do_escalate,
            "drop": do_drop,
        },
        auto_advance=False,  # deterministic example output
    )

    # Simulated failure stream. In production this feeds off your
    # real exception traffic — a decorator on your HTTP client, a
    # middleware on your message handler, an ``except`` block that
    # calls ``sw.dispatch(ctx)`` instead of inlining its own ladder.
    failures = [
        FailureContext("HTTPError", http_status=503, endpoint="/api/v1/users", attempt=1),
        FailureContext("HTTPError", http_status=401, endpoint="/api/v1/billing", attempt=1),
        FailureContext("TimeoutError", endpoint="/api/v1/search", attempt=1, elapsed_ms=5000),
        FailureContext("ValueError", endpoint="/api/v1/parse", attempt=1),
        FailureContext("RuntimeError", endpoint="/api/v1/internal", attempt=1),
        FailureContext("HTTPError", http_status=504, endpoint="/api/v1/export", attempt=3),
    ]

    print("Day 0: rule-based exception handling")
    print("-" * 78)
    for ctx in failures:
        result = sw.dispatch(ctx)
        print(
            f"  {ctx.exception_type:14s} "
            f"status={str(ctx.http_status or '-'):>4s}  "
            f"attempt={ctx.attempt} "
            f"-> {result.label:9s}  {result.action_result}"
        )

    # As outcomes accumulate, reviewers label whether each strategy
    # actually worked. Simulated here; in production, a downstream
    # signal (did the retry succeed? did the escalated ticket get
    # resolved as a real bug?) feeds record_verdict.
    print("\nLabeling outcomes (simulated downstream signal)...")
    records = sw.storage.load_records(sw.name)
    for r in records:
        # Toy oracle: retries on 5xx worked; escalations on 4xx worked;
        # fallbacks on timeouts worked; drops on ValueError worked.
        # Everything else is "incorrect" — the rule overreached.
        ctx = r.input
        rule_pick = r.label
        ok = (
            (rule_pick == "retry" and ctx.http_status in (502, 503, 504))
            or (rule_pick == "escalate" and ctx.http_status in (401, 403))
            or (rule_pick == "fallback" and ctx.exception_type == "TimeoutError")
            or (rule_pick == "drop" and ctx.exception_type == "ValueError")
            or (rule_pick == "escalate" and ctx.exception_type in
                ("RuntimeError", "KeyError", "AttributeError"))
        )
        sw.record_verdict(
            input=ctx,
            label=rule_pick,
            outcome=Verdict.CORRECT.value if ok else Verdict.INCORRECT.value,
            source="downstream-signal",
        )

    status = sw.status()
    print(
        f"outcome log: total={status.outcomes_total}, "
        f"correct={status.outcomes_correct}, "
        f"incorrect={status.outcomes_incorrect}"
    )
    print(
        "\nNext step (not shown here): as the log grows, an ML head "
        "trained on (ctx → correct_strategy) pairs graduates into\n"
        "MODEL_SHADOW / MODEL_PRIMARY. The rule stays as the safety "
        "floor; the learned policy fires only when the evidence gate\n"
        "confirms it beats the rule head-to-head with p < 0.05. See "
        "examples/06_ml_primary.py for the full lifecycle."
    )


if __name__ == "__main__":
    # Seed for reproducible output.
    random.seed(0)
    main()
