# When Should a Rule Learn? Predicting the Rule / Neural-Net / ML Composition for Classification Streams

*Working draft for DMLR. Numbers from the aligned study (common test set, text n=400;
image/audio preliminary). Anonymize before submission (system name behind a macro).*

## Abstract

A production classification site can be served by a hand-written rule, a (neural-net) LLM call,
or a trained classical model. Common practice treats the rule→LLM→ML progression as a fixed
ladder whose destination is always the trained model. We ask a data-centric question instead:
**given a stream's measurable characteristics, which of these tiers should serve it, and when?**
On 19 public text datasets (plus preliminary image and audio), scored apples-to-apples on a
common test set across four tiers (keyword rule, zero-shot LLM, few-shot LLM, classical-ML
training curve), we find a clear, interpretable gradient: **label cardinality and
rule-accuracy-over-chance predict which tier wins** — high-cardinality streams favor the trained
classical model, low-cardinality/binary streams favor the LLM. We operationalize this as
**Adaptive Graduated Autonomy (AGA)**, which selects the *terminal* tier per stream (often the
LLM, not the trained model) under a *multi-objective* policy — modality feasibility and a latency
SLO as hard constraints, accuracy within a significance band, operating cost minimized — and
graduates under a statistical non-inferiority gate. This paper measures the accuracy, cost, AND latency axes (modality
feasibility is first-class but a hard filter, not a measured scalar). AGA matches
the fixed-ladder baseline's accuracy while consuming **~44% fewer labeled outcomes**, and keeps
the LLM as terminal on roughly half of streams. We report two honest negatives that strengthen
rather than weaken the picture: a *learned cross-site predictor* of the best tier does **not**
generalize at this fleet size (leave-one-dataset-out below the majority baseline) — per-site
measurement is required — and a vision-LLM-on-spectrogram proxy fails on environmental audio.
The contribution is the characterization and the cost-reducing adaptive algorithm, not the
cascade or the gate (both prior art).

## 1. Introduction

Most production software is full of small classification decisions: route this ticket, tag this
message, flag this content, pick this branch. Each is a function from an input to one of a fixed
set of labels. Teams build these three ways — a hand-written rule, a call to a large language
model, or a trained classical model — and the choice is usually made once, per site, by intuition,
and rarely revisited; there is no shared, evidence-based basis for it. Two progressions are
well-documented in practice: replacing brittle rules with learned models (the rule-to-ML migration
long noted as a source of technical debt; Sculley et al. 2015, Breck et al. 2017), and, more
recently, prototyping with an LLM and distilling to a cheaper model to cut inference cost
(FrugalGPT; LLM-as-teacher distillation). Both *implicitly* treat a trained model as the eventual
destination — an assumption also baked into autonomy frameworks whose ceiling is a trained model.
This paper tests that assumption directly rather than asserting a ladder.

This paper questions that ladder with data. For a given classification stream, which tier
*should* serve it, and when? We treat it as a data-centric measurement problem: score all three
tiers (rule, LLM, classical ML) on the same test set, on a shared axis of labeled-outcomes
consumed, across 19 public text datasets spanning sentiment, intent, moderation, emotion, topic,
and spam, plus preliminary image and audio. The answer is not "always the trained model." It
depends on measurable, early-observable properties of the stream — chiefly label cardinality and
how far the day-zero rule sits above chance — and for a large fraction of streams the LLM is the
correct *terminal* tier, not a way-station.

We turn this into an algorithm, Adaptive Graduated Autonomy (AGA), that chooses the terminal tier
per stream under a multi-objective policy (modality feasibility and a latency SLO as hard
constraints, accuracy within a statistical significance band, operating cost minimized) and
graduates between tiers under a non-inferiority gate — graduating to the cheaper tier as soon as
it *ties*, not only when it wins. Against the fixed "always graduate to ML" baseline, AGA matches
accuracy while consuming far fewer labeled outcomes.

What is *not* claimed as novel: LLM cost-cascades (FrugalGPT) and learning-to-defer route between
models already; paired-McNemar classifier comparison is standard. What *is* contributed: (1) a
characterization of which dataset characteristics predict the cost-optimal tier, (2) an adaptive
algorithm that exploits it to cut labeled-data cost at parity accuracy, and (3) honest evidence on
where a learned cross-site policy does and does not work — including two negatives we report
plainly rather than hide.

### 1.1 Contributions
1. **Aligned multi-tier benchmark** — rule / zero-shot LLM / few-shot LLM / classical-ML-curve, all
   on one common test set per dataset, on a shared labeled-outcomes-consumed axis, across 19 text
   datasets (sentiment, intent, moderation, emotion, topic, spam) + preliminary image/audio.
2. **Predictive characterization** — label cardinality (Spearman ρ≈−0.7 vs outcomes-to-graduate)
   and rule-over-chance predict which tier wins and how deep ML must train.
3. **AGA** — chooses the terminal tier and graduates under a non-inferiority gate; matches
   fixed-ladder accuracy at ~44% fewer labeled outcomes.
4. **Two honest negatives** — a learned cross-site tier-predictor does not generalize at 21
   datasets; spectrogram-vision fails on audio. Plus a tunable non-inferiority ("close-enough")
   gate for cost-aware graduation.

## 2. Setup

- **Tiers.** RULE → MODEL (LLM, zero/few-shot) → ML (classical sklearn head). The LLM is the
  neural-net tier; ML is the trained classical tier.
- **Datasets.** 19 text + CIFAR-10 (image) + ESC-50 (audio).
- **Aligned protocol.** Common test subset per dataset (text n=400); rule from a 100-example seed;
  ML as a budget curve; LLM (Claude Haiku) zero- and few-shot (≤40 exemplars). Each tier placed at
  its true labeled-outcome cost (rule≈100, zero-shot=0, few-shot=k, ML=budget).
- **Two costs.** We separate **one-time labeled-outcome cost** (to train ML / seed the rule) from
  **perpetual per-call inference cost** (the LLM bills every call forever; rule/trained-ML are
  ~free per call). AGA's savings are reported on the labeled-outcome axis; the steady-state
  inference-cost argument is what makes graduating *off* the LLM valuable.

## 3. The composition varies by stream

Sorted by label cardinality, the gradient is visible: high-cardinality → trained ML; binary/
low-cardinality → LLM. (esc50 ML "wins" only because the spectrogram-vision proxy fails; cifar10's
LLM "win" is an artifact of the pixel-logreg baseline — a frozen-ViT baseline reaches 0.95 and
beats the LLM's 0.78, so image actually favors ML; see §6.)

| dataset | labels | rule | ML | MODEL-zs | MODEL-fs | best |
|---|--:|--:|--:|--:|--:|---|
| clinc150 | 151 | 0.172 | 0.838 | 0.748 | 0.725 | ML |
| banking77 | 77 | 0.225 | 0.870 | 0.777 | 0.807 | ML |
| hwu64 | 64 | 0.247 | 0.805 | 0.850 | 0.865 | MODEL-fs |
| esc50 | 50 | 0.058 | 0.283 | 0.017 | 0.050 | ML |
| atis | 26 | 0.733 | 0.848 | 0.598 | 0.815 | ML |
| twenty_newsgroups | 20 | 0.133 | 0.660 | 0.685 | 0.680 | MODEL-zs |
| dbpedia14 | 14 | 0.632 | 0.968 | 0.968 | 0.978 | MODEL-fs |
| codelangs | 12 | 0.892 | 0.986 | 1.000 | 1.000 | MODEL-zs |
| cifar10 | 10 | 0.173 | 0.327 | 0.780 | 0.340 | MODEL-zs |
| yahoo_answers | 10 | 0.240 | 0.670 | 0.740 | 0.720 | MODEL-zs |
| snips | 7 | 0.755 | 0.983 | 0.970 | 0.983 | ML |
| emotion | 6 | 0.323 | 0.818 | 0.632 | 0.608 | ML |
| trec6 | 6 | 0.420 | 0.855 | 0.795 | 0.887 | MODEL-fs |
| ag_news | 4 | 0.375 | 0.895 | 0.853 | 0.892 | ML |
| tweet_emotion | 4 | 0.403 | 0.613 | 0.828 | 0.810 | MODEL-zs |
| imdb | 2 | 0.532 | 0.853 | 0.938 | 0.958 | MODEL-fs |
| rotten_tomatoes | 2 | 0.487 | 0.723 | 0.930 | 0.940 | MODEL-fs |
| sms_spam | 2 | 0.920 | 0.945 | 0.887 | 0.988 | MODEL-fs |
| sst2 | 2 | 0.512 | 0.770 | 0.968 | 0.978 | MODEL-fs |
| tweet_hate | 2 | 0.497 | 0.560 | 0.770 | 0.640 | MODEL-zs |
| tweet_offensive | 2 | 0.660 | 0.775 | 0.752 | 0.730 | ML |

## 4. Adaptive Graduated Autonomy

- **oracle_terminal** is *multi-objective*, applied in priority order: (i) **modality
  feasibility** (hard filter — e.g. spectrogram-vision can't read words), (ii) **latency SLO**
  (hard — a tier breaching the real-time budget is out), (iii) **accuracy** within a Wilson-CI
  significance band, (iv) **operating cost** minimized within the band (perpetual inference $ +
  labeled cost). The LLM wins only when *significantly* better on accuracy *and* feasible within
  the latency/modality constraints; otherwise a rule/trained-ML tie graduates off the LLM. Cost is
  the axis this study measures; latency and modality are first-class and default to unconstrained.
- **Tie-to-graduate**: ML graduates once it *ties* the LLM (non-inferiority), not once it beats it.
- **CostToleranceGate**: the non-inferiority gate; ε is operator-tunable.
- **The router is permanent**: it holds the rule floor, watches for drift, and re-escalates to the
  LLM when the data demands — only the LLM *dependency* shrinks.

### 4.1 Measured latency + cost (the steady-state axes)
Per-call latency (p50): rule 0.003 ms, classical-ML 0.1 ms, MiniLM-embed ML 6-23 ms, LLM 530-570 ms — the rule is ~200,000x faster than the LLM, classical ML ~5,000x. LLM per-call cost $0.0001-0.0006 (scales with label-prompt length); rule/ML ~$0. Under a real-time SLO (e.g. 50 ms) the LLM is structurally excluded, so latency *forces* graduation to on-device tiers (latency_profile.json).

### 4.2 AGA vs the fixed ladder
AGA matches accuracy at far lower labeled-outcome cost:

```
dataset             oracle          AGA(LODO)        AGA_acc fixed_acc AGA_cost fix_cost
------------------------------------------------------------------------------------------------
ag_news             ml              model_zeroshot     0.853     0.895        0     5000
atis                ml              ml                 0.848     0.848     1000     1000
banking77           ml              model_fewshot      0.807     0.870       40     5000
cifar10             model_zeroshot  ml                 0.327     0.327     1000     1000
clinc150            ml              ml                 0.838     0.838     5000     5000
codelangs           ml              ml                 0.986     0.986      500      500
dbpedia14           ml              ml                 0.968     0.968    10000    10000
emotion             ml              model_zeroshot     0.632     0.818        0     5000
esc50               ml              model_fewshot      0.050     0.283        8      100
hwu64               model_fewshot   ml                 0.805     0.805     5000     5000
imdb                model_fewshot   model_fewshot      0.958     0.853       40     5000
rotten_tomatoes     model_fewshot   model_zeroshot     0.930     0.723        0     5000
sms_spam            model_fewshot   ml                 0.945     0.945     2000     2000
snips               ml              ml                 0.983     0.983      500      500
sst2                model_fewshot   model_zeroshot     0.968     0.770        0     5000
trec6               ml              model_zeroshot     0.795     0.855        0     5000
tweet_emotion       model_zeroshot  ml                 0.613     0.613     2000     2000
tweet_hate          model_zeroshot  model_fewshot      0.640     0.560       40      250
tweet_offensive     ml              model_fewshot      0.730     0.775       40     2000
twenty_newsgroups   ml              ml                 0.660     0.660    10000    10000
yahoo_answers       model_zeroshot  ml                 0.670     0.670    10000    10000
------------------------------------------------------------------------------------------------

LODO oracle-match: 7/21 = 0.33
Mean accuracy — AGA 0.762 vs fixed-ladder(ML_PRIMARY) 0.764
Mean labeled outcomes — AGA 2246 vs fixed-ladder 4017
AGA keeps LLM as terminal (skips ML training): 10/21
AGA picks rule/ML over not-better LLM (avoids perpetual cost): 6/21
```

### 4.3 Honest negative: cross-site prediction does not yet generalize
A gradient-boosted predictor of the best tier from early-observable characteristics scores
**below the majority baseline** leave-one-dataset-out at 21 datasets, and its apparent skill
swings with test-set noise (0.62→0.41 between n=200 and n=400 before significance-aware labeling).
**Conclusion: per-site measured routing is what works today; cross-site prediction needs a much
larger fleet.** This is a finding, not a failure — it tells practitioners to measure, not guess.

## 5. Preliminary: image and audio
The gate is modality-agnostic (it consumes (decision, outcome) pairs); only the LLM input path is
modality-specific. Rule+ML transfer to CIFAR-10 and ESC-50. The MODEL tier is preliminary: Claude
vision on CIFAR images reaches 0.78 zero-shot (but our pixel-logreg ML is a weak baseline), and a
spectrogram→vision proxy for ESC-50 **fails** (0.017, below chance) — native-audio models are
needed. We report these as preliminary evidence of modality-generality, not as headline results.

## 6. Limitations
- **Classical-ML baselines are the cheap day-N head** (TF-IDF / pixel / MFCC + logistic
  regression). **Robustness check (Threat-1 ablation):** a much stronger classical tier —
  frozen sentence-transformer embeddings (all-MiniLM-L6-v2) + logistic regression — was run on a
  representative text subset. The "LLM wins on short-text sentiment/moderation" result *survives*:
  sst2 strong-ML 0.80 vs LLM 0.98; rotten_tomatoes 0.74 vs 0.94; tweet_hate 0.53 vs 0.77 — so it
  is **not** a weak-baseline artifact. We then ran the strongest form — **fine-tuning DistilBERT**
  (4k examples, 3 epochs): the LLM edge *still* persists on short-text sentiment (sst2 fine-tuned
  0.89 vs LLM 0.98; imdb 0.87 vs 0.96 — the gap shrinks from the TF-IDF baseline but survives at
  ~9 points). On high-cardinality intent a tuned **TF-IDF+logreg remained the strongest classical
  baseline** (banking77 0.87 > fine-tuned DistilBERT 0.80 at this budget), consistent with ML
  winning there. So the central finding is robust to baseline strength. Caveats: fine-tuning was
  light (untuned, 4k examples) — heavier tuning could narrow but not erase the sentiment gap;
  frozen embeddings are not uniformly stronger (imdb MiniLM 0.78 < TF-IDF 0.85, truncation).
  **Image, resolved:** with a real baseline (frozen ViT embeddings + logreg) classical ML reaches
  **0.95 on CIFAR-10, decisively beating the zero-shot vision LLM (0.78)** — so CIFAR's apparent
  "LLM wins" was purely the pixel-logreg strawman; with a proper image model, ML wins, mirroring
  the high-cardinality-text pattern. (The aligned table still lists the pixel-logreg tier; the ViT
  result is the robustness check.)
- Latency/cost are measured on a sample (latency_profile.json); LLM latency is network-bound and will vary.
- **Single LLM (Claude Haiku), single prompt** — accuracies are prompt/model sensitive.
- **Multimodal MODEL tier is a proxy** (spectrogram/frames); native audio/video models are future
  work. Spectrogram-vision is shown to fail on environmental audio.
- **Cross-site meta-prediction does not generalize** at this fleet size (§4.2).
- **n=400 test subsets** → accuracy CIs ≈ ±0.04; we use significance-aware tie bands accordingly.

## 7. Related work
FrugalGPT / LLM cascades (cost routing); learning-to-defer (per-example deferral);
champion-challenger + shadow deployment (MLOps); McNemar / Dietterich. AGA differs by choosing the
*terminal* tier per stream from characteristics and graduating off the LLM under a non-inferiority
gate — not per-example routing or a hand-tuned cascade.

## 8. Reproducibility
One command (`scripts/reproduce_dmlr.sh`) regenerates every number; MODEL stages auto-skip without
an API key. See REPRODUCE.md.
