<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/logo/postrule-wordmark-horizontal-dark.svg">
  <img src="brand/logo/postrule-wordmark-horizontal.svg" alt="Postrule" width="420">
</picture>

# Software that's smarter every month than the day you shipped it.

**Drop a rule. Drop a verifier. Watch your classifier get smarter automatically.**

```python
from postrule import ml_switch, default_verifier

@ml_switch(
    labels=["bug", "feature_request", "question"],
    verifier=default_verifier(),  # autonomous mode — see "What default_verifier does" below
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
evidence gate decides when the language model (or a learned ML head) has
earned the front seat. The rule stays as the safety floor —
forever, behind a circuit breaker that auto-reverts on ML
failure.

**No reviewer queues. No labeled-data prerequisite. No manual
`mark_correct()` calls scattered through your code.** Drop the
verifier and Postrule's autonomous mode does the rest.

> **What `default_verifier()` does on first call.** Lazy-downloads
> `qwen2.5:7b` (~4.7 GB) into your Ollama cache, then runs locally
> for every verdict — no API key, no per-call cost, nothing leaves
> the box. Three lighter paths in [Install](#install) if you'd
> rather skip the download: a smaller local model, a hosted
> provider via `JudgeSource(AnthropicAdapter())`, or omit the
> kwarg entirely and call `record_verdict()` yourself.

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

Postrule is the migration scaffolding. Six lifecycle phases (rule
→ model-shadow → model → ML-shadow → ML), a head-to-head evidence
gate at every transition (McNemar's exact test under the hood —
swappable), the rule retained as a safety floor with a circuit
breaker, and an autonomous-verification default so the gate has
evidence to evaluate without you wiring a reviewer queue.

## The bet

If your AI bill is more than $1M/yr and Postrule is in your stack,
that bill will be 30% smaller in 12 months. Public benchmark, or
you get every dollar of consulting back.

## Why I built this

Every system I've worked on with classification decisions in
production — ML at Uber, research-software work in nuclear
engineering at UT, and AI-using software at SoilMetrix —
repeated the same pattern: rules accumulate, larger models
earn their seat over the rule, but graduating decisions *off*
a larger model onto something smaller and cheaper needed a
formal evidence gate that nothing on the shelf provided. I
wrote Postrule because I needed it on my own systems. The
companion paper formalizes the gate (paired-McNemar at every
transition); the eight-benchmark suite stress-tests it.

— Ben Booth

## What graduation looks like

When a wrapped switch graduates, Postrule writes a markdown report
card alongside your other repo artifacts. The card is the launch
evidence — what the gate saw, when it fired, the cost trajectory,
the drift posture going forward.

Excerpt from `postrule/results/triage_rule.md` — a sample card
generated 2026-04-29 (full sample in
[`docs/sample-reports/triage_rule.md`](docs/sample-reports/triage_rule.md)):

> **Phase: `ML_PRIMARY`** — graduated 2026-04-25 at outcome 312.
> Gate (`McNemarGate`, α = 0.01) fired with p = **4.2 × 10⁻⁴**.
> Effect size: rule 78.4% → ML 87.2% (**+8.8 pp**).
> Cost per call: **$0.0042 → $0.000003** (99.93% reduction).
> Latency p50: **412 ms → 0.8 ms** (99.81% reduction).

Three commands produce the evidence trilogy:

- `postrule analyze --report` — initial-analysis discovery card. Which
  sites would graduate, projected savings, recommended order.
  ([sample](docs/sample-reports/_initial-analysis.md))
- `postrule report <switch>` — per-switch graduation card. Transition
  curve, p-value trajectory, cost trajectory, drift checks.
  ([sample](docs/sample-reports/triage_rule.md))
- `postrule report --summary` — project rollup. Cockpit view across
  every wrapped switch with phase distribution and aggregate
  reduction. ([sample](docs/sample-reports/_summary.md))

The cards are markdown — they live in your repo, diff in PRs, ship
in releases. **The report card *is* the audit trail**: the same
artifact is what your compliance team reads, what your CFO reads,
and what your engineers reads. No separate dashboard to wire up,
no SaaS lock-in for the evidence.

## Status & limitations

v1.0 ships the full decorator path (`@ml_switch`), the native `postrule.Switch` class authoring pattern, multi-arg signatures via auto-packing, full auto-lift across globals / `self.attr` / mid-function I/O / closures, drift detection, the prescriptive analyzer, the account system, the `propagate_action_exceptions` knob, and the MCP server. The classifier function returns a label name (string), not a structured value; tests, fixtures, validators, and order-dependent state machines are not classification sites; dynamic dispatch (`getattr` with runtime keys) requires explicit `@evidence_inputs` annotation; `eval` / `exec` is refused. Deep IDE plugins, A2A integration, and runtime AST mode are out of v1. The full list, version-tagged with the path forward for each item, lives in [`docs/limitations.md`](docs/limitations.md).

## Install

Three ways in. Pick whichever matches what you have on hand.

### A. Bring your own API key (fastest first verdict)

```bash
pip install postrule
export OPENAI_API_KEY=sk-...        # or ANTHROPIC_API_KEY=...
```
```python
from postrule import default_verifier
verifier = default_verifier(prefer="openai")     # or "anthropic"
```

Verdicts land in <1 s per classification. No local models, no
disk, no Ollama install. Recurring API cost.

### B. Bundled local model (privacy + offline-capable)

```bash
pip install postrule[bundled]
```
```python
from postrule.bundled import default_verifier_bundled, default_classifier
verifier = default_verifier_bundled()    # qwen2.5:7b, ~4.7 GB
model    = default_classifier()           # gemma2:2b, ~1.6 GB
```

First call lazy-downloads the GGUFs to
`~/.cache/llama.cpp/models/` (the community-standard location, so
any other `llama-cpp-python` tool on the same machine reuses the
same weights). Inference runs locally via `llama-cpp-python` —
no Ollama daemon, no third-party hosting at runtime, works the
same on macOS / Linux / Windows. Model picks are
benchmark-justified — see
[`docs/benchmarks/slm-verifier-results.md`](docs/benchmarks/slm-verifier-results.md).

### C. Axiom OS (shared local LM runtime for other tools)

```bash
pip install axi-platform
axi serve     # starts the bundled local-LM server on localhost
pip install postrule
```
```python
from postrule import LearnedSwitch, JudgeSource, LlamafileAdapter
verifier = JudgeSource(LlamafileAdapter())   # talks to the running axi node
```

If you already run an [Axiom](https://github.com/b-tree-labs/axiom-os)
node — or you'd like other tools on this machine to share one
local-LM runtime — Path C wires Postrule's verifier through it.

### Try it in 60 seconds (no API keys, no Ollama)

```bash
pip install postrule
postrule quickstart           # copies a working example into the cwd and runs it
```
```bash
postrule quickstart --list    # see the menu (hello / tournament / autoresearch / ...)
```

Runnable examples in [`examples/`](./examples/) — each file is
self-contained (no API keys, no external services) and walks one
concept end-to-end. Python 3.10+.

> **Heading to production?** Add `persist=True` —
> the default storage is in-memory and dies with the process.
> Below ~2% verdict rate, in-mem also evicts verdicts before the
> gate has enough paired evidence to advance. See
> [`docs/storage-backends.md`](./docs/storage-backends.md#low-verdict-rate-footgun)
> for the math.

## The six phases

| Phase | Decision-maker | Learning component | Safety floor |
|---|---|---|---|
| `RULE` | Your rule | — | Rule (self) |
| `MODEL_SHADOW` | Your rule | Model predicts, no effect on decision | Rule |
| `MODEL_PRIMARY` | Model if confident | Rule fallback on low conf / model failure | Rule |
| `ML_SHADOW` | Model (or rule) | ML head trains, no effect | Rule |
| `ML_WITH_FALLBACK` | ML if confident | Rule fallback | Rule |
| `ML_PRIMARY` | ML | — | Rule (circuit breaker only) |

Advance between phases when the configured gate decides the
higher-tier classifier is reliably better than the current one.
The default gate (`McNemarGate`) is the paired-proportion
statistical test bounding the probability of a worse-than-rule
transition by its Type-I error rate. `AccuracyMarginGate`,
`MinVolumeGate`, `CompositeGate`, and `ManualGate` ship too;
any object satisfying the `Gate` protocol works.

**Calendar time to `ML_PRIMARY`** (default config, three gated
transitions, `min_paired=200`):

| Workload | Verdict rate | Time to ML_PRIMARY |
|---|---|---|
| 1k req/day, 100% verdicts (`verifier=default_verifier()`) | 1,000/day | **~1.5–6 days** |
| 1k req/day, 5% verdicts (reviewer queue) | 50/day | ~30–120 days |
| 10k+ req/day, 100% verdicts | ≥10k/day | < 1 day |

The autonomous verifier (default) closes the loop dramatically.
Without it, reviewer throughput is the gating constraint — which
is the whole reason `verifier=default_verifier()` is on by
default. See [`docs/getting-started.md`](./docs/getting-started.md#4-graduation--automatic-3-minutes)
for the math.

## Autoresearch + agent loops

Language-model-driven autoresearch loops have a deployment gap:
the loop generates good candidate classifiers, but the path
from "this candidate looks promising" to "ship it under
statistical confidence with rollback" is usually duct tape.

`CandidateHarness` is the production substrate. Wrap a live
switch, register candidates, shadow them against production, get
a head-to-head significance verdict on whether each candidate
beats the live decision. The autoresearch loop reads
`report.recommend_promote`; the rule floor protects production
from bad proposals throughout.

```python
from postrule import CandidateHarness, LearnedSwitch

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
> **Postrule tells you when it worked.**

Full walkthrough in [`docs/autoresearch.md`](docs/autoresearch.md);
runnable end-to-end loop in
[`examples/19_autoresearch_loop.py`](examples/19_autoresearch_loop.py).

## CLIs

```bash
# Find classification sites in any codebase (static AST scan).
postrule analyze ./my-repo --format markdown --project-savings

# Wrap a target function with @ml_switch (AST injection, no typos).
postrule init src/triage.py:triage_ticket --author "@triage:support"

# Reproduce the four-benchmark transition-curve measurements.
postrule bench banking77

# Render a Figure 1-style plot from benchmark output.
postrule plot results/atis.jsonl -o figure-1.png

# Self-measured ROI report from production outcome logs.
postrule roi runtime/postrule/
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
`postrule bench atis` regenerates the ATIS panel of Figure 1 in
seconds; pass other benchmark names (`banking77`, `clinc150`,
`hwu64`, `snips`, `trec6`, `ag_news`, `codelangs`) for the other
panels.

Measured latency (Apple M5 / Python 3.13 / macOS 26 — full
methodology + reproduce instructions in
[`docs/benchmarks/perf-baselines-2026-05-01.md`](docs/benchmarks/perf-baselines-2026-05-01.md)):

- **`classify` at Phase.RULE:** 0.96 µs p50 / 1.04 µs p95.
- **`dispatch` at Phase.RULE:** 1.00 µs p50 / 1.08 µs p95.
- `dispatch` at Phase.MODEL_PRIMARY (LM verifier stubbed):
  1.46 µs p50 / 1.54 µs p95.
- `dispatch` at Phase.ML_PRIMARY (ML head stubbed):
  1.50 µs p50 / 1.58 µs p95.
- **Storage: `BoundedInMemoryStorage` (default for ephemeral state):**
  12M writes/sec sustained.
- **Storage: `FileStorage` batched (production-recommended):**
  245K writes/sec sustained; 4.1 µs per write; ~50 ms crash window.
- Storage: `FileStorage` concurrent 4 threads (batched):
  181K writes/sec sustained.
- Storage: `FileStorage` unbatched per-call fsync (regulated
  workloads): 28K writes/sec, ~36 µs per write.
- Local SLM verifier (shipped default `qwen2.5:7b` via Ollama):
  ~481 ms p50 — see
  [`docs/benchmarks/slm-verifier-results.md`](docs/benchmarks/slm-verifier-results.md).

**Framework tax** at Phase.RULE: ~24× a bare Python call
(42 ns → 1 µs). In absolute terms ~1 µs is fast enough that any
production hot path is dominated by the caller's own logic and
(when later phases engage) by the model's inference, not by Postrule.

Raw numbers + JSONL benchmark data:
[`docs/benchmarks/v1-audit-benchmarks.md`](docs/benchmarks/v1-audit-benchmarks.md).
Regression-guard tests:
[`tests/test_latency_pinned.py`](tests/test_latency_pinned.py).

## Where truth comes from

Verdicts feed the outcome log and drive gate graduation. Postrule
ships five built-in `VerdictSource` implementations:

- `CallableVerdictSource` — any `(input, label) -> Verdict`
  callable. The escape hatch for downstream-signal oracles,
  business rules, pre-computed labels.
- `JudgeSource` — single-model judge with a self-judgment bias
  guardrail (refuses construction when classifier and judge
  resolve to the same model — G-Eval / MT-Bench / Arena
  literature).
- `JudgeCommittee` — multi-model majority / unanimous
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
`abulk_record_verdicts`. Async language-model adapter siblings —
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
  the shadow language model is configured to return the attacker-desired
  label at 0.99 confidence. Each payload is authentic sensitive
  content (ITAR, EXPORT_CONTROLLED, `classified:secret`,
  `samsung_internal` markers) concatenated with an injection
  attempt drawn from publicly-documented families (AgentDojo,
  InjecAgent, OWASP LLM Top-10). An env-gated live-provider
  sweep is available via `POSTRULE_JAILBREAK_LIVE=1` for in-situ
  validation.
- **PII corpus:** rule-only classifier, mixed corpus (SSN, phone,
  email, CC, passport, AWS key, JWT, Bearer token, MRN, ICD-10,
  IBAN, DOB).
- **Circuit-breaker stress:** 100 consecutive ML failures →
  breaker trips once, stays tripped, only explicit operator
  reset restores ML routing. Breaker state persists across
  process restart when `persist=True`.
- **Adversarial-shadow latency:** shadow language model hangs and throws →
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

The same primitive wraps classifications of language-model-*generated
output* before delivery to users. `safety_critical=True` refuses
construction at `Phase.ML_PRIMARY` — the rule floor cannot be
removed without a code change.

```python
from postrule import ml_switch, Phase

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

## Language-model-as-teacher bootstrap

Zero historical labels? Deploy at `Phase.MODEL_PRIMARY`. The
language model makes the decisions. Every classification writes
an outcome record. After 500–5,000 records, train a local ML
head on those model-labeled records, graduate to
`Phase.ML_WITH_FALLBACK`, and the hot path runs at sub-
millisecond per call with zero token cost on the 80%+ of
traffic the ML handles confidently.

```python
from postrule.research import train_ml_from_model_outcomes

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
src/postrule/
├── core.py           # LearnedSwitch, Phase, SwitchConfig, ClassificationRecord
├── decorator.py      # @ml_switch
├── gates.py          # Gate protocol + McNemar / AccuracyMargin / MinVolume / Composite / Manual
├── verdicts.py       # VerdictSource family — Callable / LLMJudge / LLMCommittee / Webhook / HumanReviewer
├── autoresearch.py   # CandidateHarness — production substrate for autoresearch loops
├── storage.py        # FileStorage (batched), SqliteStorage, ResilientStorage, BoundedInMemoryStorage
├── models.py         # OpenAI / Anthropic / Ollama / Llamafile adapters (sync + async siblings)
├── ml.py             # MLHead protocol + sklearn default head
├── wrap.py           # AST-based @ml_switch injector (`postrule init`)
├── analyzer.py       # Static classification-site finder (`postrule analyze`)
├── research.py       # Transition-curve runner, paired-test helpers
├── roi.py            # Self-measured ROI report (`postrule roi`)
├── viz.py            # Figure rendering + McNemar p-values
├── telemetry.py      # Emitter protocol + shipped emitters
├── benchmarks/       # Public-benchmark loaders + reference rules
└── cli.py            # `postrule` CLI entry point

tests/                # 1,433 tests passing, 88 deselected (benchmark/perf/smoke)
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
Rule-to-ML Graduation"* — companion paper, arXiv submission
targeted for ~2026-05-22 (a few days after the v1.0 launch, once
the Snips-rerun + paper-polish work is in). Outline + reproducible
benchmark results at
[`docs/papers/2026-when-should-a-rule-learn/`](docs/papers/2026-when-should-a-rule-learn/).
Annotated bibliography of related work at
[`related-work-bibliography.md`](docs/papers/2026-when-should-a-rule-learn/related-work-bibliography.md).

## Licensing

Postrule is split-licensed:

- **Client SDK** (what you `import` — decorator, config, storage,
  adapters, telemetry, viz, benchmarks, gates, verdicts,
  autoresearch): **Apache License 2.0**. Free for any commercial
  use.
- **Postrule-operated components** (analyzer, ROI reporter,
  research/graduation tooling, CLI, hosted surfaces): **Business
  Source License 1.1** with Change Date **2030-05-01** (auto-
  conversion to Apache 2.0). Additional Use Grant: **production
  self-hosted use is permitted** — the BSL only prohibits
  offering a competing hosted Postrule service.

**The split lives at the per-file level inside `src/postrule/`**, not
at the directory level. Most files in `src/postrule/` are Apache 2.0;
the four BSL-licensed exceptions are
[`src/postrule/analyzer.py`](src/postrule/analyzer.py),
[`src/postrule/cli.py`](src/postrule/cli.py),
[`src/postrule/research.py`](src/postrule/research.py), and
[`src/postrule/roi.py`](src/postrule/roi.py). Tests for those four files
mirror the BSL identifier; everything else is Apache. Each source
file's `SPDX-License-Identifier:` header is authoritative when in
doubt — the `.github/workflows/license-check.yml` workflow enforces
the split on every PR.

See [`LICENSE.md`](LICENSE.md) for the split map,
[`LICENSING.md`](LICENSING.md) for developer-facing Q&A,
[`LICENSE-APACHE`](LICENSE-APACHE) for the canonical Apache 2.0
text, and [`LICENSE-BSL`](LICENSE-BSL) for the canonical BSL 1.1
text + the Additional Use Grant. Commercial licensing that removes
the BSL restrictions is available — contact
`licensing@b-treeventures.com`.

The underlying classification primitive is covered by a filed
US provisional patent (application pending, filed 2026-04-21).

## Status

**v1.1.0** — public release (2026-05-20). Skips 1.0.0
deliberately to match the product posture (software smarter
every month, not frozen at .0.0). PyPI
[1.0.0rc1](https://pypi.org/project/postrule/1.0.0rc1/) was the
pre-release; 1.1.0 supersedes it for the public ship.
Six lifecycle phases ✓ Head-to-head evidence gates ✓
Native async API ✓ VerdictSource family ✓
CandidateHarness for autoresearch loops ✓
Native `Switch` class authoring ✓ Multi-arg auto-packing ✓
`postrule init --auto-lift` (branch + evidence lifters) ✓
Drift detection (`postrule refresh` / `doctor`) ✓
Prescriptive analyzer (Phase 5 hazard diagnostics) ✓
Account system MVP + `postrule login` ✓
MCP server (`postrule mcp`) ✓
VS Code extension (early v1.1) ✓
1,433 tests passing.

Wave 2 (cloud features + hosted analyzer + dashboards) — rolling
through 2026; waitlist on [postrule.ai](https://postrule.ai).
PyCharm plugin + benchmark/report harness + branch-lifter relaxation —
v1.5 / v1.x. See [`docs/limitations.md`](docs/limitations.md) for the
versioned roadmap.

## Dev setup

```bash
git clone https://github.com/b-tree-labs/postrule.git
cd postrule
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,train,bench,viz]'
pytest tests/
```

## About B-Tree Labs

B-Tree Labs is a DBA of B-Tree Ventures, LLC (Texas). Postrule is
patent-protected (USPTO provisional filed 2026-04-21),
dual-licensed (Apache 2.0 + BSL 1.1 with Change Date 2030-05-01
→ Apache 2.0), and shipped under a formal release process — the
company carries the work, not any single person.

- GitHub: <https://github.com/b-tree-labs/postrule>
- Issues / questions: <https://github.com/b-tree-labs/postrule/issues>
- Maintainer: Benjamin Booth ([@benjaminbooth](https://github.com/benjaminbooth))
- Trademark / licensing inquiries — `trademarks@b-treeventures.com`,
  `licensing@b-treeventures.com`
- Procurement-ready documents: [DPA template](docs/legal/dpa-template.md),
  [sub-processors](docs/legal/sub-processors.md),
  [access policy](docs/legal/access-policy.md),
  [telemetry wire spec](docs/legal/telemetry-shape.md).

---

_Copyright © 2026 B-Tree Labs (dba B-Tree Labs).
Split-licensed — Apache 2.0 on the client SDK, BSL 1.1 on
Postrule-operated components; see [`LICENSE.md`](LICENSE.md).
Postrule and B-Tree Labs are trademarks (or pending trademarks) of
B-Tree Labs. Neither the Apache 2.0 license nor the
BSL 1.1 license grants any right to use these marks — see
[`TRADEMARKS.md`](TRADEMARKS.md) for the project's fair-use
position._
