---
name: dendra-instrument-classifier
description: Wrap a Python function that returns a string label from a finite set with Dendra's @ml_switch decorator so the primitive can log outcomes and graduate the classifier through six lifecycle phases safely. Invoke when the user says "add dendra to", "instrument this classifier", "dendra-ify", "wrap with @ml_switch", "make this learnable", or similar.
---

# Dendra — Instrument a Classifier

## When invoked

The user has a Python function that returns a string label from a
fixed set (if/elif chains, match/case dispatch, keyword lookup,
regex dispatch, or a model-prompted classifier). Your job is to wrap
it with Dendra's `@ml_switch` decorator so the function logs outcomes
and can graduate through six phases (RULE → MODEL_SHADOW → MODEL_PRIMARY
→ ML_SHADOW → ML_WITH_FALLBACK → ML_PRIMARY) with a statistical gate
at every transition.

**Core invariant:** the wrap MUST NOT change what the caller sees.
The rule is always the safety floor. Dendra adds an outcome log and
optional phase machinery around it.

## Steps

### 1. Confirm the target fits Dendra

Check the function:

- **Returns a string label from a finite set?** Required.
  - If it returns a number, ranking, or generation — Dendra doesn't
    fit, STOP.
- **Callable in production?** Required. A helper only called in
  tests is not a Dendra target.
- **Output will be observable post-hoc?** Preferred but not required
  — a site with no outcome signal can still benefit from uniform
  instrumentation.

### 2. Prefer the CLI for the wrap

The library ships `dendra init`, which performs the AST surgery
correctly without any risk of malformed decorator syntax:

```bash
dendra init path/to/file.py:function_name \
    --author "@person:team" \
    [--labels bug,feature,question] \
    [--phase RULE] \
    [--safety-critical] \
    [--dry-run]
```

- `--author` is required. Use Matrix-style `@name:context`. Check
  the codebase's `CLAUDE.md`, `AGENTS.md`, or existing Dendra switches
  for the local convention.
- `--labels` is optional; omit it and Dendra infers from `return`
  string literals.
- `--phase` defaults to `RULE`. Don't change unless the user has
  explicit reason to start at a higher phase.
- `--safety-critical` MUST be set for authorization-class decisions:
  content moderation, export-control, access control, fraud
  blocking, clinical coding, or any classification where an incorrect
  ML answer creates regulatory, safety, or fiduciary exposure. This
  caps graduation at Phase 4.
- `--dry-run` shows a unified diff. Use it first for review.

Example — triage classifier, not safety-critical:

```bash
dendra init src/support/triage.py:triage_ticket \
    --author "@triage:support" \
    --dry-run
```

Example — content-moderation, safety-critical:

```bash
dendra init src/moderation/output_gate.py:gate_response \
    --author "@safety:output-gate" \
    --labels safe,pii,toxic,confidential \
    --safety-critical \
    --dry-run
```

### 3. If the user asks for manual integration instead

Manual pattern (only when the CLI isn't suitable — e.g., multi-file
registration, class-based dispatchers, or integration into an
existing factory):

```python
from dendra import ml_switch, Phase, SwitchConfig

LABELS = ["bug", "feature_request", "question"]

@ml_switch(
    labels=LABELS,
    author="@triage:support",
    config=SwitchConfig(phase=Phase.RULE),
)
def triage_ticket(ticket: dict) -> str:
    # UNCHANGED original body
    title = ticket.get("title", "").lower()
    if "crash" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"
```

Expose the switch for outcome recording at module scope:

```python
# Callers who learn ground truth later record it through this helper.
record_triage_outcome = triage_ticket.record_outcome
```

### 4. Update `pyproject.toml` / `requirements.txt`

Add `dendra>=0.2.0` as a dependency:

```toml
dependencies = [
    ...,
    "dendra>=0.2.0",
]
```

Or for a host application that wants to be Dendra-*ready* without
taking a hard dep (preferred when the host is itself a library):

```toml
[project.optional-dependencies]
learning = ["dendra>=0.2.0"]
```

In that case, guard the import:

```python
try:
    from dendra import ml_switch, Phase, SwitchConfig
    _DENDRA = True
except ImportError:
    _DENDRA = False
```

See `axiom/src/axiom/infra/dendra_adapter.py` in the companion Axiom
project for a full optional-adapter shim pattern.

### 5. Record outcomes when ground truth arrives

The whole point of Dendra is the outcome log. Ensure the integration
calls `record_outcome` at the moment ground truth is known:

```python
# User later resolves the ticket, revealing the true label:
triage_ticket.record_outcome(
    input=ticket,
    output=triage_result,
    outcome="correct",   # or "incorrect" or "unknown"
)
```

### 6. Verify the test suite still passes

Run the repository's test suite immediately after the wrap. Zero
regression is the whole point of Phase 0.

```bash
pytest tests/
```

### 7. What NOT to do

- **Do not change the function body.** The rule stays as written.
- **Do not change caller signatures.** The decorated function must
  be callable identically.
- **Do not introduce classes or refactors.** Dendra integration is
  a one-file decorator change.
- **Do not graduate the phase yet.** Phase 0 (RULE) is the
  integration target. Graduation happens later when outcome data
  justifies it, via `dendra roi` / `dendra bench` analysis.
- **Do not pick arbitrary labels.** Infer from return strings or
  ask the user.

## Output

A minimal diff (~5-15 added lines: the import, the decorator,
optionally a `record_*_outcome` module-level alias). Verify the
test suite still passes. Report the path and line range of the
modified function so the user can review.

## Further reading

- `README.md` in the Dendra repo — overview and quickstart
- `docs/papers/2026-when-should-a-rule-learn/outline.md` — the
  formal framework behind the six phases
- `docs/scenarios.md` — industry-scale applicability with
  cited volume / impact ranges
- `dendra init --help` — CLI reference
