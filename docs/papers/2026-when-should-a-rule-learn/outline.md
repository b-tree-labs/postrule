# When Should a Rule Learn?

## Transition Curves for Safe Rule-to-ML Graduation

**Authors:** Benjamin Booth (B-Tree Ventures)
**Status:** Working outline — v0.1
**Target venue:** NeurIPS main track (primary) / ICML (secondary) / ICLR ML4Code workshop (fallback)
**Date:** 2026-04-20

---

## Abstract (draft)

Production classification systems overwhelmingly start as hand-written
rules — support-ticket triage, intent routing, content moderation,
retrieval-strategy selection — because training data doesn't yet
exist on day one. Over time, outcome data accumulates, yet the rules
stay frozen: replacing a rule with a machine-learned classifier
requires custom migration engineering at every decision point. We
formalize this migration as a *graduated-autonomy* lifecycle with
six phases (rule → LLM-shadow → LLM-primary → ML-shadow →
ML-with-fallback → ML-primary) and prove that transitions between
phases admit safety-guaranteed termination conditions. We present
empirical *transition curves* — the functional relationship between
accumulated outcome volume and the depth at which a graduated
classifier first outperforms the rule floor — across four public
intent-classification benchmarks (Banking77, CLINC150, HWU64, ATIS).
Transition depth varies by two orders of magnitude across
categories: label cardinality, distribution stability, and outcome
quality each predict crossover. We introduce a reference
implementation (the Dendra library) and release the transition-curve
dataset so practitioners can estimate when their own classifier
should graduate.

---

## 1. Introduction

### 1.1 The pattern nobody has formalized

Every production system has classification decisions. Day-zero
patterns:

- Ticket triage: `if "crash" in title: return "bug"`
- Intent routing: lookup table over 10 intents
- Content quality: threshold on a heuristic score
- Retrieval strategy: rule-tree over query length + domain

Day-N reality: outcome data has accumulated — you know, post-hoc,
whether each rule's classification was correct. The rule still runs
because nobody invested engineering time to graduate it. Everyone
does this migration ad-hoc; nobody has formalized it.

### 1.2 Why this is a problem

Three failure modes:

- **Rules calcify.** Distribution shifts that would be obvious
  signal to a learned model simply produce degrading accuracy.
- **ML-from-day-one fails.** No training data on day zero =
  cold-start failure.
- **Ad-hoc migration has no safety floor.** Most production
  `try_ml_else_fall_back_to_rule` code has no statistical criterion
  for "when is ML actually better."

What's missing: a **primitive** that captures the pattern formally,
with safety guarantees, so every classification site graduates
uniformly.

### 1.3 Contribution

1. A **six-phase graduated-autonomy lifecycle** (Table 1) with
   formal transition criteria.
2. A **safety theorem** bounding the probability of worse-than-rule
   behavior under any phase transition (§3).
3. Empirical **transition curves** on four public benchmarks,
   measuring the outcome-volume threshold at which ML first
   outperforms rule at statistical significance (§5).
4. A **category taxonomy** (§6) predicting transition depth from
   dataset attributes (label cardinality, distribution stability,
   outcome latency, outcome quality).
5. An open-source reference implementation (Dendra library, Apache
   2.0) + released transition-curve dataset.

---

## 2. Related Work

- **Rule-based classification** — Expert systems era through
  modern rule-based triage. Cite classic reference + recent
  production-ML treatments.
- **Cold-start ML** — Active learning (Settles 2009), few-shot
  fine-tuning, zero-shot LLM classification.
- **Shadow-mode deployment** — Industry practice; cite blog posts
  and any formal treatments. Most discussions are about A/B testing
  user-facing features, not rule→ML.
- **Online learning** — Vowpal Wabbit, adaptive models; cannot
  guarantee rule-floor safety.
- **AutoML** — H2O, AutoGluon, AutoKeras. Focuses on model selection
  given training data, not lifecycle migration from rule.
- **Graduated autonomy in other domains** — aviation autopilot
  (degrees of autonomy), self-driving (SAE levels), medical
  decision-support. Draw the analogy — we're importing safety
  vocabulary from those fields into classification.

---

## 3. Formal Framework

### 3.1 Lifecycle phases

Table 1: **Six-phase lifecycle**.

| Phase | Decision-maker | Learning component | Safety floor |
|---|---|---|---|
| P0: RULE | Rule | — | Rule (self) |
| P1: LLM_SHADOW | Rule | LLM predicts, no effect on decision | Rule |
| P2: LLM_PRIMARY | LLM | LLM if confident, rule fallback | Rule |
| P3: ML_SHADOW | LLM (or Rule) | ML classifier trains, no effect | Rule |
| P4: ML_WITH_FALLBACK | ML | ML if confident, rule fallback | Rule |
| P5: ML_PRIMARY | ML | — | Rule (circuit breaker only) |

The rule is *always* the safety floor. It's the contract: no phase
transition can produce worse-than-rule behavior in steady state.

### 3.2 Transition guards

Every transition has a guard condition (Table 2). Guards are
statistical: we require evidence, not intuition.

[Table 2: transition guards — who decides, what evidence, what p-value]

### 3.3 Safety theorem

**Claim (informal).** If the approval backend enforces the
conservative preconditions (min samples, statistical beat, flip
rate ≤ k, shadow period), then the probability that a phase
transition produces a classifier with mean accuracy worse than the
rule's mean accuracy — measured over the post-transition window —
is bounded by the Type-I error rate of the underlying statistical
test, multiplied by the number of transitions.

[Formal statement + proof sketch. Reference: spec-learned-switch.md §14]

---

## 4. Experimental Setup

### 4.1 Benchmarks (public)

Four intent-classification datasets chosen for diversity across
label cardinality, domain, and distribution characteristics:

| Dataset | Labels | Train | Test | Key attribute |
|---|---|---|---|---|
| **Banking77** (Casanueva et al. 2020) | 77 | 10,003 | 3,080 | Fine-grained, single domain |
| **CLINC150** (Larson et al. 2019) | 151 | 15,000 | 5,500 | Multi-domain (10); **includes out-of-scope (OOS)** — safety-relevant |
| **HWU64** (Liu et al. 2019) | 64 | ~25,716 | — | Multi-domain (21 scenarios), more diverse |
| **ATIS** (Hemphill et al. 1990) | 17–26 | 4,978 | 893 | Classic flight-booking; smallest label count, narrow domain |

Rationale: published, cited, leaderboarded. No private corpora.

### 4.2 Rule construction

For each dataset, a hand-written rule is built from:

- The first 100 training examples (authors inspect + construct if/else).
- Keyword heuristics over the dominant lexical signal per label.
- Fallback label for no-match cases (the most common label, or
  `out_of_scope` for CLINC150).

Rules are released with the dataset so replicators can reproduce
exactly. *Deliberately simple* — this represents the real-world
"day-zero rule" not an expert-tuned baseline.

### 4.3 Outcome simulation

Production systems accumulate outcomes over time; benchmark
experiments simulate this by:

1. Starting Dendra at Phase 0 (rule only).
2. Streaming test-set examples one at a time.
3. Recording the rule's prediction + the ground-truth label as an
   outcome.
4. Every 100 outcomes, evaluating whether the accumulated training
   data supports a phase transition.

This produces the **transition curve**: outcome volume × accuracy
for each phase.

### 4.4 Metrics

- **Transition depth** (primary): outcome count at which the ML
  phase first beats the rule with *p* < 0.01 on a held-out split.
- **Crossover accuracy delta**: ML accuracy minus rule accuracy at
  the transition point.
- **Sustained superiority window**: consecutive outcomes for which
  ML maintains its lead.
- **Safety breaches**: phase transitions that subsequently regress.

### 4.5 Reproducibility

- All code: `dendra/` (Apache 2.0).
- All rules: published in supplementary material.
- All seeds fixed; transition curves generated with `dendra bench`
  (ships with v0.2.0).
- Transition-curve dataset: released as `dendra-transition-curves-2026.jsonl`.

---

## 5. Transition Curves (Main Results)

### 5.1 Primary figure

[Figure 1: four panels, one per benchmark. X = outcome volume
(log-scale 10² → 10⁴), Y = accuracy. Two lines per panel: rule
(flat) and ML (rising). Vertical bar at transition depth.]

### 5.2 Transition depths

Table 3 (**headline result**):

| Dataset | Transition depth | Rule acc | ML acc @ transition | Delta |
|---|---|---|---|---|
| ATIS | ~? | ~? | ~? | ~? |
| Banking77 | ~? | ~? | ~? | ~? |
| HWU64 | ~? | ~? | ~? | ~? |
| CLINC150 | ~? | ~? | ~? | ~? |

*(Numbers fill in from experiments. Expected pattern: ATIS (narrow,
low-cardinality) transitions earliest; CLINC150 (high cardinality +
OOS) transitions latest.)*

### 5.3 Per-category analysis

Break transition depth by:

- Label cardinality (few-shot per-class vs many-shot)
- Distribution stability (KL divergence window-over-window)
- Outcome quality (direct label vs inferred)

---

## 6. Category Taxonomy — Predicting Transition Depth

If transition depth is predictable from dataset attributes, we can
tell practitioners *before* deployment when to expect graduation.

### 6.1 Attribute dimensions

1. **Label cardinality**: 2 → 1000.
2. **Distribution stability**: stable / moderate / unstable.
3. **Outcome latency**: seconds → days.
4. **Outcome quality**: direct human label > indirect metric >
   inferred.
5. **Feature dimensionality**: low (hand-crafted) → high (embeddings).

### 6.2 Regression model

Fit transition-depth ~ f(cardinality, stability, quality, ...).
Report coefficients, R², and residuals for the four benchmark
datasets.

### 6.3 Category heuristics

Translate the regression into practitioner rules of thumb:

- "If you have direct human labels AND cardinality < 20 AND stable
  distribution, expect transition by ~N_low outcomes."
- "If cardinality > 100 OR distribution unstable, expect ~10×N_low
  to ~100×N_low."

---

## 7. Safety & Governance

### 7.1 Safety-critical caps

`safety_critical=True` switches cap at Phase 4 (ML_WITH_FALLBACK).
Authorization decisions (export-control, classification, RACI) never
graduate to ML-primary.

### 7.2 Approval backends

Summarize the `ApprovalBackend` protocol (manual / conservative
/ strict) from the Dendra spec. Emphasize: the **rule-update
proposal** is a signed content-addressed artifact — consumer systems
can mirror, audit, and sign off on phase transitions via their own
identity infrastructure.

### 7.3 Observed regressions

Report any transition-regression events from the experimental
runs. If any occurred: what triggered auto-revert, how long, was
recovery clean?

---

## 8. Reference Implementation — Dendra

Brief description of Dendra:

- Apache-2.0 Python library, zero required runtime deps beyond ONNX.
- `@ml_switch` decorator API.
- File-based storage (JSONL outcome log + content-addressed ONNX heads).
- Approval backends pluggable.
- Cross-language portability via ONNX + native-code export.

Repo: `https://github.com/bwbooth/dendra`

---

## 9. Discussion

### 9.1 Implications for production ML

What this paper changes for practitioners:

- You can predict, from day-one dataset attributes, approximately
  when to expect to graduate.
- The lifecycle primitive is the same regardless of the task —
  one library, six phases, applied uniformly.
- Safety is bounded by construction.

### 9.2 Limits of the study

- Four benchmarks is not every category.
- Intent classification is one family; text-moderation / image /
  structured-data would extend the taxonomy.
- Rule construction is manual — subject to author bias; we publish
  rules for scrutiny.

### 9.3 Future work

- Federated training: aggregating outcome pools across
  institutions without raw-data sharing. Does federation
  accelerate transitions?
- LLM-as-shadow-labeler: Phase 1 (LLM_SHADOW) specifically tests
  whether an off-the-shelf LLM can serve as the graduation
  intermediate. Potentially skip directly to Phase 4.
- Transition dynamics under adversarial drift: what happens when
  distribution shifts aggressively?

---

## 10. Conclusion

Rule-to-ML graduation is a universal production pattern that has
been left to per-project engineering effort. Formalized, it admits
measurable transition curves, predictable depths, and bounded
safety. The Dendra primitive + the transition-curve dataset give
practitioners the first systematic answer to *when should a rule
learn?*

---

## Appendix

- A: Rules per benchmark (full text).
- B: Transition-curve per benchmark (raw data).
- C: Dendra API reference.
- D: Reproducibility checklist.

---

## Outstanding TODOs (outline → paper)

- [ ] Dendra v0.2.0 — Phase 1 (LLM_SHADOW) implementation
- [ ] Dendra v0.3.0 — Phase 3/4 (ML_SHADOW, ML_WITH_FALLBACK)
- [ ] Benchmark loaders for the four datasets (HuggingFace Datasets)
- [ ] Reference rules per benchmark
- [ ] `dendra bench` CLI — reproducible transition-curve runner
- [ ] Run transition-curve experiments on all four benchmarks
- [ ] Category-taxonomy regression on transition depths
- [ ] Safety-theorem formal write-up
- [ ] Figure 1 (four-panel transition curves)
- [ ] Related-work citations (Casanueva, Larson, Hemphill, etc.)
- [ ] Approval-backend demo scenario (manual sign-off on a transition)

---

_Copyright (c) 2026 B-Tree Ventures, LLC. Apache-2.0 licensed._
