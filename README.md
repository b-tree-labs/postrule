<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/logo/dendra-wordmark-horizontal-dark.svg">
  <img src="brand/logo/dendra-wordmark-horizontal.svg" alt="Dendra" width="420">
</picture>

**Drop a rule. Drop a verifier. Watch your classifier get smarter automatically.**

```python
from dendra import ml_switch, default_verifier

@ml_switch(
    labels=["bug", "feature_request", "question"],
    verifier=default_verifier(),  # auto-detects Ollama → OpenAI → Anthropic
)
def triage(ticket: dict) -> str:
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"
```

That's the whole setup. Every classification gets routed through
the verifier automatically. Verdicts feed the outcome log. The
McNemar gate decides when the LLM (or a learned ML head) has
earned the front seat. The rule stays as the safety floor —
forever, behind a circuit breaker that auto-reverts on ML
failure.

**No reviewer queues. No labeled-data prerequisite. No manual
`mark_correct()` calls scattered through your code.** Drop the
verifier and Dendra's autonomous mode does the rest.

## What this replaces

Every production system has classification decisions — routing
a ticket, classifying an intent, selecting a retrieval strategy,
screening an output for PII, dispatching an exception to retry
vs escalate vs drop. They start as hand-written rules because
no training data exists on day one. Outcome data accumulates,
but the rules stay frozen because migrating each site to ML is
bespoke engineering at every decision point — and "we should ML
this" tickets sit in backlogs forever because nobody has the
time to build the migration scaffolding.

Dendra is the migration scaffolding. Six lifecycle phases (rule
→ LLM-shadow → LLM → ML-shadow → ML), a paired-McNemar
statistical gate at every transition, the rule retained as a
safety floor with a circuit breaker, and an autonomous-
verification default so the gate has evidence to evaluate
without you wiring a reviewer queue.

## Install

```bash
pip install dendra
```

Zero hard runtime dependencies. Optional extras: `train`
(scikit-learn), `bench` (HuggingFace datasets), `viz` (matplotlib),
`openai` / `anthropic` / `ollama` adapters. Python 3.10+.

Runnable examples in [`examples/`](./examples/) — each file is
self-contained (no API keys, no external services) and walks one
concept end-to-end.

## The six phases

| Phase | Decision-maker | Learning component | Safety floor |
|---|---|---|---|
| `RULE` | Your rule | — | Rule (self) |
| `MODEL_SHADOW` | Your rule | LLM predicts, no effect on decision | Rule |
| `MODEL_PRIMARY` | LLM if confident | Rule fallback on low conf / LLM failure | Rule |
| `ML_SHADOW` | LLM (or rule) | ML head trains, no effect | Rule |
| `ML_WITH_FALLBACK` | ML if confident | Rule fallback | Rule |
| `ML_PRIMARY` | ML | — | Rule (circuit breaker only) |

Advance between phases when the configured gate decides the
higher-tier classifier is reliably better than the current one.
The default gate (`McNemarGate`) is the paired-proportion
statistical test bounding the probability of a worse-than-rule
transition by its Type-I error rate. `AccuracyMarginGate`,
`MinVolumeGate`, `CompositeGate`, and `ManualGate` ship too;
any object satisfying the `Gate` protocol works.

## Autoresearch + agent loops

The dirty secret of LLM-driven autoresearch loops is the last
mile: the loop generates good candidate classifiers, and the
deployment story is duct tape.

`CandidateHarness` is the production substrate. Wrap a live
switch, register candidates, shadow them against production, get
paired-McNemar verdicts on whether each candidate beats the live
decision. The autoresearch loop reads `report.recommend_promote`;
the rule floor protects production from bad proposals throughout.

```python
from dendra import CandidateHarness, LearnedSwitch

sw = LearnedSwitch(rule=production_rule, ...)

harness = CandidateHarness(
    switch=sw,
    truth_oracle=labeled_validation_lookup,
    alpha=0.05,
)

# Autoresearch loop iteration:
candidate = autoresearch_agent.propose_next(sw.storage)
harness.register("v3", candidate)
harness.observe_batch(eval_traffic)
report = harness.evaluate("v3")

if report.recommend_promote:
    autoresearch_agent.commit_candidate(candidate)
```

> **Autoresearch tells you what to try.**
> **Dendra tells you when it worked.**

Full walkthrough in [`docs/autoresearch.md`](docs/autoresearch.md);
runnable end-to-end loop in
[`examples/19_autoresearch_loop.py`](examples/19_autoresearch_loop.py).

## CLIs

```bash
# Find classification sites in any codebase (static AST scan).
dendra analyze ./my-repo --format markdown --project-savings

# Wrap a target function with @ml_switch (AST injection, no typos).
dendra init src/triage.py:triage_ticket --author "@triage:support"

# Reproduce the four-benchmark transition-curve measurements.
dendra bench banking77

# Render a Figure 1-style plot from benchmark output.
dendra plot results/atis.jsonl -o figure-1.png

# Self-measured ROI report from production outcome logs.
dendra roi runtime/dendra/
```

## What's measured

Four public NLU benchmarks, end-to-end with paired McNemar's
test on per-example correctness:

| Benchmark | Labels | Rule acc | ML final | Paired McNemar p | Transition depth |
|---|---:|---:|---:|---:|---:|
| ATIS | 26 | 70.0% | **88.7%** | 1.8e-33 | **≤ 250 outcomes** |
| HWU64 | 64 | 1.8% | **83.6%** | < 1e-260 | **≤ 250 outcomes** |
| Banking77 | 77 | 1.3% | **87.7%** | ≈ 0 | **≤ 250 outcomes** |
| CLINC150 | 151 | 0.5% | **81.9%** | ≈ 0 | **≤ 250 outcomes** |

Every benchmark clears paired statistical significance (p < 0.01)
at the **first** checkpoint of 250 labeled outcomes. Two days of
moderate production traffic, not six months. Reproducible:
`dendra bench atis` regenerates Figure 1 in seconds.

Measured latency (Apple M5 / Python 3.13 / macOS 26):

- **Phase 0 classify, default config:** 1.67 µs p50 / 2.42 µs p99
  (573k ops/sec). Auto-logs an UNKNOWN outcome record.
- Phase 0 classify, `auto_record=False`: 0.50 µs p50 / 0.67 µs p99
  (1.9M ops/sec). Pure routing.
- **`persist=True` classify (batched FileStorage, the production
  recommendation):** 33.8 µs p50 / 390 µs p99 (~30k ops/sec).
  Durable outcome log with a 50 ms crash window.
- `persist=True` classify (per-call fsync — explicit opt-in for
  regulated workloads): 195 µs p50 / 260 µs p99.
- Real ML head (TF-IDF + LR on ATIS): 105 µs p50.
- Local LLM (llama3.2:1b via Ollama): ~250 ms p50.

Raw numbers + JSONL benchmark data:
[`docs/working/v1-audit-benchmarks.md`](docs/working/v1-audit-benchmarks.md).
Regression-guard tests:
[`tests/test_latency_pinned.py`](tests/test_latency_pinned.py).

## Where truth comes from

Verdicts feed the outcome log and drive gate graduation. Dendra
ships five built-in `VerdictSource` implementations:

- `CallableVerdictSource` — any `(input, label) -> Verdict`
  callable. The escape hatch for downstream-signal oracles,
  business rules, pre-computed labels.
- `LLMJudgeSource` — single-LLM judge with a self-judgment bias
  guardrail (refuses construction when classifier and judge
  resolve to the same model — G-Eval / MT-Bench / Arena
  literature).
- `LLMCommitteeSource` — multi-LLM majority / unanimous
  aggregation. Async committee judging via `asyncio.gather` runs
  N judges in parallel.
- `WebhookVerdictSource` — POST to an external HTTP endpoint
  (CRM, fraud system, ticketing tool) that reports outcomes. All
  failure modes absorb to UNKNOWN.
- `HumanReviewerSource` — queue-backed human-in-the-loop. Pending
  queue drains to your reviewer tool; verdicts queue fills back.
  Subclass-friendly for Redis / SQS / Kafka backends.

Bulk ingestion primitives (`bulk_record_verdicts`,
`export_for_review` / `apply_reviews`,
`bulk_record_verdicts_from_source`) handle cold-start preload +
periodic reviewer round-trips. See
[`docs/verdict-sources.md`](docs/verdict-sources.md) for the
decision matrix.

## Async API

Every sync entry point has an `a`-prefixed coroutine peer:
`aclassify`, `adispatch`, `arecord_verdict`,
`abulk_record_verdicts`. Async LLM adapter siblings —
`OpenAIAsyncAdapter`, `AnthropicAsyncAdapter`,
`OllamaAsyncAdapter`, `LlamafileAsyncAdapter`. FastAPI / LangGraph
/ LlamaIndex callers can `await sw.aclassify(input)` directly.
Worked example in
[`examples/15_async_fastapi.py`](examples/15_async_fastapi.py)
and the parallel-committee benchmark in
[`examples/16_async_committee.py`](examples/16_async_committee.py)
(3× speedup on a 3-judge committee).

Full surface + interop contract: [`docs/async.md`](docs/async.md).

## Security properties

- **20-pattern jailbreak corpus:** 100% rule-floor preserved when
  the shadow LLM is configured to return the attacker-desired
  label at 0.99 confidence. Each payload is authentic sensitive
  content (ITAR, EXPORT_CONTROLLED, `classified:secret`,
  `samsung_internal` markers) concatenated with an injection
  attempt drawn from publicly-documented families (AgentDojo,
  InjecAgent, OWASP LLM Top-10). An env-gated live-provider
  sweep is available via `DENDRA_JAILBREAK_LIVE=1` for in-situ
  validation.
- **PII corpus:** rule-only classifier, mixed corpus (SSN, phone,
  email, CC, passport, AWS key, JWT, Bearer token, MRN, ICD-10,
  IBAN, DOB).
- **Circuit-breaker stress:** 100 consecutive ML failures →
  breaker trips once, stays tripped, only explicit operator
  reset restores ML routing. Breaker state persists across
  process restart when `persist=True`.
- **Adversarial-shadow latency:** shadow LLM hangs and throws →
  rule decision unblocked.
- **Path-traversal guard:** storage backends reject `..`,
  absolute paths, and any switch name that resolves outside its
  base path.
- **Redaction hook at the storage boundary:** `Storage(redact=fn)`
  scrubs PII before records hit disk — load-bearing for HIPAA /
  PII / export-controlled workloads.

See `tests/test_security.py`, `tests/test_security_benchmarks.py`,
`tests/test_security_guarantees.py`,
`tests/test_storage_hardening.py`, and
`tests/test_output_safety.py`.

## Output safety

The same primitive wraps classifications of LLM-*generated
output* before delivery to users. `safety_critical=True` refuses
construction at `Phase.ML_PRIMARY` — the rule floor cannot be
removed without a code change.

```python
from dendra import ml_switch, Phase

@ml_switch(
    labels=["safe", "pii", "toxic", "confidential"],
    starting_phase=Phase.RULE,
    safety_critical=True,
)
def classify_output(response: str) -> str:
    if _SSN.search(response) or _PHONE.search(response):
        return "pii"
    if any(m in response for m in _CONFIDENTIAL_MARKERS):
        return "confidential"
    ...
```

## LLM-as-teacher bootstrap

Zero historical labels? Deploy at `Phase.MODEL_PRIMARY`. The LLM
makes the decisions. Every classification writes an outcome
record. After 500–5,000 records, train a local ML head on those
LLM-labeled records, graduate to `Phase.ML_WITH_FALLBACK`, and
the hot path runs at sub-millisecond per call with zero token
cost on the 80%+ of traffic the ML handles confidently.

```python
from dendra.research import train_ml_from_llm_outcomes

used = train_ml_from_llm_outcomes(
    switch=triage.switch,
    ml_head=head,
    min_llm_outcomes=500,
)
```

See [`examples/07_llm_as_teacher.py`](examples/07_llm_as_teacher.py)
for a runnable demo.

## Project structure

```
src/dendra/
├── core.py           # LearnedSwitch, Phase, SwitchConfig, ClassificationRecord
├── decorator.py      # @ml_switch
├── gates.py          # Gate protocol + McNemar / AccuracyMargin / MinVolume / Composite / Manual
├── verdicts.py       # VerdictSource family — Callable / LLMJudge / LLMCommittee / Webhook / HumanReviewer
├── autoresearch.py   # CandidateHarness — production substrate for autoresearch loops
├── storage.py        # FileStorage (batched), SqliteStorage, ResilientStorage, BoundedInMemoryStorage
├── models.py         # OpenAI / Anthropic / Ollama / Llamafile adapters (sync + async siblings)
├── ml.py             # MLHead protocol + sklearn default head
├── wrap.py           # AST-based @ml_switch injector (`dendra init`)
├── analyzer.py       # Static classification-site finder (`dendra analyze`)
├── research.py       # Transition-curve runner, paired-test helpers
├── roi.py            # Self-measured ROI report (`dendra roi`)
├── viz.py            # Figure rendering + McNemar p-values
├── telemetry.py      # Emitter protocol + shipped emitters
├── benchmarks/       # Public-benchmark loaders + reference rules
└── cli.py            # `dendra` CLI entry point

tests/                # 473 tests passing, 4 skipped (require optional extras)
docs/
├── autoresearch.md          # Production substrate for autoresearch loops
├── async.md                 # Async API + interop contract
├── api-reference.md         # Full public API
├── getting-started.md       # Mental model + first 30 minutes
├── storage-backends.md      # Backend matrix + custom-backend recipe
├── verdict-sources.md       # Decision matrix + bias-guardrail rationale
├── FAQ.md                   # Top questions
├── papers/2026-when-should-a-rule-learn/   # Paper outline + results + bibliography
└── integrations/SKILL.md    # Claude Code skill
```

## Paper

*"When Should a Rule Learn? Transition Curves for Safe
Rule-to-ML Graduation"* — published on arXiv. Outline +
reproducible benchmark results at
[`docs/papers/2026-when-should-a-rule-learn/`](docs/papers/2026-when-should-a-rule-learn/).
Annotated bibliography of related work at
[`related-work-bibliography.md`](docs/papers/2026-when-should-a-rule-learn/related-work-bibliography.md).

## Licensing

Dendra is split-licensed:

- **Client SDK** (what you `import` — decorator, config, storage,
  adapters, telemetry, viz, benchmarks, gates, verdicts,
  autoresearch): **Apache License 2.0**. Free for any commercial
  use.
- **Dendra-operated components** (analyzer, ROI reporter,
  research/graduation tooling, CLI, hosted surfaces): **Business
  Source License 1.1** with Change Date **2030-05-01** (auto-
  conversion to Apache 2.0). Additional Use Grant: **production
  self-hosted use is permitted** — the BSL only prohibits
  offering a competing hosted Dendra service.

See [`LICENSE.md`](LICENSE.md) for the split map and
[`LICENSING.md`](LICENSING.md) for developer-facing Q&A.
Per-file headers declare the specific license for each source
file. Commercial licensing that removes the BSL restrictions is
available — contact `licensing@b-treeventures.com`.

The underlying classification primitive is covered by a filed
US provisional patent (application pending, filed 2026-04-21).

## Status

**v1.0.0** — public release.
Six lifecycle phases ✓ Paired-McNemar gates ✓
Native async API ✓ VerdictSource family ✓
CandidateHarness for autoresearch loops ✓
473 tests passing.

Hosted analyzer + dashboards (Wave 2) — Q3 2026, waitlist on
[dendra.dev](https://dendra.dev).

## Dev setup

```bash
git clone https://github.com/axiom-labs-os/dendra.git
cd dendra
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,train,bench,viz]'
pytest tests/
```

## Contact

- GitHub: <https://github.com/axiom-labs-os/dendra>
- Maintainer: Benjamin Booth — `ben@b-treeventures.com`
- Axiom Labs: the commercial vehicle behind Dendra (a B-Tree
  Ventures, LLC DBA).

---

_Copyright © 2026 B-Tree Ventures, LLC (dba Axiom Labs).
Split-licensed — Apache 2.0 on the client SDK, BSL 1.1 on
Dendra-operated components; see `LICENSE.md`. "Dendra",
"Transition Curves", and "Axiom Labs" are trademarks of
B-Tree Ventures, LLC._
