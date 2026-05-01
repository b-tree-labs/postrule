# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Dendra + LlamaIndex — wrap a retrieval-strategy selection.

LlamaIndex's strength: many ways to retrieve. Vector search, BM25,
hybrid, summary index, knowledge graph — each shines on different
query shapes. Most production setups have a router that picks the
right strategy per query, today usually with an LLM. That router is
exactly what Dendra graduates.

Pattern: wrap the strategy-picker. The downstream retrieval +
synthesis pipeline doesn't change; the picker eventually becomes a
local sklearn head trained on which strategy worked best per query
shape.

Run: ``python examples/integrations/llamaindex_router.py``
LlamaIndex optional — falls back to a stub so the example runs offline.
"""

from __future__ import annotations

from dendra import ml_switch

try:
    from llama_index.core import Settings, VectorStoreIndex  # noqa: F401
    from llama_index.core.query_engine import RouterQueryEngine  # noqa: F401

    _HAS_LLAMAINDEX = True
except ImportError:
    _HAS_LLAMAINDEX = False


_STRATEGY_PROMPT = """You select a retrieval strategy for the user's query.

Strategies:
- "vector"      — dense semantic search (best for fuzzy, paraphrased questions)
- "bm25"        — sparse keyword match (best for exact code/error/identifier matches)
- "hybrid"      — both; pick when the query has both intent and exact tokens
- "summary"     — read pre-computed document summaries (best for "what is X about?")
- "graph"       — traverse knowledge-graph relations (best for "how does X relate to Y?")

Query: {query}
Reply with the single strategy name only."""


def _llm_strategy_picker(query: str) -> str:
    """LLM-backed strategy selection. ~$0.0008/call on GPT-5 mini."""
    if not _HAS_LLAMAINDEX:
        # Demo stub — keyword heuristic.
        q = query.lower()
        if "what is" in q or "summarize" in q:
            return "summary"
        if "relate" in q or "connection" in q:
            return "graph"
        if any(t in q for t in ('"', "::", "_", "TypeError", "404")):
            return "bm25"
        if " and " in q and any(c.isupper() for c in query):
            return "hybrid"
        return "vector"
    # Production: call your LLM-backed strategy picker via LlamaIndex's
    # selectors module. Returning the same label set the wrap declares.
    raise NotImplementedError("wire your LlamaIndex selector here")


@ml_switch(
    labels=["vector", "bm25", "hybrid", "summary", "graph"],
    author="@your-team:rag-routing",
)
def select_strategy(query: str) -> str:
    return _llm_strategy_picker(query)


def retrieve(query: str) -> str:
    """Top-level retrieval. Dendra-wrapped strategy + LlamaIndex pipeline."""
    strategy = select_strategy(query)
    # In production, dispatch to the LlamaIndex query engine for that
    # strategy. Here we just narrate.
    return f"[retrieve via {strategy}] {query}"


if __name__ == "__main__":
    queries = [
        "What is the embedding model used in our docs index?",
        "Find every reference to TypeError 'Cannot read property foo' in the changelog",
        "How does the auth service relate to the billing service?",
        "Why might my deployment time out during cold start?",
        "Summarize the Q4 product roadmap document",
    ]
    print("Strategy selections (Phase.RULE — LLM picker still primary):")
    for q in queries:
        print(f"  {select_strategy(q):>8s}  ←  {q[:60]}")
    print()
    status = select_strategy.status()
    print(
        f"Switch '{status.name}' phase={status.phase} "
        f"outcomes={status.outcomes_total}"
    )
    print()
    print(
        "Once the gate fires, the strategy picker is in-process: <1 ms\n"
        "vs ~600 ms LLM round-trip. Improves p50 retrieval latency on every\n"
        "downstream RAG call."
    )
