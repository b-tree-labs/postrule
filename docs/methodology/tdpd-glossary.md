# TDPD glossary

Vocabulary lock for Test-Driven Product Development. Cite this page
as the canonical definition; we'll keep it stable with versioned
amendments rather than silent edits.

## Hypothesis

A pre-registered, content-hashed claim about what a candidate
decision-maker will do under production traffic. Written before the
candidate sees real verdicts, committed to git, immutable thereafter.
A valid hypothesis names six things: unit of decision, gate
criterion, expected n, expected effect size, truth source, and
rollback rule.

In Dendra: lives at `dendra/hypotheses/<switch>.md`. Auto-drafted by
`dendra init` from cohort-tuned defaults; customer reviews and
commits before the switch ships.

## Shadow Phase

The period during which a candidate runs alongside the incumbent but
doesn't drive user-visible behavior. Both observe the same input;
both produce a label; only the incumbent's label takes effect. The
shadow's labels accumulate in the audit chain for later paired-
correctness comparison.

In Dendra's six-phase lifecycle: `MODEL_SHADOW` and `ML_SHADOW` are
the explicit shadow phases.

## Gate

The statistical decision rule that promotes a candidate to primary.
Evaluated continuously (or at scheduled checkpoints) against
accumulated paired-correctness evidence. Fires when evidence clears
the pre-registered α threshold.

Default gate in Dendra: `McNemarGate` with α = 0.01 and a minimum
30 paired samples. Other gates ship: `AccuracyMarginGate`,
`MinVolumeGate`, `CompositeGate`, `ManualGate`. Customers can
implement their own; the protocol is two methods.

## Graduation

The event of a candidate clearing its gate and being promoted to
primary. Discrete; observable; logged to the audit chain with
timestamp, p-value, and effect size at first clear.

A graduation is also the terminal event in the test-driven cycle —
the equivalent of a passing test. After graduation the discipline
becomes "keep it green" via drift detection.

## Drift

A change to the operating environment that invalidates the
hypothesis the gate fired against. Most common cause: the wrapped
function's source changed (AST hash mismatch). Less common: traffic
distribution shifted; label space added a new class; verdict source
quality dropped.

Dendra ships an AST-hash drift detector by default; it runs on
`dendra refresh --check`. Custom drift detectors can be plugged in
through the same protocol.

## Rule Floor

The architectural invariant that a deterministic fallback (the
"rule") is always available regardless of the candidate's phase.
The rule floor is what makes TDPD safer than free-form deployment:
gate failure mode is "fall back to rule," not "show a broken thing."

In Dendra: `safety_critical=True` on `SwitchConfig` enforces the rule
floor at construction. The circuit breaker auto-reverts ML decisions
to the rule when the candidate fails (exception, timeout, drift).

## Regime

A coarse classification of the *decision-space shape* that determines
how long the gate typically takes to fire. Three regimes in Dendra:

- **narrow**: cardinality < 30 labels. Rule is a usable day-zero
  baseline; graduation typically by ~250 outcomes.
- **medium**: cardinality 30–60 labels. Rule is borderline-usable;
  graduation timeline depends on verdict rate.
- **high**: cardinality > 60 labels. Rule is symbolic; production
  teams typically start at Phase 2 with a zero-shot LLM and
  accumulate outcome data via Dendra's logging.

Regime is computed from the analyzer's static read of the call site.
Cohort-tuned defaults are regime-keyed; the report card's predicted
graduation interval comes from the regime's cohort distribution.

## Pre-registration

The discipline of committing the hypothesis file to git **before**
any data exists. Content-hash recorded; subsequent edits change the
hash and break the audit chain. Same posture clinical trials use to
prevent p-hacking.

Pre-registration is what makes TDPD honest. Without it, a team can
quietly revise the gate criterion after seeing data. With it, the
git history is the audit trail; revisions are visible and dated.

## Cohort

The set of public, opted-in Dendra Insights participants whose
shape-only telemetry contributes to tuned defaults and predicted
graduation intervals. Receiving cohort wisdom does NOT require
contributing to it (asymmetric by design). Contributing requires
opt-in via `dendra insights enroll`.

The cohort exists at the methodology level too — anyone running
TDPD-style experiments outside Dendra is part of the broader cohort
of TDPD practitioners. Cite each other; build the field.

## Audit Chain

The append-only log of every classify call, every gate evaluation,
every phase transition, and every drift event for a wrapped switch.
Stored locally by default (FileStorage / SqliteStorage). Optionally
signed and published; signed audit chains are auditor-grade evidence
for SOC 2 / HIPAA compliance.

The audit chain is what makes a graduation reproducible by a third
party — auditor, regulator, future you. Without it, "we graduated
this site at 312 outcomes" is a claim. With it, it's evidence.

## Verdict Source

The mechanism that says whether the user-visible label was right.
Five built-in source types in Dendra: callable (synchronous Python
function), LLM judge, LLM committee, webhook (async HTTP), human
reviewer. Each source has a documented bias profile (e.g., LLMs have
a self-judgment bias when judging their own outputs; the
committee variant mitigates).

The verdict source is the truth oracle for the gate. Choosing it is
where most TDPD adoption decisions land — which truth signal does
your team trust enough to graduate decisions against?

## Phase

A discrete state in the six-phase lifecycle. Each phase has a primary
decision-maker (the source of the user-visible label) and zero or
more shadow observers. Phase transitions are gated; the gate evaluates
paired-correctness evidence between the current phase's primary and
the next phase's candidate.

The six phases:
1. **RULE**: deterministic fallback only. Rule decides.
2. **MODEL_SHADOW**: rule decides; LLM observes silently.
3. **MODEL_PRIMARY**: LLM decides; rule is fallback.
4. **ML_SHADOW**: LLM decides; in-process ML observes silently.
5. **ML_WITH_FALLBACK**: ML decides; rule remains as circuit-breaker
   fallback.
6. **ML_PRIMARY**: ML decides; rule retained for drift rollback.

A switch's lifecycle traverses 1→2→3→4→5→6 with a gate evaluation at
each transition. Skipping is allowed (e.g., a customer who doesn't
have an LLM in the loop goes 1→6 via a different protocol).

---

*Updates to this glossary land as appended sections with version
labels rather than edits to existing definitions. Backwards-
compatibility is the discipline.*
