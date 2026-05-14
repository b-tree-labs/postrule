# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""Postrule + LiteLLM — wrap a classifier-style LLM call.

LiteLLM is the universal proxy: one ``completion(...)`` call goes to
any provider. The most common production use case is a classifier
(intent routing, content moderation, severity tagging) where the LLM
returns one of N labels. That's the call site Postrule wraps.

Pattern: wrap the classifier function; LiteLLM stays as the actual
LLM client; Postrule logs the calls and eventually graduates the easy
cases off LLM entirely. Per-call cost drops from ~$0.0015 (Sonnet 4.6)
to ~$0.000003 (in-process sklearn head) on graduated traffic.

Run: ``python examples/integrations/litellm_classify.py``
LiteLLM optional — falls back to a stub so the example runs offline.
"""

from __future__ import annotations

from postrule import ml_switch

try:
    from litellm import completion

    _HAS_LITELLM = True
except ImportError:
    _HAS_LITELLM = False


_SYSTEM_PROMPT = """You classify customer-support tickets. Reply with
exactly one of: bug, feature_request, question, billing.
"""


def _llm_classify(ticket_title: str) -> str:
    """LiteLLM-backed classification. Default model: claude-haiku-4-5."""
    if not _HAS_LITELLM:
        # Offline stub — keyword rules.
        t = ticket_title.lower()
        if any(k in t for k in ("crash", "error", "broken", "500")):
            return "bug"
        if any(k in t for k in ("can you add", "would be nice", "feature")):
            return "feature_request"
        if any(k in t for k in ("invoice", "charge", "refund", "billing")):
            return "billing"
        return "question"
    resp = completion(
        model="claude-haiku-4-5",  # any LiteLLM-supported model works
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": ticket_title},
        ],
        temperature=0,
        max_tokens=10,
    )
    return resp.choices[0].message.content.strip().lower()


@ml_switch(
    labels=["bug", "feature_request", "question", "billing"],
    author="@your-team:ticket-classify",
)
def classify_ticket(ticket_title: str) -> str:
    return _llm_classify(ticket_title)


if __name__ == "__main__":
    tickets = [
        "App keeps crashing when uploading a file",
        "Can you add dark mode for the dashboard",
        "How do I export my data?",
        "I was charged twice for my subscription this month",
        "500 error when calling /api/v1/invoices",
    ]
    print("Ticket classifications (Phase.RULE — Haiku still primary):")
    for t in tickets:
        print(f"  {classify_ticket(t):>16s}  ←  {t}")
    print()
    status = classify_ticket.status()
    print(f"Switch '{status.name}' phase={status.phase} outcomes={status.outcomes_total}")
    print()
    print(
        "Cost trajectory at 1M tickets/mo on claude-haiku-4-5 ($0.0005/call):\n"
        "  pre-graduation:   ~$500/mo through LiteLLM\n"
        "  post-graduation:  ~$3/mo (sklearn head, ~$3 per million calls)\n"
        "  net savings:      ~$497/mo per wrapped classifier."
    )
