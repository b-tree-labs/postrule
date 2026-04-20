# Dendra

**Graduated-autonomy classification primitive.** Starts as a rule on
day one. Accumulates labeled outcome data. Graduates to an ML
classifier when evidence justifies it. Falls back to the rule on
low confidence. Retrains continuously. One primitive, one lifecycle,
deployed everywhere classification decisions are made.

## Why

Every production system has classification decisions: triage a
support ticket, route a query, classify an interaction, assess
content quality, select a retrieval strategy. These decisions start
as handcrafted rules because there's no training data on day one.
Over time, outcome data accumulates — but the rules stay frozen
because replacing them with ML requires a custom migration for each
decision point.

Dendra formalizes the rule → ML upgrade path so every classification
site in your system gets it for free.

## Design principles

1. **Rule stays code.** User-authored rule function remains as the
   circuit-breaker floor and is never modified by the library.
2. **ML head is data.** Proposals carry a new ONNX head + evidence;
   adoption updates a pointer in `active.json`. No code redeploy at
   adoption or rollback.
3. **Excellent zero-config defaults.** Level-0 default is safe and
   useful with no configuration. Automation and custom backends are
   progressively disclosed opt-ins.
4. **Library-first.** Full governance ships standalone (zero runtime
   deps beyond ONNX Runtime). Consumer systems plug in via one
   protocol; the library never knows consumers exist.
5. **Safety invariant.** `safety_critical=True` caps the lifecycle at
   ML-with-Fallback — authorization decisions never graduate to
   ML-primary mode.

## Status

**v0.1.0 — Phase 0 only.** Wraps a rule function, records outcomes
to a file-based store, gives you a platform to accumulate training
data. ML training + graduation (Phase 1+) ship in later releases.

## Quick start

```python
from dendra import ml_switch

@ml_switch(labels=["bug", "feature_request"], author="alice")
def triage(ticket: dict) -> str:
    if "crash" in ticket.get("title", "").lower():
        return "bug"
    return "feature_request"

# The decorated function works like the original rule, plus records
# the decision for later ML graduation.
label = triage({"title": "App keeps crashing"})   # → "bug"

# When you learn whether the decision was correct, record it:
triage.record_outcome(
    input={"title": "App keeps crashing"},
    output="bug",
    outcome="correct",
)

# Inspect:
triage.status()
# SwitchStatus(name='triage', phase=Phase.RULE, outcomes_total=1, ...)
```

You can also use the class directly when the decorator's convenience
isn't what you want:

```python
from dendra import LearnedSwitch

switch = LearnedSwitch(name="triage", rule=triage_rule_fn, author="alice")
result = switch.classify(ticket)     # SwitchResult
switch.record_outcome(input=..., output=..., outcome="correct")
```

## Lifecycle (six phases)

```
RULE  →  LLM_SHADOW  →  LLM_PRIMARY  →  ML_SHADOW  →  ML_WITH_FALLBACK  →  ML_PRIMARY
```

v0.1.0 ships RULE only. Subsequent phases land as follow-up releases;
each phase is a data-driven transition gated by evidence (see spec).

## Roadmap

- v0.1.0 — Phase 0 (RULE) + outcome logging + file storage
  *(this release)*
- v0.2.0 — Phase 1/2 (LLM shadow / LLM primary)
- v0.3.0 — Phase 3/4 (ML shadow / ML with fallback)
- v0.4.0 — Phase 5 (ML primary) + ApprovalBackend governance
- v1.0.0 — Production-stable API; cross-language scaffolding

## License

Apache 2.0.

_Copyright (c) 2026 B-Tree Ventures, LLC. Apache-2.0 licensed._
