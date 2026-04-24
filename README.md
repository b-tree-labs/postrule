<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/logo/dendra-wordmark-horizontal-dark.svg">
  <img src="brand/logo/dendra-wordmark-horizontal.svg" alt="Dendra" width="420">
</picture>

**The classification primitive every production codebase is missing.**

Every production system has classification decisions — routing a
ticket, classifying an intent, selecting a retrieval strategy,
screening an output for PII. They start as hand-written rules because
no training data exists on day one. Over time, outcome data
accumulates, but the rules stay frozen because migrating each site
to ML is bespoke engineering at every decision point.

Dendra is one decorator, six lifecycle phases, statistical gates at
every transition, and a safety floor that survives jailbreaks, silent
ML failures, and unbounded token bills.

```python
from dendra import ml_switch, Phase, SwitchConfig

@ml_switch(
    labels=["bug", "feature_request", "question"],
    author="@triage:support",
    config=SwitchConfig(phase=Phase.RULE),
)
def triage(ticket: dict) -> str:
    title = ticket.get("title", "").lower()
    if "crash" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"
```

Zero behavior change on day one. Dendra logs every outcome. When
statistical evidence accumulates, advance the phase and the LLM or
ML head takes over — with the rule always available as the safety
floor.

## Install

```bash
pip install dendra
```

Zero required runtime dependencies. Optional extras: `train`
(scikit-learn), `bench` (HuggingFace datasets), `viz` (matplotlib),
`openai` / `anthropic` / `ollama` adapters.

Runnable examples in [`examples/`](./examples/) — each file is
self-contained (no API keys, no external services) and targets
one concept: hello-world wrap, outcome logging, safety-critical
cap, LLM shadow mode, output-safety gate.

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
The default gate (`McNemarGate`) is a paired-proportion
statistical test bounding the probability of a worse-than-rule
transition above by its Type-I error rate; `AccuracyMarginGate`,
`CompositeGate`, `MinVolumeGate`, and `ManualGate` ship too, and
any object satisfying the `Gate` protocol works.

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

Four public benchmarks evaluated end-to-end with paired McNemar's
tests at `p < 0.01`:

| Benchmark | Labels | Rule acc | ML @ transition | ML final | Transition depth |
|---|---:|---:|---:|---:|---:|
| ATIS | 26 | 70.0% | 75.6% | 88.7% | ≤ 250 outcomes |
| HWU64 | 64 | 1.8% | 10.5% | 83.6% | ≤ 1,000 outcomes |
| Banking77 | 77 | 1.3% | 8.8% | 87.8% | ≤ 1,000 outcomes |
| CLINC150 | 151 | 0.5% | 7.9% | 81.9% | ≤ 1,500 outcomes |

Measured latency (Apple M5 / Python 3.13 / macOS 26, 2026-04-24):

- **Phase 0 classify, default config: 1.67 µs p50 / 2.42 µs p99**
  (573k ops/sec). Includes the auto-logged UNKNOWN record.
- Phase 0 classify, `auto_record=False`: 0.50 µs p50 / 0.67 µs p99
  (1.9M ops/sec). Pure routing, no log write.
- `persist=True` classify (batched FileStorage, the production
  recommendation): 33.8 µs p50 / 390 µs p99 (~30k ops/sec).
  Durable outcome log with a 50 ms crash window.
- `persist=True` classify (per-call fsync, explicit opt-in for
  regulated workloads): 195 µs p50 / 260 µs p99.
- Real ML head (TF-IDF + LR on ATIS): 105 µs p50.
- Local LLM (llama3.2:1b via Ollama): ~250 ms p50.

Raw numbers: [`docs/working/v1-audit-benchmarks.md`](docs/working/v1-audit-benchmarks.md).
Regression-guard tests: [`tests/test_latency_pinned.py`](tests/test_latency_pinned.py).

At 100M classifications/month, an LLM-only design with a Sonnet-
class model runs **$11.5M/yr** in inference tokens. Dendra at Phase
4 drops this to essentially zero while preserving LLM-quality
decisions on the 20% of traffic the rule/ML can't handle confidently.

## Security properties

- **20-pattern jailbreak corpus:** 100% rule-floor preserved when
  the shadow LLM is configured to return the attacker-desired label
  at 0.99 confidence. Each payload is authentic sensitive content
  (ITAR, EXPORT_CONTROLLED, `classified:secret`, `samsung_internal`
  markers) concatenated with an injection attempt drawn from
  publicly-documented families (AgentDojo, InjecAgent, OWASP LLM
  Top-10). An env-gated live-provider sweep is available via
  `DENDRA_JAILBREAK_LIVE=1` for in-situ validation.
- **PII corpus:** 100% recall, 100% precision on a 25-item mixed
  corpus (SSN, phone, email, CC, passport, AWS key, JWT, Bearer
  token, MRN, ICD-10, IBAN, DOB).
- **Circuit-breaker stress:** 100 consecutive ML failures → breaker
  trips once, stays tripped, only explicit operator reset restores
  ML routing.
- **Adversarial-shadow latency:** shadow LLM hangs 5 ms then throws
  → decision p95 under 50 ms, rule decision unblocked.

See `tests/test_security.py`, `tests/test_security_benchmarks.py`,
and `tests/test_output_safety.py`.

## Output safety

The same primitive wraps classifications of LLM-*generated output*
before delivery to users. Tag with `safety_critical=True` and the
switch refuses to construct at `Phase.ML_PRIMARY` — the rule floor
can never be removed.

```python
@ml_switch(
    labels=["safe", "pii", "toxic", "confidential"],
    author="@safety:output-gate",
    config=SwitchConfig(phase=Phase.RULE, safety_critical=True),
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
makes the decisions. Every classification writes an outcome record.
After 500-5,000 records, train a local ML head on those LLM-labeled
records, graduate to `Phase.ML_WITH_FALLBACK`, and the hot path
runs at ~1 µs per call with zero token cost on the 80%+ of traffic
the ML handles confidently.

```python
from dendra.research import train_ml_from_llm_outcomes

used = train_ml_from_llm_outcomes(
    switch=triage.switch,
    ml_head=head,
    min_llm_outcomes=500,
)
```

See [`examples/07_llm_as_teacher.py`](./examples/07_llm_as_teacher.py) for a
runnable demo of the full pattern.

## Project structure

```
src/dendra/
├── core.py           # LearnedSwitch, Phase, SwitchConfig, ClassificationRecord
├── decorator.py      # @ml_switch
├── storage.py        # Self-rotating file storage + in-memory
├── models.py         # OpenAI / Anthropic / Ollama / llamafile adapters
├── ml.py             # MLHead protocol + sklearn default head
├── wrap.py           # AST-based @ml_switch injector (`dendra init`)
├── analyzer.py       # Static classification-site finder (`dendra analyze`)
├── research.py       # Transition-curve runner, paired-test helpers
├── roi.py            # Self-measured ROI report (`dendra roi`)
├── viz.py            # Figure rendering + McNemar p-values
├── telemetry.py      # Emitter protocol + shipped emitters
├── benchmarks/       # Public-benchmark loaders + reference rules
└── cli.py            # `dendra` CLI entry point

tests/                # 385 tests (13 benchmark-marked, 4 concurrency-marked)
docs/
├── papers/2026-when-should-a-rule-learn/   # Paper outline + results
├── marketing/        # Pricing, applicability, VC deck, positioning
├── integrations/     # SKILL.md for Claude Code + GitHub Action
└── working/          # Design docs, strategy, patent package
```

## Paper

"**When Should a Rule Learn? Transition Curves for Safe Rule-to-ML
Graduation**" — target venue NeurIPS 2026. Outline + results at
`docs/papers/2026-when-should-a-rule-learn/`. arXiv preprint landing
post-patent-filing.

## Licensing

Dendra is split-licensed:

- **Client SDK** (what you `import` — decorator, config, storage,
  adapters, telemetry, viz, benchmarks): **Apache License 2.0**.
  Free for any commercial use.
- **Dendra-operated components** (analyzer, ROI reporter,
  research/graduation tooling, CLI, hosted surfaces): **Business
  Source License 1.1** with Change Date **2030-05-01** (auto-
  conversion to Apache 2.0) and Additional Use Grant permitting
  customer production use against their own code; only prohibits
  offering a competing hosted Dendra service.

See [`LICENSE.md`](./LICENSE.md) for the split map and
[`LICENSING.md`](./LICENSING.md) for developer-facing Q&A.
Per-file headers declare the specific license for each source
file. Commercial licensing that removes the BSL restrictions is
available — contact `licensing@b-treeventures.com`.

The underlying classification primitive is covered by a filed
US provisional patent (application pending).

## Status

**v0.2.0** — all six phases implemented; four-benchmark measurements
published; static analyzer and `dendra init` CLI shipping; output-
safety patterns documented; patent provisional filed 2026-04-21.
385 tests green. Paper submission in progress.

## Dev setup

```bash
git clone https://github.com/axiom-labs-os/dendra.git
cd dendra
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,train,bench,viz]'
pytest tests/
```

## Contact

- GitHub: https://github.com/axiom-labs-os/dendra
- Maintainer: Benjamin Booth — `ben@b-treeventures.com`
- Axiom Labs: the commercial vehicle behind Dendra
  (a B-Tree Ventures, LLC DBA).

---

_Copyright (c) 2026 B-Tree Ventures, LLC (dba Axiom Labs).
Split-licensed — Apache 2.0 on the client SDK, BSL 1.1 on
Dendra-operated components; see `LICENSE.md`. "Dendra",
"Transition Curves", and "Axiom Labs" are trademarks of
B-Tree Ventures, LLC._
