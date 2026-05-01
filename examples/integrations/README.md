# Agent-harness integrations

Self-contained examples showing how to wrap a classification or
routing decision in your existing stack with Dendra's `@ml_switch`.
Each file:

- runs offline (the framework imports are optional — falls back to
  a deterministic stub when the dependency isn't installed),
- shows a real-feeling production scenario (intent routing, ticket
  classification, RAG strategy selection, agent tool-use, output
  safety),
- explains the per-call cost / latency story before and after the
  paired-McNemar gate fires.

| Stack | What gets wrapped | File | Stack role |
|---|---|---|---|
| **LangChain** | Agent routing decision (which chain handles this query?) | [`langchain_triage.py`](langchain_triage.py) | orchestration framework |
| **LlamaIndex** | Retrieval-strategy selection (vector / BM25 / hybrid / graph / summary) | [`llamaindex_router.py`](llamaindex_router.py) | RAG orchestration |
| **LiteLLM** | Universal LLM-classifier call site | [`litellm_classify.py`](litellm_classify.py) | LLM proxy |
| **NousResearch Hermes** | Tool-selection in a function-calling loop | [`hermes_tool_use.py`](hermes_tool_use.py) | open-weights model + protocol |
| **Axiom OS** | Local-LM safety classifier through `axi serve` | [`axiom_local_lm.py`](axiom_local_lm.py) | local-LM runtime |

## Two integration shapes

The five examples cluster into two patterns:

1. **Wrap the routing decision** — LangChain / LlamaIndex. The
   orchestration framework already has a discrete "which path?"
   choice. Wrapping it lets Dendra graduate the picker without
   touching the downstream chains/retrievers.

2. **Wrap the LLM call site** — LiteLLM / Hermes / Axiom. The LLM
   call itself returns a label. Wrapping that call replaces it with
   a local sklearn head once the gate fires; the framework stays.

Frameworks shipping post-launch (v1.1): CrewAI, AutoGen, DSPy,
Instructor, Haystack. The wrap shape is one of the two above for
each — open an issue if you want to pull a specific one forward.

## Running

Each file is self-contained:

```bash
python examples/integrations/langchain_triage.py
python examples/integrations/llamaindex_router.py
python examples/integrations/litellm_classify.py
python examples/integrations/hermes_tool_use.py
python examples/integrations/axiom_local_lm.py
```

All run offline; install the matching framework to swap the offline
stub for the real call path.

## What you'll see

Each example prints:

- The classifications/routings it produced for a handful of inputs
- The wrapped switch's `status()` (current phase, outcomes
  accumulated)
- The per-call cost trajectory before / after graduation

## Adding your stack

Send a PR with `examples/integrations/<your_framework>_<scenario>.py`
following the same shape. The bar:

- Single classifier / router function decorated with `@ml_switch`
- Offline stub fallback (no required network for the example to run)
- One-paragraph cost-trajectory story in the closing print block

We'll add it to the table above.
