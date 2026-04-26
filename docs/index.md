# Dendra docs

Welcome. Dendra is a graduated-autonomy classification primitive
for production Python systems. Pick the entry point that matches
where you are:

## Start here

- **[Getting started](getting-started.md)** — mental model + your
  first 30 minutes. Begin here if you're new.
- **[FAQ](FAQ.md)** — top questions, with honest answers.

## Reference

- **[API reference](api-reference.md)** — full public surface,
  every method, every parameter.
- **[Storage backends](storage-backends.md)** — `BoundedInMemoryStorage`,
  `FileStorage`, `SqliteStorage`, `ResilientStorage`. Decision
  matrix + custom-backend recipe.
- **[Verdict sources](verdict-sources.md)** — `CallableVerdictSource`,
  `JudgeSource`, `JudgeCommittee`, `WebhookVerdictSource`,
  `HumanReviewerSource`. Decision matrix + bias-guardrail rationale.
- **[Async API](async.md)** — `aclassify` / `adispatch` /
  parallel-committee judging. FastAPI / LangGraph / LlamaIndex
  interop.
- **[Threat model](THREAT_MODEL.md)** — trust boundaries, threats
  considered + mitigations, audit-chain integrity claims, what's
  out of scope. Written for enterprise pre-checks.

## Deep dives

- **[Autoresearch loops](autoresearch.md)** —
  using `CandidateHarness` to gate proposals from a language-model-driven
  loop with head-to-head significance tests before they land
  in production.
- **[Paper](papers/2026-when-should-a-rule-learn/)** — *"When
  should a rule learn? A statistical framework for graduated ML
  autonomy"* — the academic anchor. Includes outline,
  reproducible JSONL benchmarks, Figure 1, paired-McNemar
  results across 4 NLU benchmarks.
- **[Annotated bibliography](papers/2026-when-should-a-rule-learn/related-work-bibliography.md)** —
  related work tiered MUST / SHOULD / NICE.

## By audience

### Production ML engineers

You have a hand-written rule classifier in production and a
backlog ticket that says "we should ML this." The ticket
doesn't move because replacing the rule is risky.

→ Start with [getting-started.md](getting-started.md), then
walk through [`examples/01_hello_world.py`](../examples/01_hello_world.py)
through [`examples/06_ml_primary.py`](../examples/06_ml_primary.py)
to see the full lifecycle.

### Agent / autoresearch builders

Your loop generates good candidates and your deployment story is
duct tape.

→ Start with [autoresearch.md](autoresearch.md), then
[`examples/19_autoresearch_loop.py`](../examples/19_autoresearch_loop.py)
for the end-to-end loop.

### Compliance / regulated industries

You need an audit chain on every classification + redaction at
the storage boundary + a circuit breaker that survives process
restart.

→ Storage redaction hook in
[storage-backends.md § "Redaction hook"](storage-backends.md).
Architectural rule-floor guarantee in `examples/03_safety_critical.py`.
Bulk reviewer round-trip in
[`examples/10_bulk_verdict_ingestion.py`](../examples/10_bulk_verdict_ingestion.py).

## Examples gallery

The shortest path to a working install. Every file is
self-contained, runs without API keys, and walks one concept.

| # | What it shows |
|---|---|
| [`01_hello_world.py`](../examples/01_hello_world.py) | Smallest complete example. |
| [`02_outcome_log.py`](../examples/02_outcome_log.py) | `persist=True` + ground-truth verdicts. |
| [`03_safety_critical.py`](../examples/03_safety_critical.py) | Architectural rule-floor guarantee. |
| [`04_llm_shadow.py`](../examples/04_llm_shadow.py) | language model observes, rule decides. |
| [`05_output_safety.py`](../examples/05_output_safety.py) | Same primitive on model output. |
| [`06_ml_primary.py`](../examples/06_ml_primary.py) | End-state ML decisions + circuit breaker. |
| [`07_llm_as_teacher.py`](../examples/07_llm_as_teacher.py) | Cold-start with model-labeled outcomes. |
| [`08_classify_vs_dispatch.py`](../examples/08_classify_vs_dispatch.py) | The two verbs. |
| [`09_verdict_webhook.py`](../examples/09_verdict_webhook.py) | Async verdict ingestion. |
| [`10_bulk_verdict_ingestion.py`](../examples/10_bulk_verdict_ingestion.py) | Bulk preload + reviewer round-trip. |
| [`11_llm_judge.py`](../examples/11_llm_judge.py) | model judge with self-judgment guardrail. |
| [`12_llm_committee.py`](../examples/12_llm_committee.py) | Multi-model committee aggregation. |
| [`13_webhook_verdicts.py`](../examples/13_webhook_verdicts.py) | External-endpoint verdict source. |
| [`14_human_reviewer_queue.py`](../examples/14_human_reviewer_queue.py) | Queue-backed human-in-the-loop. |
| [`15_async_fastapi.py`](../examples/15_async_fastapi.py) | FastAPI integration. |
| [`16_async_committee.py`](../examples/16_async_committee.py) | `asyncio.gather` over model judges. |
| [`17_exception_handling.py`](../examples/17_exception_handling.py) | Dendra as a try/except-tree replacement. |
| [`18_system_defaults_tuning.py`](../examples/18_system_defaults_tuning.py) | Post-install tuning of system defaults. |
| [`19_autoresearch_loop.py`](../examples/19_autoresearch_loop.py) | End-to-end autoresearch + Dendra. |

## Help + community

- **GitHub issues:** <https://github.com/axiom-labs-os/dendra/issues>
- **Email:** ben@b-treeventures.com
- **Hosted-beta waitlist:** [dendra.dev](https://dendra.dev)

## License

- **Client SDK:** Apache 2.0. Free for any commercial use.
- **Hosted analyzer / server / dashboards:** BSL 1.1 with Change
  Date 2030-05-01. Production self-hosted use is permitted by
  the license; competing-hosted-service is prohibited.

See [LICENSE.md](../LICENSE.md) and [LICENSING.md](../LICENSING.md)
for the split map and Q&A.
