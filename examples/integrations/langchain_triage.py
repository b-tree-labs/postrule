# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Dendra + LangChain — wrap an agent's routing decision.

LangChain's pattern: an LLM picks one of N tools / retrievers / paths
based on the user query. The "which path?" decision is exactly what
Dendra graduates: today it's an LLM (slow, expensive, drifts); after
~250 logged outcomes a paired-McNemar gate fires and an in-process
sklearn head replaces the LLM for the easy cases. The LangChain
pipeline downstream of the routing decision doesn't change.

Pattern: wrap the routing function; Dendra logs every (input, label)
the LLM produces; the gate decides when the head is good enough to
take over. The rule (whatever fallback you want) is the safety floor.

Run: ``python examples/integrations/langchain_triage.py``
LangChain optional — falls back to a stub so the example runs offline.
"""

from __future__ import annotations

from dendra import ml_switch

# LangChain is optional; fall back to a stub so this file is runnable
# without the dependency installed. Production users have it.
try:
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI

    _HAS_LANGCHAIN = True
except ImportError:
    _HAS_LANGCHAIN = False


_PROMPT = """You are a routing layer. Classify the user query into ONE of:
- "billing"     — invoices, charges, refunds, payment methods
- "technical"   — bugs, errors, integration issues, "how do I"
- "account"     — login, password, 2FA, profile changes
- "other"       — anything else

User query: {query}
Reply with the single label only."""


def _llm_router(query: str) -> str:
    """LLM-backed routing. Real cost: ~$0.0015/call on Sonnet 4.6."""
    if not _HAS_LANGCHAIN:
        # Demo stub: deterministic keyword fallback.
        q = query.lower()
        if any(k in q for k in ("invoice", "charge", "refund")):
            return "billing"
        if any(k in q for k in ("error", "bug", "integration")):
            return "technical"
        if any(k in q for k in ("login", "password", "2fa")):
            return "account"
        return "other"
    chain = ChatPromptTemplate.from_template(_PROMPT) | ChatOpenAI(
        model="gpt-5-mini", temperature=0
    )
    return chain.invoke({"query": query}).content.strip().lower()


# Wrap the routing decision. At Phase.RULE the call is just the LLM
# (no behavior change). Once 250+ outcomes accumulate and the gate
# fires, the in-process ML head replaces the LLM for the easy cases;
# the rule remains the safety floor for everything the head punts on.
@ml_switch(
    labels=["billing", "technical", "account", "other"],
    author="@your-team:support-routing",
)
def route_query(query: str) -> str:
    return _llm_router(query)


# ---- Downstream LangChain handlers (unchanged after graduation) -----------


def _handle_billing(query: str) -> str:
    return f"[billing chain] {query}"


def _handle_technical(query: str) -> str:
    return f"[technical chain — RAG over docs] {query}"


def _handle_account(query: str) -> str:
    return f"[account chain] {query}"


_HANDLERS = {
    "billing": _handle_billing,
    "technical": _handle_technical,
    "account": _handle_account,
}


def respond(query: str) -> str:
    label = route_query(query)
    return _HANDLERS.get(label, lambda q: f"[fallback] {q}")(query)


if __name__ == "__main__":
    samples = [
        "Why was I charged $99 twice this month?",
        "Getting a 500 error when calling the embeddings endpoint",
        "How do I enable 2FA?",
        "What's the airspeed of an unladen swallow?",
    ]
    print("Routing decisions (Phase.RULE — LLM still primary):")
    for q in samples:
        print(f"  {route_query(q):>10s}  ←  {q}")
    print()
    status = route_query.status()
    print(
        f"Switch '{status.name}' phase={status.phase} "
        f"outcomes={status.outcomes_total}"
    )
    print()
    print(
        "After ~250 outcomes the gate fires; sklearn head replaces the LLM\n"
        "for the easy cases. ~$1,200/mo savings at 1M routes/mo on Sonnet 4.6."
    )
