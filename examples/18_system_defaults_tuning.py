# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Using a Dendra switch to tune system defaults post-install.

Run: `python examples/18_system_defaults_tuning.py`

Cache TTLs, retry policies, batch sizes, timeout ceilings,
queue priorities are all bucketed classification decisions
("what TTL for this response?"). Routing the decision through a
:class:`LearnedSwitch` records each choice on the outcome log;
once the system's own signals (cache hit vs stale, retry
success vs permanent failure) feed back as verdicts, an ML head
can graduate against the shipped rule on the operator's actual
workload.

The pattern:

1. Ship the system with a hand-written rule (Phase.RULE) — the
   same defaults everyone gets at install time.
2. Every decision routes through the Dendra switch, so the rule
   is the live picker on day 0 and every choice is recorded.
3. Outcomes come from the system's own signals (cache hit
   useful or stale, timeout abandoned vs completed, batch size
   vs backpressure).
4. As the log grows, an ML head trained on those outcomes
   graduates against the rule under the evidence gate.

This example walks HTTP-response cache-TTL selection — the rule
picks one of four TTL buckets; outcomes score whether the chosen
TTL led to stale reads or good reuse.
"""

from __future__ import annotations

from dataclasses import dataclass

from dendra import LearnedSwitch, Verdict


@dataclass
class HTTPResponseContext:
    """What the cache layer sees for a given response."""

    endpoint: str
    content_type: str
    size_bytes: int
    cache_control: str  # "public", "private", "no-store", ""
    has_etag: bool


# Four TTL buckets — a discrete classification problem rather
# than a continuous regression.
TTL_BUCKETS = ["short_30s", "medium_5min", "long_1h", "no_cache"]


def default_ttl_rule(ctx: HTTPResponseContext) -> str:
    """Ship-with-these defaults. The author's best guess at install time."""
    if ctx.cache_control == "no-store":
        return "no_cache"
    if "application/json" in ctx.content_type and "api" in ctx.endpoint:
        return "short_30s"  # API JSON — assume volatile
    if ctx.has_etag and ctx.size_bytes > 100_000:
        return "long_1h"  # large static asset with ETag
    if "image/" in ctx.content_type or "font/" in ctx.content_type:
        return "long_1h"
    return "medium_5min"


def main() -> None:
    sw = LearnedSwitch(
        rule=default_ttl_rule,
        labels=TTL_BUCKETS,  # list form — no actions attached; caller dispatches
        auto_advance=False,  # deterministic example output
    )

    # Simulated workload: a mix of responses the installed system sees.
    traffic = [
        # (context, was_the_chosen_ttl_actually_good?)
        # A "good" outcome means: the cached entry was re-used within
        # its TTL (hit), and wasn't served past staleness. A "bad"
        # outcome means either served-stale or evicted-before-reuse.
        (HTTPResponseContext("/api/v1/prices", "application/json", 800, "public", False), False),
        (HTTPResponseContext("/api/v1/catalog", "application/json", 50_000, "public", True), True),
        (HTTPResponseContext("/static/logo.png", "image/png", 12_000, "public", True), True),
        (HTTPResponseContext("/api/v1/session", "application/json", 200, "private", False), True),
        (HTTPResponseContext("/api/v1/prices", "application/json", 800, "public", False), False),
        (HTTPResponseContext("/api/v1/catalog", "application/json", 50_000, "public", True), True),
        (
            HTTPResponseContext(
                "/assets/app.js", "application/javascript", 280_000, "public", True
            ),
            True,
        ),
        (
            HTTPResponseContext("/api/v1/billing", "application/json", 1_200, "no-store", False),
            True,
        ),
        (HTTPResponseContext("/api/v1/feed", "application/json", 6_000, "public", False), False),
    ]

    print("Day 0: rule-driven TTL selection")
    print("-" * 78)
    for ctx, _ in traffic:
        result = sw.classify(ctx)
        print(f"  {ctx.endpoint:25s} ct={ctx.content_type:24s} -> {result.label}")

    # The installed system now records operational outcomes —
    # whether the TTL the rule picked actually matched reality.
    # "correct" means the cache entry was re-used before it expired
    # and served fresh data. "incorrect" means served-stale or wasted.
    print("\nFeeding operational outcomes back into the switch...")
    for ctx, good in traffic:
        result = sw.classify(ctx)
        sw.record_verdict(
            input=ctx,
            label=result.label,
            outcome=Verdict.CORRECT.value if good else Verdict.INCORRECT.value,
            source="cache-layer-metrics",
        )

    status = sw.status()
    acc = status.outcomes_correct / status.outcomes_total if status.outcomes_total else 0.0
    print(f"outcome log: {status.outcomes_total} rows, rule-agreement={acc:.1%}")

    # What the operator gets:
    #
    # 1. Immediate signal: "my shipped rule is X% right on my
    #    workload." The rule-agreement rate above is a health metric
    #    you can surface in your admin console.
    #
    # 2. Path to improvement: once enough records accumulate,
    #    train an ML head on (ctx → correct_ttl) pairs. Point the
    #    switch at it, move to MODEL_SHADOW; it observes without
    #    affecting decisions. Call `sw.advance()` when the gate
    #    says the learned policy outperforms the rule with p < 0.05.
    #
    # 3. Safety: the rule stays as the floor. A regression in the
    #    learned policy can never push the cache into a worse state
    #    than the author's day-0 default; the circuit breaker
    #    (ML_PRIMARY phase) flips back automatically on excessive
    #    error rates.
    #
    # 4. Auditability: every TTL decision is on tape with its
    #    rationale. "Why did we set TTL=30s on /api/v1/prices last
    #    Tuesday?" is a one-line query against the outcome log.

    print(
        "\nThe outcome log now holds the evidence an ML head needs to "
        "graduate against the rule.\nSee examples/06_ml_primary.py for "
        "the full lifecycle."
    )


if __name__ == "__main__":
    main()
