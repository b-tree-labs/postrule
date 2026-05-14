# Postrule API reference

The required call sites, the optional affordances, and a pointer
to the example that demonstrates each feature.

Examples under [`../examples/`](../examples/) are runnable
walkthroughs of individual features. This doc is the flat list.

---

## The headline usage — an ML-graduated action, taken

Postrule's job isn't to produce a label. It's to route an input to
the **right action**, graduating the routing logic from rule to
model to ML as evidence accumulates — while preserving a safety
floor at every step. The headline example therefore pairs labels
with actions and uses `dispatch()`:

```python
from postrule import ml_switch

def send_to_engineering(ticket):  ...   # your real handler
def send_to_product(ticket):      ...
def send_to_support(ticket):      ...

@ml_switch(labels={
    "bug":             send_to_engineering,
    "feature_request": send_to_product,
    "question":        send_to_support,
})
def triage_rule(ticket: dict) -> str:
    title = (ticket.get("title") or "").lower()
    if "crash" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"

# Decide + act. The rule routes today. As the outcome log fills,
# switch.advance() graduates the routing to language model, then to a local
# ML head — the actions stay yours, the classifier behind them
# earns its place through evidence.
result = triage_rule.dispatch({"title": "app crashes on login"})
# result.label == "bug"; result.action_result from send_to_engineering
```

### The smallest possible switch (just the label)

If you don't need dispatch — just the decision — the primitive
also works as a classifier-only wrap:

```python
@ml_switch(labels=["bug", "feature_request", "question"])
def triage_rule(ticket: dict) -> str:
    ...

label = triage_rule({"title": "..."})   # str label, same as the rule
```

Same decorator, same graduation story; the caller handles the
action. Useful when the action is someone else's job — a queue
consumer, a downstream pipeline, a UI.

### Constraints

The `@ml_switch` decorator imposes three structural constraints on the wrapped function:

- **Single positional input** at the v1.0 baseline. Multi-arg signatures lift via `auto_pack=True` (v1): the decorator inspects `inspect.signature(...)`, synthesizes a packed input dataclass, and unpacks at the call site. Type hints on every parameter are required for the LLM/ML head schema.
- **Rule must return a known label name (string), statically determinable** from the function body. The labels passed to `labels=` (or the keys of the dict-form) are the only allowed return values. Returning a value not in the label set is a runtime error caught by the dispatcher.
- **Rule purity** (input only, no side effects). Side effects belong in label-keyed handlers (`Label(on=...)`, dict-labels, or `_on_<label>` methods on a `postrule.Switch` subclass), not in the rule body. Auto-lift (Phases 2-3) extracts side effects mechanically; until then, the user does the extraction.

The full list of structural and semantic limitations, including hidden-state extraction, exception semantics, and dynamic-dispatch refusals, lives in [`limitations.md`](./limitations.md).

## Required per classify

Exactly one of these, per input:

| Call | Returns | Fires `Label.on=` actions? |
|---|---|---|
| `triage_rule(input)` | the rule's label (str) | no |
| `triage_rule.classify(input)` | `ClassificationResult` | no |
| `triage_rule.dispatch(input)` | `ClassificationResult` | yes |

The decorated call is the fast path. `classify()` gives you the
full `ClassificationResult` (label, source, confidence, phase).
`dispatch()` is the production verb when you've registered
actions via dict-labels or `Label(on=...)`.

## The affordances — what else you get

The decorator isn't just a rule-call wrapper. It exposes a small
API that delivers the things that make Postrule *Postrule*: the
evidence log that drives graduation, the lifecycle you can
observe, the gate that promotes the switch automatically, the
breaker you can reset. Most real deployments use at least
`record_verdict` and `advance`; the others show up in
production as soon as you have a dashboard or an on-call
rotation.

### `record_verdict(input, label, outcome=...)`

**Role:** append the verdict (CORRECT / INCORRECT / UNKNOWN) for
a past classification. Every call by default also appears
automatically in the outcome log as an UNKNOWN row (see
`auto_record`); `record_verdict` adds a verdict-bearing row the
gate prefers for paired-correctness math. Prefer the fluent
shortcuts when the feedback is local: `result.mark_correct()`,
`.mark_incorrect()`, `.mark_unknown()`. Use
`switch.verdict_for(input)` as a context manager for
try/except-scoped feedback.

**Signature:** `def record_verdict(*, input, label, outcome, source="rule", confidence=1.0) -> None`

**Side effects:** appends a `ClassificationRecord` to storage,
fires `config.on_verdict(record)` if set, may trigger
`advance()` via the `auto_advance_interval` counter, and emits an
`outcome` telemetry event.

**See also:** [`../examples/02_outcome_log.py`](../examples/02_outcome_log.py)
— verdict recording basics.
[`../examples/09_verdict_webhook.py`](../examples/09_verdict_webhook.py)
— async verdict ingestion from an external webhook.

### `advance()`

**Role:** the phase-graduation verb. Usually called
automatically — `record_verdict` triggers it every
`config.auto_advance_interval` records (default 500). Manual
calls probe the gate on demand; they work whether auto-advance
is on or off.

**Signature:** `def advance() -> GateDecision`

**Returns:** a `GateDecision` in all cases — phase moved or not,
the gate always explains itself (`advance`, `rationale`,
`p_value`, `paired_sample_size`, `current_accuracy`,
`target_accuracy`). On pass, `config.starting_phase` is mutated
up by one phase and an `advance` telemetry event is emitted
with `auto: bool` distinguishing automatic from manual calls.

**Configuration:** `config.gate` selects the evaluator (default
`McNemarGate(alpha=0.01, min_paired=200)`; `ManualGate` refuses
unconditionally; custom gates satisfy the `Gate` protocol).
`config.auto_advance=False` disables the automatic schedule.
`config.auto_advance_interval` controls the frequency.

**See also:** [`../examples/07_llm_as_teacher.py`](../examples/07_llm_as_teacher.py)
— evidence-based graduation end-to-end.

### `status()`

**Role:** JSON-serializable observability snapshot — phase,
outcome counts, shadow-agreement rate, breaker state, model
version. One call per dashboard render / health-check tick.

**Signature:** `def status() -> SwitchStatus`

**See also:** any monitoring integration that consumes
dataclass-as-JSON (Prometheus exporter, StatsD adapter, health
endpoint).

### `phase()` / `phase_limit()`

**Role:** read-only accessors for the current phase and the
ceiling enforced by `config.phase_limit`. Useful in branching
code outside the switch that needs to know "are we trusting
the language model yet" without catching a telemetry event.

**Signatures:**
- `def phase() -> Phase`
- `def phase_limit() -> Phase`

### `reset_circuit_breaker()`

**Role:** operator-driven clear of a tripped circuit breaker at
`Phase.ML_PRIMARY`. The breaker trips automatically on ML
failure; routing falls back to the rule until this method is
called. Typically wired to a signal-sink reply webhook, an admin
HTTP endpoint, or a CLI command.

**Signature:** `def reset_circuit_breaker() -> None`

**See also:** [`../examples/06_ml_primary.py`](../examples/06_ml_primary.py)
Part 2 — breaker trip + reset.

## Switch construction — what's actually required

```python
LearnedSwitch(
    rule=triage_rule,                  # REQUIRED
    name="triage",                     # optional; auto from rule.__name__
    author="@team:triage",             # optional; auto from rule's module
    labels=["bug", "feature"],         # optional; required at Phase 1+
    starting_phase=Phase.RULE,         # optional; default Phase.RULE
    phase_limit=Phase.ML_PRIMARY,      # optional; default Phase.ML_PRIMARY
    safety_critical=False,             # optional; caps phase_limit
    confidence_threshold=0.85,         # optional
    gate=None,                         # optional; default McNemarGate()
    auto_record=True,                  # optional; classify auto-logs UNKNOWN records
    auto_advance=True,                 # optional; default ON
    auto_advance_interval=500,         # optional; evaluate gate every N verdicts (default 500)
    on_verdict=None,                   # optional; callback fired per verdict
    storage=None,                      # optional; default BoundedInMemoryStorage
    persist=False,                     # optional; True = durable FileStorage
    model=None,                        # optional; needed at MODEL_SHADOW+
    ml_head=None,                      # optional; needed at ML_SHADOW+
    telemetry=None,                    # optional; default NullEmitter
)
```

Only `rule=` is required. Everything else defaults sensibly; pass
what your phase / deployment actually needs.

The `@ml_switch(...)` decorator accepts the same kwargs except
`rule=` (it captures the decorated function automatically).

## Six phases at a glance

| Phase | Decision path | Rule on hot path? | Requires |
|---|---|---|---|
| `RULE` | rule | yes | — |
| `MODEL_SHADOW` | rule; language model observes | yes | `model=` |
| `MODEL_PRIMARY` | language model; rule on low-confidence | no (fallback only) | `model=` |
| `ML_SHADOW` | language model or rule; ML observes | yes | `model=`, `ml_head=` |
| `ML_WITH_FALLBACK` | ML; rule on low-confidence | no (fallback only) | `ml_head=` |
| `ML_PRIMARY` | ML; rule on breaker trip | no (breaker only) | `ml_head=` |

Graduation is **evidence-gated**. Call
`switch.advance()` periodically; the default `McNemarGate` reads
the paired-prediction log and advances the phase only when the
target-phase decision-maker is statistically better than the
current-phase one (p < 0.01 on ≥200 paired samples). Pass
`gate=ManualGate()` for operator-only graduation, or plug in a
custom `Gate` for domain-specific thresholds.

## Storage backends (default is fine for dev)

| Backend | Durability | Concurrency | When |
|---|---|---|---|
| `BoundedInMemoryStorage` (default) | none | single thread | dev, tests, short-lived processes |
| `FileStorage` (via `persist=True`) | process-restart safe | single-host multi-process (POSIX flock) | single-host production |
| `SqliteStorage` | ACID (WAL) | 1 writer + N readers | multi-process production |
| `ResilientStorage(FileStorage(...))` | inherits primary; in-memory fallback on failure | inherits primary | production that must survive transient disk issues |
| custom | your call | your call | anything else — implement the `Storage` protocol |

See [`storage-backends.md`](./storage-backends.md) for the full matrix
and custom-backend recipe.

## Labels: three equivalent forms

```python
# (1) plain label names — the caller handles dispatch
@ml_switch(labels=["bug", "feature_request"])
def triage_rule(ticket): ...

# (2) Label objects — mix named + optional actions
@ml_switch(labels=[
    Label("bug", on=send_to_engineering),
    Label("feature_request"),  # no action
])
def triage_rule(ticket): ...

# (3) dict shorthand — label → action handler
@ml_switch(labels={
    "bug": send_to_engineering,
    "feature_request": send_to_product,
})
def triage_rule(ticket): ...
```

`dispatch()` fires the action for the matched label. `classify()`
never fires actions — it's pure.

## Example gallery: what each one actually demonstrates

| # | File | Demonstrates |
|---|---|---|
| 1 | `01_hello_world.py` | Smallest complete example: dict-labels + dispatch. |
| 2 | `02_outcome_log.py` | `persist=True`; ground-truth verdicts; reading records back. |
| 3 | `03_safety_critical.py` | `safety_critical=True` refuses ML_PRIMARY at construction. |
| 4 | `04_llm_shadow.py` | MODEL_SHADOW: rule decides, language model observes, paired log feeds graduation. |
| 5 | `05_output_safety.py` | Same primitive on language model *output*; list[str] labels (no dispatch). |
| 6 | `06_ml_primary.py` | ML_PRIMARY end-state + circuit breaker trip/reset. |
| 7 | `07_llm_as_teacher.py` | Cold-start at MODEL_PRIMARY; operator-triggered graduation. |
| 8 | `08_classify_vs_dispatch.py` | `classify()` pure vs `dispatch()` side-effecting; graceful handler-failure contract. |

## Rule invariants (don't violate these)

The rule function you pass to `rule=` MUST be:

- **Pure** — input only, no side effects. Postrule re-runs the rule
  on every `classify()` for shadow comparison and fallback, so
  side effects fire multiple times in surprising places. If you
  want side effects, use `Label(on=...)` / dict-labels dispatch
  — that's what it's for.
- **Total** — returns for every input. An exception from the
  rule is a crash at every phase; there's no "rule fallback for
  the rule."
- **Deterministic-enough** — doesn't have to be pure-functional
  in the math sense, but two close-in-time calls on the same
  input should agree. Otherwise shadow-comparison math is noise.

## Autoresearch primitives

Two classes that pair with `LearnedSwitch` for picking among
candidate classifiers with statistical confidence. Use them when
you have multiple variants of the same classification problem
(prompt variants, model architectures, retrieval strategies,
scoring formulas) and need an evidence-backed answer to "which
is best?"

### `CandidateHarness`

Shadow-evaluate candidate classifiers against a live production
switch. Each candidate runs alongside production on real
traffic; head-to-head significance verdicts surface promote/hold
recommendations.

```python
from postrule import CandidateHarness, LearnedSwitch

sw = LearnedSwitch(rule=production_rule)
harness = CandidateHarness(switch=sw, truth_oracle=truth_fn, alpha=0.05)
harness.register("v3", candidate_v3)
harness.observe_batch(traffic)
report = harness.evaluate("v3")
if report.recommend_promote:
    deploy(candidate_v3)
```

`CandidateReport` carries `recommend_promote` (the gate
verdict), `p_value` (head-to-head significance), and `b` / `c`
(discordant-pair counts). See `examples/19_autoresearch_loop.py`
for the end-to-end loop.

### `Tournament`

Round-robin head-to-head selection across N candidates. Picks
the candidate that beats every other at `p < alpha`. Has a
unanimity short-circuit when all candidates agree on every
input (no significance test needed in that case).

```python
from postrule import Tournament

t = Tournament(
    candidates={
        "narrow":   narrow_rule,
        "moderate": moderate_rule,
        "broad":    broad_rule,
    },
    truth_oracle=ground_truth_fn,
    alpha=0.05,
)
t.observe_batch(corpus)
report = t.evaluate()
print(report.summary_table())
if report.winner:
    ship_default(report.winner)
```

`TournamentReport` carries `winner`, `unanimous`, `accuracies`
(per-candidate), and `pairwise_reports` (full N×(N−1) matrix
of `CandidateReport`s). See `examples/21_tournament.py` for the
worked walkthrough.

**Pick CandidateHarness** when one candidate is being tested
against an existing live decision-maker (production) — common
in autoresearch loops that propose iterative refinements.
**Pick Tournament** when N candidates need ranking against each
other — common for picking among prompt variants or scoring
formulas where there's no incumbent.

## What Postrule does NOT do (today)

- **Auto-graduate phases.** You set `starting_phase` explicitly;
  the McNemar transition gate is designed but not shipped.
- **Learn the rule.** The rule is yours, written by you. Postrule
  learns *around* the rule — when to augment it, when to route
  past it.
- **Manage model-serving infrastructure.** You bring the language model
  adapter / ML head; Postrule calls `classify()` / `predict()`.
- **Replace your observability stack.** The outcome log is
  structured and greppable; ship it to your metrics pipeline of
  choice.

## Where to go next

- [`storage-backends.md`](./storage-backends.md) — picking the right backend.
- [`FAQ.md`](./FAQ.md) — common questions.
- Examples in [`../examples/`](../examples/) — runnable walkthroughs.
