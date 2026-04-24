# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""FastAPI integration sketch — `await sw.aclassify(...)` in a route.

Run (after ``pip install fastapi uvicorn``):

    uvicorn examples.15_async_fastapi:app --reload

The point: on an async web framework (FastAPI, Starlette,
Hypercorn), `sw.classify(x)` would burn a threadpool worker per
request. `await sw.aclassify(x)` hands the event loop back during
any underlying I/O (storage writes, LLM calls) and cooperates
naturally with other in-flight requests.

Dendra doesn't hard-depend on FastAPI — this file only imports it
inside the module-level try/except so the example is still
inspectable without the extra install.
"""

from __future__ import annotations

import sys

try:
    from fastapi import FastAPI
except ImportError:
    print(
        "This example requires FastAPI. Install with:\n"
        "  pip install fastapi uvicorn\n"
        "Then run:\n"
        "  uvicorn examples.15_async_fastapi:app --reload",
        file=sys.stderr,
    )
    raise SystemExit(1)

from dendra import LearnedSwitch, SwitchConfig, Verdict, ml_switch


@ml_switch(
    labels=["bug", "feature_request", "question"],
    # persist=True routes to the batched FileStorage + ResilientStorage
    # default — async classify + durable outcome log on one event loop,
    # ~33 µs p50 per classify.
    config=SwitchConfig(auto_record=True, auto_advance=True),
)
def triage_rule(ticket: dict) -> str:
    title = (ticket.get("title") or "").lower()
    if "crash" in title or "error" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"


sw: LearnedSwitch = triage_rule.switch
app = FastAPI(title="Dendra FastAPI example")


@app.post("/classify")
async def classify_endpoint(ticket: dict) -> dict:
    """Classify a ticket. Non-blocking under the async event loop."""
    result = await sw.aclassify(ticket)
    return {
        "label": result.label,
        "source": result.source,
        "confidence": result.confidence,
        "phase": result.phase.value,
    }


@app.post("/verdict/{input_hash}")
async def verdict_endpoint(input_hash: str, review: dict) -> dict:
    """Apply a reviewer-produced verdict back into the outcome log.

    The caller pairs the ``/classify`` return with a separate
    verdict decision (from a ticketing tool, a reviewer UI, etc.)
    and POSTs it here. ``apply_reviews`` matches by input_hash.
    """
    review["input_hash"] = input_hash
    summary = sw.apply_reviews([review])
    return {
        "recorded": summary.recorded,
        "failed": summary.failed,
    }


@app.get("/status")
def status_endpoint() -> dict:
    """Sync is still fine for quick reads — no event-loop hop needed."""
    s = sw.status()
    return {
        "phase": s.phase.value,
        "outcomes_total": s.outcomes_total,
        "outcomes_correct": s.outcomes_correct,
        "circuit_breaker_tripped": s.circuit_breaker_tripped,
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True}
