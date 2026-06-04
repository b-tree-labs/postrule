# Adaptive Graduated Autonomy: Predicting the Rule / Neural-Net / ML Composition for Classification Streams

*Working draft for DMLR (Journal of Data-centric Machine Learning Research). Numbers are
from the 19-dataset aligned study (common 200-example test set); a larger-n re-run is
planned to tighten the oracle. Anonymize before submission (system name behind a macro).*

## Abstract

Production classification sites begin as hand-written rules, are often replaced by a large
language model (LLM) call, and may eventually graduate to a trained classical model. Existing
practice treats this as a fixed linear ladder that always marches toward a trained model as
the destination. We ask a data-centric question instead: **given a data stream's measurable
characteristics, which composition of {rule, LLM, trained ML} is correct, and when?** Across
19 public text classification datasets — scored apples-to-apples on a common test set with
four decision-maker tiers (keyword rule, zero-shot LLM, few-shot LLM, classical ML over a
training-budget curve) — we find that two early-observable characteristics, **label
cardinality** and **rule-accuracy-over-chance**, together with the accumulated labeled-outcome
budget, predict the cost-optimal tier (0.62 leave-one-dataset-out, vs 0.51 majority baseline).
We turn this into **Adaptive Graduated Autonomy (AGA)**, an algorithm that selects the
*terminal* tier per stream and graduates between tiers under a statistical gate, rather than
always advancing to the trained model. Against the fixed-ladder baseline, AGA delivers higher
mean accuracy (0.836 vs 0.816) at 64% fewer labeled outcomes (1,409 vs 3,934), and keeps the
LLM as the terminal decision-maker on 12 of 19 streams — precisely the streams where a trained
model never reliably matches it. A tunable non-inferiority margin lets operators trade a
bounded accuracy give-up for shedding the LLM's perpetual per-call cost.

## 1. Introduction

(Adapted from the prior draft's framing — keep the "every production codebase has classify
functions" motivation, drop product/commercial language.)

The contribution is **not** the cascade itself (FrugalGPT-style cost cascades and
learning-to-defer route between models already) and **not** the statistical gate (paired
McNemar comparison of classifiers is standard). The contribution is **the characterization**:
what predicts the right composition, demonstrated across task families and modalities, plus an
algorithm and a learned, continually-refined policy that operationalize it.

### 1.1 Contributions
1. An **aligned multi-tier benchmark**: rule / zero-shot LLM / few-shot LLM / classical-ML-curve,
   all scored on one common test set per dataset, positioned on a shared labeled-outcomes-consumed
   axis, across 19 text datasets spanning sentiment, intent, moderation, emotion, topic, and spam
   (plus image and audio modalities via the rule+ML tiers).
2. A **predictive-characteristics result**: label cardinality (Spearman ρ=−0.73 vs
   outcomes-to-graduate) and rule-over-chance predict both transition depth and the cost-optimal
   terminal tier.
3. **AGA**, an adaptive phase-gate algorithm that chooses the terminal tier and graduates under a
   gate, beating the fixed ladder on accuracy *and* labeled-outcome cost.
4. A **non-inferiority ("close-enough") gate** exposing a single cost-tolerance knob, and a
   continually-refined meta-policy.

## 2. Setup

- **Tiers / phases.** RULE → MODEL (LLM: zero-shot / few-shot) → ML, mapped to the lifecycle
  phases. The LLM is a neural-net decision-maker; ML is a classical sklearn head.
- **Datasets (19).** banking77, clinc150, hwu64, atis, snips, trec6, ag_news, codelangs, sst2,
  imdb, rotten_tomatoes, tweet_eval{offensive,hate,emotion}, dair-ai/emotion, dbpedia14,
  yahoo_answers, 20newsgroups, sms_spam. Modality generalization (image/audio) via CIFAR-10 and
  ESC-50 on rule+ML.
- **Aligned protocol.** Common 200-example test subset per dataset; rule from a 100-example seed;
  ML as a curve over training budget; LLM (Claude Haiku) zero-shot and few-shot (≤40 exemplars).
  All tiers placed on the labeled-outcomes-consumed axis (rule≈100, zero-shot=0, few-shot=k,
  ML=budget).

## 3. The composition varies by stream (results)

[Table: per-dataset rule / ML / model_zs / model_fs accuracies — from llm_tiers.json + aligned/.]
Key reading: classical ML wins on high-cardinality intent (banking77 0.875 vs LLM 0.81); the LLM
wins decisively on low-cardinality sentiment/moderation (sst2 0.78→0.975, imdb 0.895→0.975); few-
shot ≥ zero-shot almost everywhere.

## 4. Adaptive Graduated Autonomy

- **oracle_terminal** (cost-aware): highest-accuracy tier; among those within ε, prefer cheapest
  to operate. The LLM wins only when strictly >ε better.
- **Tie-to-graduate** and **crossover**: ML graduates once it *ties* (non-inferiority) the LLM,
  not once it beats it — because trained ML is far cheaper per call.
- **CostToleranceGate**: the non-inferiority gate; the ε knob is operator-tunable.
- **AGAMetaPolicy**: predicts the terminal tier from early-observable characteristics; continually
  refined as new streams append rows.

### 4.1 AGA vs the fixed ladder
Mean accuracy 0.836 vs 0.816; mean labeled outcomes 1,409 vs 3,934 (−64%); LLM kept as terminal on
12/19. [Table from evaluate_aga.py.]

### 4.2 Meta-policy generalization
Four-tier leave-one-dataset-out: 0.623 (vs 0.51 majority). Drivers: log label-cardinality (0.42),
rule-over-chance (0.26), outcome budget (0.18). [Feature-importance figure.]

## 5. Limitations (honest)
- Early-only terminal prediction is weak (0.32 oracle-match); the budget-aware policy is stronger
  (0.62). Oracle noisy at n=200 — larger-n re-run planned.
- MODEL tier is text-only so far; audio/video MODEL requires a multimodal LLM (the gate is
  modality-agnostic; only the LLM input differs).
- Single LLM (Haiku); single classical head family.

## 6. Related work
FrugalGPT / LLM cascades (cost routing); learning-to-defer (per-example deferral);
champion-challenger + shadow deployment (MLOps); McNemar / Dietterich (classifier comparison).
AGA differs: a *learned, continually-refined, cross-stream* policy that predicts the right
composition from data-stream characteristics and chooses the terminal tier, rather than routing
per example or hand-tuning a cascade.

## 7. Reproducibility
All loaders, the aligned harness, AGA, the gate, and the meta-classifier are released; every
number regenerates from `scripts/aligned_tier_eval.py`, `scripts/evaluate_aga.py`,
`scripts/build_meta_classifier.py`, and `scripts/analyze_dmlr_characteristics.py`.
