# Postrule examples

Runnable examples. Each file is self-contained — no external
services, no API keys — and targets a single conceptual piece of
the Postrule primitive.

```bash
pip install git+https://github.com/b-tree-labs/postrule
python examples/01_hello_world.py
```

New to Postrule? Start with [`01_hello_world.py`](./01_hello_world.py),
then skim [`../docs/api-reference.md`](../docs/api-reference.md) for
the minimum required vs optional surface.

## Gallery

| # | File | What it shows |
|---|---|---|
| 1 | [`01_hello_world.py`](./01_hello_world.py) | Smallest complete example: dict-labels + dispatch. |
| 2 | [`02_outcome_log.py`](./02_outcome_log.py) | `persist=True` + ground-truth verdicts; reading the log back. The outcome log feeds phase graduation, ROI, drift. |
| 3 | [`03_safety_critical.py`](./03_safety_critical.py) | `safety_critical=True` refuses construction in `Phase.ML_PRIMARY` — the rule floor is architecturally guaranteed. |
| 4 | [`04_llm_shadow.py`](./04_llm_shadow.py) | `Phase.MODEL_SHADOW`: rule decides, LLM observes; paired predictions land in the outcome log for later transition-gate analysis. Stub LLM (swap for `OpenAIAdapter` / `AnthropicAdapter` / `OllamaAdapter` / `LlamafileAdapter` in production). |
| 5 | [`05_output_safety.py`](./05_output_safety.py) | Same primitive on LLM *output* — PII / confidentiality gating. `list[str]` labels (no dispatch; caller handles). |
| 6 | [`06_ml_primary.py`](./06_ml_primary.py) | End-state: `Phase.ML_PRIMARY` with a healthy ML head deciding and the rule as circuit-breaker target. Part 2 simulates an ML failure → breaker trip → operator reset. |
| 7 | [`07_llm_as_teacher.py`](./07_llm_as_teacher.py) | Cold-start: start at `Phase.MODEL_PRIMARY` with zero labeled data, let the LLM label production traffic, then train a local ML head and graduate (operator-triggered). |
| 8 | [`08_classify_vs_dispatch.py`](./08_classify_vs_dispatch.py) | The two verbs: `classify()` pure (tests / dashboards); `dispatch()` classify + fire the handler. Includes the graceful-failure contract — a handler that raises is captured on `action_raised`, not propagated. |
| 9 | [`09_verdict_webhook.py`](./09_verdict_webhook.py) | Verdicts arriving async from outside the process (simulated reviewer thread feeding a queue). Shows the three ergonomic shapes — direct `record_verdict`, fluent `.mark_correct()`, and `verdict_for()` context manager — plus the `on_verdict=` mirror-to-audit hook. |
| 10 | [`10_bulk_verdict_ingestion.py`](./10_bulk_verdict_ingestion.py) | Two bulk workflows: cold-start preload from labeled history, and the reviewer-queue round-trip (`export_for_review` → label in your tool → `apply_reviews` back). Deferred auto-advance fires at most once at end-of-batch. |
| 11 | [`11_llm_judge.py`](./11_llm_judge.py) | `LLMJudgeSource` — single-LLM critic verdict source with the self-judgment bias guardrail. `require_distinct_from=` refuses construction when classifier and judge resolve to the same model (G-Eval / MT-Bench / Arena literature). |
| 12 | [`12_llm_committee.py`](./12_llm_committee.py) | `LLMCommitteeSource` — majority / unanimous / confidence-weighted aggregation across a committee of distinct LLM judges. Unanimous mode biases toward caution (any dissent → UNKNOWN) for expensive-false-positive workflows. |
| 13 | [`13_webhook_verdicts.py`](./13_webhook_verdicts.py) | `WebhookVerdictSource` — pull verdicts from an external HTTP endpoint (CRM, fraud system, ticketing tool). All failure modes absorb as UNKNOWN so a downstream outage never breaks the audit loop. |
| 14 | [`14_human_reviewer_queue.py`](./14_human_reviewer_queue.py) | `HumanReviewerSource` — queue-backed human-in-the-loop. `pending` queue drains to your reviewer tool; `verdicts` queue fills from it. Timeout → UNKNOWN so no reviewer on shift doesn't stall the classifier. |
| 15 | [`15_async_fastapi.py`](./15_async_fastapi.py) | FastAPI route with `await sw.aclassify(...)`, reviewer roundtrip via `apply_reviews`, and a sync `/status` handler. Shows sync + async on the same switch. Requires `pip install fastapi uvicorn`. |
| 16 | [`16_async_committee.py`](./16_async_committee.py) | `LLMCommitteeSource.ajudge` via `asyncio.gather` — committee latency is `max(judge_latency)`, not `sum`. Sequential-vs-parallel timing delta printed inline (3× on a 3-judge committee). |
| 17 | [`17_exception_handling.py`](./17_exception_handling.py) | Postrule as the try/except-tree replacement: exception-handling as classification. Rule picks retry / fallback / escalate / drop; outcomes feed back from downstream signals so the policy can learn endpoint-specific patterns the author couldn't hand-write. |
| 18 | [`18_system_defaults_tuning.py`](./18_system_defaults_tuning.py) | Post-install tuning: ship hand-written defaults, let the installed system re-derive them from its own workload. HTTP cache-TTL selection as the worked example — rule-agreement rate becomes a health metric; graduation upgrades defaults without shipping a new binary. |
| 19 | [`19_autoresearch_loop.py`](./19_autoresearch_loop.py) | `CandidateHarness` shadows candidate classifiers against production, runs head-to-head significance tests on the same inputs, and returns a per-candidate promotion recommendation. Drop-in for an LLM-driven autoresearch loop. Production rule at 55% accuracy ratchets to 100% across four loop iterations. |
| 20 | [`20_verifier_default.py`](./20_verifier_default.py) | `verifier=` configuration: every classification routes through an LLM judge automatically and the verdict-bearing record lands without a manual `record_verdict()` call. |
| 21 | [`21_tournament.py`](./21_tournament.py) | `Tournament` — round-robin head-to-head selection across N candidates. Same primitive readers can adapt to compare prompt variants, ML head architectures, retrieval strategies, scoring formulas. Unanimity short-circuit + full pairwise-report matrix. |

## On the roadmap

Not yet written — contributions welcome via
[`../CONTRIBUTING.md`](../CONTRIBUTING.md):

- End-to-end transition curve on a public benchmark (ATIS-class).
- `postrule roi` report from an accumulated outcome log.
- Integration with LangSmith / Weights & Biases telemetry.
- Vision / audio / multimodal adapters.
- FastAPI / LangGraph integration showcase (pairs with the
  native async API landing in v1).

## License

Examples are licensed under **Apache 2.0** (matching the client
SDK they exercise). The `SPDX-License-Identifier: Apache-2.0`
header at the top of each file declares this explicitly. You
can copy these files into your own projects without attribution
beyond the Apache 2.0 license terms.
