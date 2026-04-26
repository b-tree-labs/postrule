# Getting started with Dendra

A working switch in 2 minutes. A switch that graduates itself in
15. No prior ML experience required.

> **If you only read one thing:** You call your rule (or
> `rule.classify()` / `rule.dispatch()`). Dendra logs every call
> automatically. When a verdict arrives — from a human reviewer,
> a downstream signal, an oracle — you record it with
> `result.mark_correct()` (or `.mark_incorrect()`). Every N
> verdicts, Dendra's gate reads the log and graduates the phase
> if the evidence is strong enough. You never have to call
> `advance()` by hand.

## What Dendra is

A classification primitive — first shipped as a Python
decorator; TypeScript and Mojo-compat bindings follow — that
wraps a hand-written classification rule and manages its
evolution through six lifecycle phases, from `RULE` (your rule
decides) to `ML_PRIMARY` (a trained ML head decides, rule as
safety net). Graduation between phases is **evidence-gated**:
a configurable gate compares the candidate against the current
decision-maker on the same inputs and advances only when the
target phase is reliably better. The default is a head-to-head
significance gate (`McNemarGate` — McNemar's exact test under
the hood); `AccuracyMarginGate`, `CompositeGate`,
`MinVolumeGate`, and `ManualGate` also ship, and any
`Gate`-conforming object works.

You keep the rule. Dendra learns around it.

## The mental model

Three things happen around a switch, in this order:

1. **You call the rule** (or `.classify` / `.dispatch`). Dendra
   runs it, optionally runs a shadow language model or ML head depending on
   the phase, and returns the label. **Dendra also auto-appends
   a `ClassificationRecord`** to the outcome log with
   `outcome=UNKNOWN` and every shadow observation captured —
   drift dashboards, ROI reports, and multi-language model scorecards now
   work for you with no extra calls.
2. **You report the verdict when it arrives.** The ergonomic
   path is `result.mark_correct()` /
   `.mark_incorrect()` / `.mark_unknown()` right on the returned
   `ClassificationResult`. For async / webhook feedback, the
   `rule.record_verdict(input, label, outcome)` method is the
   same thing without the back-reference.
3. **Dendra graduates the phase** automatically, every
   `auto_advance_interval` verdicts (default 100). The configured
   gate reads the log and, if the evidence is strong enough,
   mutates the phase up by one.

For operator-only graduation (regulated deployments, manual
approval), pass `gate=ManualGate()` or set `auto_advance=False`
and call `switch.advance()` from your own workflow.

> A sequence diagram for this flow lives in
> `docs/diagrams/switch-lifecycle.svg` (rendered from source in
> `docs/diagrams/src/`).

## 1. The smallest switch (2 minutes)

```python
from dendra import ml_switch

@ml_switch(labels=["bug", "feature_request", "question"])
def triage_rule(ticket: dict) -> str:
    title = (ticket.get("title") or "").lower()
    if "crash" in title or "error" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"

# Call it like any Python function.
label = triage_rule({"title": "app crashes on login"})   # → "bug"
```

That's a working switch. Behavior is identical to the
un-wrapped rule. **The log is already filling up** — every call
auto-appends a record with the label, the shadow predictions
(when a model is configured), and `outcome=UNKNOWN` waiting for
your verdict.

**What Dendra did:** built a `LearnedSwitch` holding your rule,
initialized an outcome log (bounded in-memory by default),
picked a default gate, made the decorated name callable, and —
on the first call above — appended an UNKNOWN record you can
later update with feedback.

## 2. Dispatch: let Dendra call the handler (2 minutes)

Most production code doesn't just want the label — it wants
*the thing that happens when the label is X.* Pass handlers in
the `labels=` dict and call `dispatch()`:

```python
def send_to_engineering(ticket): ...  # your real handler
def send_to_product(ticket):     ...
def send_to_support(ticket):     ...

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

# Classify AND fire the matched handler.
result = triage_rule.dispatch({"title": "app crashes on login"})
# result.label == "bug"
# result.action_result == send_to_engineering's return value
```

`classify()` is the pure verb (safe from tests / dashboards);
`dispatch()` is the production verb (classify + fire the
handler). A handler that raises is captured on
`result.action_raised`, not propagated — the classification
decision survives handler bugs.

**What Dendra did:** ran the rule, looked up the matched
label's `on=` callable, invoked it with the input, captured any
exception as a string on the result, and auto-appended the
call + action outcome to the log.

## 3. Recording verdicts — three easy ways (2 minutes)

When feedback arrives you tell Dendra the verdict. Four
equivalent shapes — pick the one that fits your control flow:

**Fluent on the result** (immediate-feedback pattern):

```python
result = triage_rule.classify(ticket)
result.mark_correct()   # or .mark_incorrect() / .mark_unknown()
```

**Context manager** (try/except-scoped feedback):

```python
with triage_rule.switch.verdict_for(ticket) as v:
    try:
        do_downstream(v.result.label)
        v.correct()
    except HandlerError:
        v.incorrect()
# If the block exits without a mark, default to UNKNOWN — the
# log always has a trailing state.
```

**Direct method** (webhook / queue / external verdict source):

```python
# From a FastAPI endpoint, Slack slash-command handler, CRM
# callback, cron-driven reviewer-queue consumer:
triage_rule.record_verdict(
    input=ticket,
    label="bug",
    outcome="correct",
)
```

See `examples/09_verdict_webhook.py` for a full webhook
walkthrough.

**Mirror to an audit store** with the `on_verdict` hook:

```python
@ml_switch(
    labels=["bug", "feature_request"],
    on_verdict=lambda record: audit_log.append(record),
)
def triage_rule(ticket): ...
```

**What Dendra did:** appended a verdict-bearing
`ClassificationRecord` to the outcome log, fired your
`on_verdict` hook if configured, and — every
`auto_advance_interval` verdicts — asked the configured gate
whether the evidence earns the next phase.

## 4. Graduation — automatic (3 minutes)

You don't have to do anything. Every `auto_advance_interval`
verdicts (default 100), the switch asks its gate whether the
evidence has earned the next phase. When it does, Dendra
mutates the phase and emits an `advance` telemetry event tagged
`auto=true`. Subsequent `classify()` calls route through the
new phase's decision-maker.

```python
# After a few hundred .mark_correct() / .mark_incorrect() calls...
print(triage_rule.switch.phase())     # MODEL_SHADOW — graduated automatically
```

Dendra's default gate is `McNemarGate(alpha=0.01, min_paired=200)`
— it refuses to advance until at least 200 paired (current,
target) outcomes are logged AND the paired-proportion test
rejects the null at p < 0.01. The probability that Dendra
graduates to a worse-than-current phase is bounded above by
`alpha`.

**Other built-in gates** (swap via `gate=` on the switch):

- `AccuracyMarginGate(margin=0.05)` — advance when target
  accuracy beats current by a margin; no significance test.
- `MinVolumeGate(inner=..., min_records=2000)` — wrap any gate
  to require an absolute record floor first.
- `CompositeGate.all_of([g1, g2, ...])` — advance only when all
  sub-gates advance.
- `CompositeGate.any_of([g1, g2, ...])` — advance when any
  sub-gate advances.
- `ManualGate()` — always refuses; graduation is operator-only.

Any object conforming to the `Gate` protocol works; write your
own for domain-specific thresholds (e.g., reject advances
during business hours, require a signal-sink approval first).

**On-demand probe.** Call `switch.advance()` yourself to force
an evaluation ahead of schedule — useful for dashboards, ops
workflows, or tests:

```python
decision = triage_rule.switch.advance()
print(decision.rationale)  # explains whether it advanced and why
```

**Operator-only graduation.** For regulated deployments:

```python
from dendra import ManualGate

@ml_switch(labels=..., gate=ManualGate())
def triage_rule(ticket): ...

# Nothing advances until an operator explicitly calls advance()
# with a different gate, or mutates config.gate.
```

## 5. Going to production — persistence, safety, observability (5 minutes)

The four knobs that turn the 2-minute demo into a production
deployment:

**Durable outcome log.** Pass `persist=True` and Dendra writes
to a resilient file-backed store (rotating JSONL + auto-fallback
to an in-memory buffer on disk issues). See
[`storage-backends.md`](./storage-backends.md) for the full
matrix.

```python
@ml_switch(labels=..., persist=True)
def triage_rule(ticket): ...
```

**Safety-critical cap.** For classifications where the rule
floor must remain reachable (auth, content-safety, HIPAA-bound
decisions): refuse the final phase at construction time.

```python
@ml_switch(labels=..., safety_critical=True)
def access_rule(request): ...
```

The switch will never construct at `Phase.ML_PRIMARY`; the
evidence-based advance will never promote past
`ML_WITH_FALLBACK`.

**Observability.** `status()` returns a JSON-serializable
snapshot for dashboards; telemetry events flow through a
pluggable emitter.

```python
snapshot = triage_rule.switch.status()
print(snapshot.phase, snapshot.outcomes_total, snapshot.circuit_breaker_tripped)
```

**Circuit breaker** (kicks in at `Phase.ML_PRIMARY`). When the
ML head starts failing, routing falls back to the rule until an
operator resets:

```python
triage_rule.switch.reset_circuit_breaker()
```

## Where next

- [`api-reference.md`](./api-reference.md) — signature-by-signature
  lookup for every method.
- [`storage-backends.md`](./storage-backends.md) — which backend
  fits which deployment, custom-backend recipe.
- [`../examples/`](../examples/) — eight+ runnable examples, each
  targeting one concept.

## FAQ — the questions you're about to ask

**Do I have to call `mark_correct()` / `record_verdict` on every classify?**
No. Skip it when no feedback signal is available. Auto-log
means the UNKNOWN row is already there; a verdict call appends
the outcome when you learn it. More verdicts → stronger gate
evidence; no verdicts → no graduation, but drift / ROI /
dashboards still work against the UNKNOWN rows.

**Do I have to call `advance()` myself?**
No — the default is automatic. Every 100 verdicts, Dendra asks
the gate. Pass `gate=ManualGate()` or set `auto_advance=False`
for operator-only graduation; then call `switch.advance()`
yourself from a cron or ops workflow.

**Does the rule still run once the language model takes over?**
At MODEL_SHADOW and ML_SHADOW, yes — the rule decides; the language model
or ML is shadow-observed. At MODEL_PRIMARY and
ML_WITH_FALLBACK, the rule runs as a fallback when the primary
has low confidence or fails. At ML_PRIMARY, the rule is only
reached when the circuit breaker trips. The rule is never
deleted — it is always at most one hop away from the decision.

**Can I use a different gate?**
Yes. Pass `gate=ManualGate()` for operator-only,
`gate=AccuracyMarginGate(margin=0.05)` for margin-based,
`gate=CompositeGate.all_of([...])` for multi-condition, or any
custom object satisfying the `Gate` protocol.

**What about async?**
`classify()` / `dispatch()` / `record_verdict()` are synchronous.
If your model adapter is async, wrap it with `asyncio.run`
inside a sync adapter shim. Native async support is on the
roadmap.

**Where's the language-model adapter?**
`OpenAIAdapter`, `AnthropicAdapter`, `OllamaAdapter`,
`LlamafileAdapter` ship out of the box; import from `dendra`.
Pass one as `model=` to the switch; Dendra calls its
`classify(input, labels)` at MODEL_SHADOW and MODEL_PRIMARY.
