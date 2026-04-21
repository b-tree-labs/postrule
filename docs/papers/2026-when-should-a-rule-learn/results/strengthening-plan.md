# Strengthening the Dendra Study — Rigor Plan

**Companion to:** `findings.md`.
**Audience:** paper reviewers, internal validators.
**Generated:** 2026-04-20.

---

## Why this document exists

A reviewer's version of "where are the cracks?" Everything below is
something we can measurably do now; cost and expected paper-uplift is
flagged per item.

Priority = (paper risk if not done) × (ease).

---

## Tier A — must do before submission

### A1. Per-example prediction logging + McNemar's paired test
**Gap:** We report accuracy per checkpoint but not per-example rule
vs ML outputs. That forces the unpaired z-test (conservative).
**Fix:** extend `BenchmarkCheckpoint` with `rule_predictions` and
`ml_predictions` lists (test-row order). Add a `mcnemar_p_value`
helper. Re-report transition depths with paired test.
**Cost:** half a day.
**Impact:** tightens the statistical story for reviewers; lets us
quote "ML beats rule on N more examples than it loses on, p < X."

### A2. Multiple training-order seeds (reproducibility)
**Gap:** Each benchmark was run with one ordering of the training
stream. Dendra outcomes could be sensitive to order.
**Fix:** run each benchmark 5× with different shuffles of the
training list, fixed seeds (`seed={1..5}`). Report transition depth
and final accuracy as **mean ± stddev**.
**Cost:** one day (15 runs with the current CLI; batch via a shell
loop).
**Impact:** reviewer confidence. Also catches "is there one unlucky
seed where the crossover doesn't happen?"

### A3. Stronger ML head as an alternative baseline
**Gap:** TF-IDF + LogReg is simple, but a reviewer will ask "does
the transition curve shape depend on ML capacity?"
**Fix:** add `SentenceTransformerHead` (uses `sentence-transformers`
with a logistic head on the embedding) as an opt-in ML-head. Run
all four benchmarks with it. Expected: ML ceiling rises, transition
depth shifts *earlier*.
**Cost:** two days (adapter + runs).
**Impact:** shows the transition-curve pattern is robust to ML-head
choice. Lets us plot both on the same figure.

### A4. Hand-authored rule comparison (narrow-domain only)
**Gap:** Our auto-rule might be weaker than what a careful engineer
would write.
**Fix:** hand-author a keyword rule for ATIS based on the first 100
training examples (transparent, in the paper's appendix). Re-run
and report both auto-rule and hand-rule baselines side by side.
**Cost:** half a day.
**Impact:** defuses the "your rule is a straw-man" reviewer
critique for the narrow-domain regime.

### A5. Fixed random seeds + pinned dataset versions
**Gap:** HuggingFace datasets can drift; our results must be
byte-exact reproducible.
**Fix:** pin HF revision hashes in the benchmark loaders, set
`numpy.random.seed()` at runner entry, document every version. Add
a `reproduce.sh` script.
**Cost:** few hours.
**Impact:** mandatory for any serious ML venue.

---

## Tier B — substantially strengthens the paper

### B1. Cross-validation instead of single test split
**Gap:** Each benchmark has one train/test split. Single-split
variance isn't quantified.
**Fix:** 5-fold CV on each benchmark. Report accuracy as mean ± SD
over folds.
**Cost:** 1–2 days (training is cheap; pipeline rework).
**Impact:** tighter accuracy CIs, defensible transition-depth error
bars.

### B2. F1 / Precision-Recall / per-class analysis
**Gap:** Accuracy alone can mislead on imbalanced benchmarks
(CLINC150 has an out-of-scope class inflating chance levels).
**Fix:** add macro-F1 + per-class precision/recall to checkpoints.
Report per-class crossover depths.
**Cost:** one day.
**Impact:** addresses the "rule is hiding behind majority-class
prediction" reviewer question. Also helpful for the "category
taxonomy" section §6 of the paper.

### B3. Cost-asymmetric evaluation
**Gap:** accuracy treats all errors equally. Real production
systems have different false-positive/false-negative costs.
**Fix:** add a `cost_matrix` parameter to the runner; report
weighted loss at each checkpoint. Demonstrate with a moderation-
style 2×2 matrix on Banking77 (reclassify a subset as safe/unsafe).
**Cost:** 1–2 days.
**Impact:** speaks directly to the paper's §7.1 safety-critical
claim — gives concrete numbers for Phase 4 cap decisions.

### B4. Per-example confidence distributions
**Gap:** §6 category taxonomy claims transition depth is predictable
from dataset attributes but we don't show the mechanism.
**Fix:** save rule/ML/(LLM) confidence per example at each
checkpoint. Plot confidence-histogram over time — show how ML
confidence *separates* as training accumulates.
**Cost:** half a day (data) + plotting time.
**Impact:** a compelling secondary figure. Opens §6 to an
interpretability angle.

### B5. LLM-shadow experiments with a competent model
**Gap:** Our only Phase 1 probe was llama3.2:1b (0% on ATIS). We
claim §9.3 future work for LLM-as-shadow but have no positive data.
**Fix:** run Phase 1 with **claude-haiku-4-5**, **gpt-4o-mini**, or
a local **llama-3.3-70B-instruct** on at least ATIS + CLINC150.
Report LLM vs rule vs ML. Expected: LLM trivially beats rule on
ATIS; may not catch up to a trained ML head on CLINC150.
**Cost:** API key + $10–50 in inference; one day.
**Impact:** turns §9.3 from future-work into headline result.

### B6. A fifth benchmark from a different domain
**Gap:** All four benchmarks are intent classification in English.
**Fix:** add a content-moderation benchmark (e.g., **Jigsaw
Toxicity**, **HateXplain**) or a code-classification benchmark
(e.g., **CodeSearchNet intent**). Runs end-to-end with the existing
runner.
**Cost:** 1–2 days (loader + run).
**Impact:** general-classification claim is no longer bounded by one
task family.

### B7. Add production case study (Axiom turn-intent classifier)
**Gap:** Only public data. Reviewers love internal case studies.
**Fix:** integration currently in flight (see parent session). Run
Dendra on the classroom turn classifier for 30 days of production
traffic, report a production transition curve.
**Cost:** integration + 30-day observation window.
**Impact:** "we tested this on our own product" = major credibility
gain.

---

## Tier C — nice to have / future papers

### C1. Power analysis
Given test-set size n, what's the smallest true gap (effect size)
we can detect at α=0.01? Helps interpret why ATIS's +19pp gap was
easy vs a hypothetical +1pp gap.

### C2. Stability under distribution shift
Add a synthetic drift test: after Phase 4, inject out-of-distribution
examples. Does the circuit breaker trip? How fast?

### C3. Multi-label extension (for real-world applicability)
Our current API is single-label. Real production classifiers are
often multi-label (the Axiom turn-classifier is). Extend the API +
re-run where datasets support multi-label.

### C4. Federated training arm
Aggregate outcome-logs across simulated "orgs" and show federated ML
beats any single-org ML at equal training volume. Expected to feed
a 2027 follow-up paper.

### C5. Transition-depth regression model (paper §6)
Currently §6's regression is a placeholder. With B2 + B4 data, fit
the model explicitly: `depth ~ cardinality + label_entropy + rule_acc`.
Report R² and residuals.

### C6. Adversarial / poisoning robustness
What happens when the outcome stream is intentionally corrupted?
Does Dendra's safety theorem hold? Quantify.

---

## Proposed sequencing

- **Week 1 (this week):** A1, A2, A5. One half-day each.
- **Week 2:** A3, A4, B1, B2.
- **Week 3:** B3, B4, B5 (needs API key budget).
- **Week 4:** B6, B7 kickoff.
- **Post-submission:** C1–C6 as follow-ups.

Every Tier-A item should land before arXiv preprint. Tier-B items
stretch the submission into a stronger paper. Tier-C items form the
content of paper 2 (2027).

---

## What doesn't need changing

- The two-regime conclusion is robust (seed-size sensitivity done).
- The six-phase lifecycle is stable — no reviewer has cause to
  complain about taxonomy choices given the paper's clarity.
- Apache-2.0 license + Dendra as reference implementation is
  uncontroversial.
- The dataset choice (Banking77/CLINC150/HWU64/ATIS) is standard;
  reviewers will not ask for more intent-classification benchmarks.

---

_Copyright (c) 2026 B-Tree Ventures, LLC. Apache-2.0 licensed._
