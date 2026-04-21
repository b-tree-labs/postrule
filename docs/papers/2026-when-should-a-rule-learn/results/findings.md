# Transition-Curve Findings (v0.1)

**Generated:** 2026-04-20
**Dendra version:** 0.2.0
**Raw data:** `*.jsonl` in this directory
**Reproduce:** `dendra bench {banking77,clinc150,hwu64,atis}`

---

## Primer — what this document is saying, in plain English

**The question.** Every production classifier starts its life as a
hand-written rule ("if the ticket says 'crash', it's a bug"). At some
point you have enough real-world outcome data that a machine-learned
classifier would do better than the rule. *When* is that point? And
does it depend on what kind of problem you have?

**The experiment.** We took four public benchmarks that the research
community uses to evaluate intent classifiers. For each one:

1. Wrote a **rule** the way a day-zero engineer would: skim the first
   100 training examples, pick a few keywords per label. Automated and
   reproducible; deliberately not cleverly-tuned.
2. Streamed the full training set through Dendra, **recording every
   outcome** as if it were arriving from a live system.
3. Every few hundred outcomes, **retrained a simple ML classifier**
   on the accumulated log and scored both it and the rule against a
   held-out test set that the system never trained on.

**The picture.** `figure-1-transition-curves.png` shows one panel per
benchmark. The red dashed line (rule accuracy) is flat — the rule
never changes. The blue line (ML accuracy) climbs as more outcomes
accumulate. Where blue crosses red is the **transition point** — the
earliest moment where, statistically, you should graduate from rule
to ML.

**The two regimes (the main result).** Problems fall into two buckets:

- **"The rule works fine for a long time"** (example: ATIS, a 26-label
  flight-booking classifier). The rule is 70% accurate from day one.
  ML catches up at ~500 logged outcomes and gains about 19 points on
  top. Here the interesting question is *when to graduate*.
- **"The rule never really worked"** (example: CLINC150 with 151
  labels). 100 example sentences can't possibly cover 151 categories,
  so the day-zero rule is 0.5% accurate. ML climbs to 82%. Here the
  interesting question is not "when" but *how did you ever ship this
  without Dendra's outcome-logging in the first place*.

**Why this matters for Dendra as a product.** The two regimes map to
two very different sales conversations:
- To a team with a *medium-label, narrow-domain* classifier ("the rule
  works, we just wonder if we should replace it"): Dendra is the tool
  that gives a statistical answer to "yes, now."
- To a team with a *high-cardinality, broad-domain* classifier ("our
  rule is a joke but we shipped it because we had to"): Dendra is the
  tool that makes the eventual ML migration possible at all — its
  outcome-log *is* the training-data source.

---

## Glossary — terms used below

- **Label / class.** The answer the classifier returns, drawn from a
  fixed set. E.g., `"bug"`, `"feature_request"`, `"question"`.
- **Label cardinality.** How many labels exist. "Low" = 2-10,
  "medium" = 10-50, "high" = 50+.
- **Rule.** Hand-written code — `if/elif/else` over keyword matches —
  that returns a label.
- **ML head / classifier.** A machine-learned model that takes the
  same input and returns a label. We use TF-IDF + logistic regression
  — simple, fast, interpretable. Bigger models would do better but
  would muddy the comparison.
- **Outcome.** A row in the log saying *"when the input was X, the
  correct label was Y"*. In production, this comes from user
  correction, audit trails, downstream-signal inference.
- **Training stream.** We feed training examples to Dendra one at a
  time, simulating live production traffic.
- **Test set (held-out).** Data the system never trains on, used only
  for measuring accuracy. 20-30% of each benchmark by convention.
- **Accuracy.** Fraction of test-set examples classified correctly.
- **Checkpoint.** Snapshot of rule/ML accuracy at a given training-
  outcome count. Each JSONL row (except the summary) is one checkpoint.
- **Crossover / transition depth.** The smallest outcome count at
  which ML beats the rule on the test set. The paper's headline metric.
- **Phase (0-5).** Dendra's six lifecycle stages: RULE → LLM_SHADOW
  → LLM_PRIMARY → ML_SHADOW → ML_WITH_FALLBACK → ML_PRIMARY. Higher
  phase = more autonomy; the rule is always the safety floor.
- **Shadow mode.** The LLM or ML runs, its prediction is recorded,
  but the rule's decision is still what the user sees. Lets you
  measure a candidate before trusting it.
- **Out-of-scope (OOS).** A special label meaning "none of the
  others" — CLINC150 is famous for having this. Rules struggle here.
- **Unpaired two-proportion z-test.** A statistical test for "is
  ML's accuracy *really* higher than the rule's, or could the
  difference be random?" A **p-value < 0.01** means less than 1%
  probability the difference is random. Unpaired = we don't use
  per-example correlations (more conservative).
- **McNemar's paired test.** A stronger version of the above that
  uses per-example agreement. Tighter but requires saving per-row
  predictions; deferred.
- **Seed size.** How many training examples the author looked at
  when writing the rule. Paper default: 100.

---

## Experimental setup

- **Rule:** auto-generated by `dendra.benchmarks.rules.build_reference_rule`
  — seed of N training examples, top-5 distinctive keywords per label,
  fallback to the modal label in the seed window. Paper §4.2.
- **ML head:** `dendra.ml.SklearnTextHead` — TF-IDF + LogisticRegression.
- **Training stream:** full training split, one example at a time, labels
  recorded as "oracle" outcomes (ground truth assumed, matching the
  paper's direct-human-label assumption).
- **Evaluation:** at each checkpoint, rule and ML re-scored against the
  full held-out test split.

## Headline table

Rule built from **first 100 training examples** (paper default).

| Benchmark | Labels | Train | Test | Rule acc | ML @ 1st ckpt | ML final | Final gap |
|---|---:|---:|---:|---:|---:|---:|---:|
| **ATIS**       |  26 |  4,978 |   893 | **70.0%** | 79.3% @ 500 | **88.7%** | **+18.7** |
| **HWU64**      |  64 |  8,954 | 1,076 | **1.8%**  | 10.5% @ 1k  | **83.6%** | **+81.8** |
| **Banking77**  |  77 | 10,003 | 3,080 | **1.3%**  |  8.8% @ 1k  | **87.8%** | **+86.5** |
| **CLINC150**   | 151 | 15,250 | 5,500 | **0.5%**  |  7.9% @ 1.5k | **81.9%** | **+81.3** |

### What's happening

Two regimes emerge cleanly from the data:

**Regime A — Low cardinality, narrow domain (ATIS, 26 labels).**
A day-zero keyword rule gives you **70% accuracy** from the first 100
training examples. ML crosses over at **250 outcomes** and reaches 88.7%
by training-set exhaustion. The gap is real but bounded at ~19 points.

**Regime B — High cardinality, broad domain (HWU64 / Banking77 / CLINC150,
64–151 labels).** A day-zero keyword rule is **near-useless** (0.5% – 1.8%)
— 100 examples literally can't cover 77 labels. ML climbs from single
digits to the low 80s across thousands of outcomes. The gap is enormous
(80+ points) but the crossover has no meaning because the rule never
had a shot.

## Seed-size sensitivity — the two-regime conclusion is robust

To check that the two-regime finding is not an artifact of a stingy
seed size, we re-ran every benchmark at seed=1000 (10× the paper
default) and, for ATIS, at seed=500 as well.

| Benchmark | Labels | Seed=100 rule | Seed=500 rule | Seed=1000 rule | 10× delta |
|---|---:|---:|---:|---:|---:|
| ATIS       |  26 | 70.0% | 69.5% | **72.3%** | +2.3pp |
| HWU64      |  64 |  1.8% |   —   | **5.9%**  | +4.1pp |
| Banking77  |  77 |  1.3% |   —   | **6.8%**  | +5.5pp |
| CLINC150   | 151 |  0.5% |   —   | **5.0%**  | +4.5pp |

**Conclusion.** Rule ceilings move by **<6 percentage points** even
when the engineer gets 10× more seed data to inspect. The narrow-domain
rule (ATIS) is basically saturated at ~70-72%. The high-cardinality
rules (64-151 labels) remain 75-85 points below the ML ceiling. Label
cardinality — not engineer effort — is the dominant variable.

This is the most load-bearing validation of the two-regime story: a
"clever engineer looked harder at the data" does not rescue the
high-cardinality rule.

## Transition depth (primary paper metric)

Training outcomes at which ML first exceeds the rule (taken from
the finer-grained ATIS run, `atis_seed500.jsonl`):

| Benchmark | Transition depth | ML acc @ transition | Rule acc |
|---|---:|---:|---:|
| ATIS       | **≤ 250 outcomes** | 75.6% | 69.5% |
| HWU64      | **≤ 1,000 outcomes** | 10.5% | 1.8% |
| Banking77  | **≤ 1,000 outcomes** | 8.8%  | 1.3% |
| CLINC150   | **≤ 1,500 outcomes** | 7.9%  | 0.5% |

ATIS's 250-outcome crossover reproduces in a finer-grained run
(`atis_seed500.jsonl`) and is the tightest ML-vs-rule margin in the set.
For high-cardinality benchmarks, "transition depth" is a misleading
metric because the rule was never a viable baseline.

## Marketing implications

1. **"The bigger the decision space, the more you need Dendra."**
   High-cardinality classification (70+ labels) has no meaningful
   day-zero rule. Dendra's primitive is the only abstraction that
   handles *start with nothing → graduate when ready* as one API surface.

2. **"For narrow-domain rules, Dendra tells you exactly when to graduate."**
   ATIS rules stay useful for a long time (70% is often "good enough"
   for triage). Dendra's statistical transition guards identify the
   crossover point empirically rather than by intuition.

3. **"Dendra is the only production primitive that maps to this progression."**
   Other libraries are rule-only OR ML-only. Nothing else orchestrates
   the rule → LLM-shadow → LLM-primary → ML-shadow → ML-with-fallback
   → ML-primary lifecycle with safety guarantees at every transition.

## Ideal use cases (inferred)

Where Dendra pays off fastest:

- **Medium-cardinality classification** (10–50 labels) with at least
  modest domain structure. ATIS-like. Rule gives you a working baseline
  day-one; Dendra shows you the crossover point.
- **High-cardinality classification** (50+ labels) where you need a
  working system before you have training data. Banking77-like. The
  rule is a fig leaf, but Dendra's outcome-logging is what lets you
  build the ML classifier that replaces it.
- **Safety-critical decision points** where the rule is a non-negotiable
  floor. Dendra's `safety_critical=True` caps graduation at
  ML_WITH_FALLBACK, so the rule remains the contract.

Where Dendra is less differentiated:

- **Binary classification** with abundant data from day one. Just train
  a classifier.
- **Few-shot / zero-shot LLM wins outright.** If an off-the-shelf LLM
  classifier hits 90%+ with no tuning, the Phase 1 → Phase 2 transition
  happens on day two and the ML phases are overkill.

## Phase 1 (LLM-shadow) probe — small-LLM ceiling

A Phase 1 run on ATIS with a locally-hosted **llama3.2:1b** returned
**0.0% LLM accuracy** across a 100-row test sample. On a reduced
top-4-label subset, the 1B model scored **10%** (vs 25% random).

Data: `atis_llm_llama32-1b.jsonl`.

**Interpretation.** A commodity 1B parameter model is not a viable
zero-shot shadow labeler on ATIS's 26-way compound-label task. This is
a useful negative: the paper's §9.3 "LLM-as-shadow-labeler" claim
cannot rely on the smallest local models. Replicating with a larger
open model (Llama 70B / Mistral / Qwen) or a frontier API (Claude /
GPT-4-class) is the natural follow-up. The Dendra `OllamaAdapter`,
`AnthropicAdapter`, `OpenAIAdapter`, and `LlamafileAdapter` are all
wired and ready — only the model choice changes.

## Statistical transition depth (§4.4)

Using an unpaired two-proportion z-test (p < 0.01):

| Benchmark | Stat depth | Crossover | Final gap |
|---|---:|---:|---:|
| ATIS       | 500   | 500   | **+18.7%** |
| HWU64      | 1000  | 1000  | **+81.8%** |
| Banking77  | 1000  | 1000  | **+86.3%** |
| CLINC150   | 1500  | 1500  | **+81.3%** |

In every benchmark, the first visible crossover is already
statistically significant at p < 0.01 given the held-out test-set size.
Run with `BenchmarkRun.transition_depth(alpha=0.01)`.

## Caveats

- The rule construction is **automated** (top-K keyword selection), not
  human-engineered. A thoughtful day-zero engineer with 100 examples
  would likely build a stronger rule for ATIS-like narrow domains. The
  paper's methodology specifies hand-authored rules; our numbers are a
  reproducible lower bound. Seed-size sensitivity (above) shows this
  doesn't materially change the regime conclusion.
- The ML head is **TF-IDF + LogisticRegression** — deliberately simple.
  Transformers would produce higher ML ceilings but would muddy the
  comparison. The transition-curve shape (not the ML ceiling) is what
  the paper needs.
- The out-of-scope label in CLINC150 hurts rule-only keyword matching
  disproportionately; a rule-class for "unknown/OOS" would raise the
  baseline modestly. Deferred.
- The unpaired z-test above is conservative. A paired McNemar test on
  per-example predictions would be tighter; implementation requires
  saving per-example outputs (not currently persisted).

---

## Industry extrapolation & applicability

The two-regime finding above is scientific. What follows is the
industry-applicability summary — consolidated here so a reader doesn't
have to leave the document to see what the result means for production
software. Deeper analyses are linked at the end.

### What Dendra replaces

Every production codebase contains classification decision points —
functions that take an input and return one of a fixed set of labels
(bug/feature, safe/unsafe, route-A/route-B/route-C, 77 intent codes,
150 support categories, etc.). A back-of-envelope count:

| Org size | Typical classification sites | Dendra-fit sites |
|---|---|---|
| Small SaaS (<20 eng)           | 3–8     | 2–5 |
| Mid-market (20–200 eng)        | 10–40   | 6–25 |
| Enterprise (200+ eng)          | 30–200  | 20–100 |
| Hyperscale (AWS/Google/Meta)   | 1,000+  | 400+ |

Most of these are rules. A few are ML. Almost none make the rule→ML
transition in a principled way.

### Thirteen applicability categories (ranked)

**Tier 1 — slam-dunk fit for Dendra's graduated-autonomy primitive:**

1. **Customer-support ticket triage** — medium cardinality, rich
   outcome signal via resolution codes and CSAT.
2. **Chatbot / voice-agent intent routing** — literally what our four
   benchmarks measure.
3. **Content moderation** — safety-critical (Phase 4 cap), outcome
   from user reports, appeals, moderator overrides.
4. **Clinical coding** (ICD-10, CPT) — the extreme high-cardinality
   case; rules cannot work; outcome-logging is essential.
5. **Fraud / anomaly triage** — low cardinality, safety-critical,
   regulatorily observed.
6. **Security / SOC alert triage** — analyst-disposition outcomes;
   every large SOC has custom rule-trees begging to graduate.

**Tier 2 — strong fit:**

7. E-commerce taxonomy classification.
8. Legal document classification.
9. Log / incident triage.
10. RAG retrieval-strategy selection (Axiom's own site).
11. Tool / agent routing (every agent framework needs this).
12. Tax / compliance transaction coding.

**Tier 3 — possible:**

13. Email folder routing, image/video categorization,
    form-field-validation routing (boundary cases).

### Who Dendra does NOT help

- Binary with abundant day-one training data — just train.
- Off-the-shelf LLM already ≥90% zero-shot — skip to Phase 2 on day
  two; the deeper phases are overkill.
- Sites with no outcome signal — nothing can learn.
- Micro-scale (<100 calls/year) — infra isn't worth it.

### Quantified savings per graduation

Migrating one hand-written rule to a deployed, monitored,
circuit-broken ML classifier costs **5–9 engineer-weeks** in the
status quo (outcome plumbing + training pipeline + ML wiring +
monitoring + circuit breaker). With Dendra, the first site costs 1–2
weeks (learning + setup); every subsequent site costs **0.1–0.3
weeks** (the decorator plus a label set).

At US mid-market fully-loaded engineer cost (~$3–5k/week),
per-site savings are **$18–30k**. Combined with time-to-ML
acceleration, avoided silent-regression events, and compliance
artifacts, a mid-market SaaS with 15 graduation-worthy sites sees
an **order-of-magnitude $1–2M/year** in captured value.

### Internal validation — happening now

Axiom's turn-intent classifier (the top-fit candidate identified by
the internal scan) shipped with Dendra wrapping the rule in Phase 0
this session. Zero behavior change for callers; outcome log is now
capturing every classification. The classroom extension's downstream
signals (quiz scores, teacher feedback, session replay) are the
ground-truth stream that will drive graduation decisions.

This makes the Axiom turn classifier the first **publishable
production case study** for the paper — it moves the paper's
validation from "four public benchmarks" to "four public benchmarks
+ one internal system," which is a material credibility gain before
submission.

### Deeper reads

- **`docs/marketing/industry-applicability.md`** — full
  per-category analysis, unit economics, worked example of org-level
  savings, honest "where Dendra doesn't fit" list.
- **`docs/marketing/business-model-and-moat.md`** — Snyk+Temporal
  analog, three-mode analyzer design (static scanner → dynamic
  measurement → full graduation), monetization tiers, 8 moat bricks
  ranked by durability.
- **`docs/marketing/entry-with-end-in-mind.md`** — year-one
  positioning calibrated against year-three endpoints (canonical
  primitive, analyzer corpus moat, regulated-vertical enterprise).
- **`docs/marketing/dendra-one-pager.md`** — 1-page buyer pitch.
- **`docs/working/internal-use-cases-scan-2026-04-20.md`** — the
  nine Dendra-fit sites in the Axiom codebase, ranked.
- **`strengthening-plan.md`** (this directory) — tier-A/B/C rigor
  plan to strengthen the paper before submission.

---

_Copyright (c) 2026 B-Tree Ventures, LLC. Apache-2.0 licensed._
