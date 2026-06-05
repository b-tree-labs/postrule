# When Should a Rule Learn? Predicting the Rule, Neural-Net, and ML Composition for Classification Streams

*Working draft for DMLR. Numbers from the aligned study (common test set, text n=400; image and audio preliminary). Anonymize before submission (system name behind a macro).*

## Abstract

A production classification site can be served by a hand-written rule, a large language model (LLM) call, or a trained classical model. Common practice treats the rule-to-LLM-to-ML progression as a fixed ladder whose destination is the trained model. We ask a different question. Given a stream's measurable characteristics, which of these tiers should serve it, and when? We score four tiers (keyword rule, zero-shot LLM, few-shot LLM, and a classical-ML training curve) on a common test set across 19 public text datasets, with preliminary image and audio. Two early-observable properties, label cardinality and rule accuracy over chance, predict which tier wins: high-cardinality streams favor the trained classical model, while binary and low-cardinality streams favor the LLM. We operationalize this as Adaptive Graduated Autonomy (AGA), which selects the terminal tier per stream under a multi-objective policy (modality feasibility and a latency budget as hard constraints, accuracy within a significance band, operating cost minimized) and graduates between tiers using a statistical non-inferiority gate. The study measures the accuracy, cost, and latency axes; modality feasibility enters as a hard filter rather than a measured scalar. AGA matches the fixed-ladder baseline's accuracy while using about 44% fewer labeled outcomes, and it keeps the LLM as the terminal tier on roughly half of the streams. We report two negative results. A learned cross-site predictor of the best tier does not generalize at this number of datasets, so per-site measurement is required. And a vision-LLM-on-spectrogram proxy fails on environmental audio. The contribution is the characterization and the cost-reducing adaptive algorithm, not the cascade or the gate, which are prior art.

## 1. Introduction

Most production software contains many small classification decisions: route this ticket, tag this message, flag this content, pick this branch. Each is a function from an input to one of a fixed set of labels. Teams build these three ways, with a hand-written rule, an LLM call, or a trained classical model. The choice is usually made once per site, by intuition, and rarely revisited; there is no shared, evidence-based basis for it. Two progressions are documented in practice. The first replaces brittle rules with learned models, a migration long noted as a source of technical debt (Sculley et al. 2015; Breck et al. 2017). The second prototypes with an LLM and distills to a cheaper model to cut inference cost (FrugalGPT; LLM-as-teacher distillation). Both implicitly treat a trained model as the destination, an assumption also built into autonomy frameworks whose ceiling is a trained model. We test that assumption with data rather than assert a ladder.

For a given classification stream, which tier should serve it, and when? We treat this as a measurement problem. We score all three tiers (rule, LLM, classical ML) on the same test set, on a shared axis of labeled outcomes consumed, across 19 text datasets spanning sentiment, intent, moderation, emotion, topic, and spam, with preliminary image and audio. The answer is not "always the trained model." It depends on measurable properties of the stream, chiefly label cardinality and how far the day-zero rule sits above chance. For a large fraction of streams the LLM is the right terminal tier, not a way-station.

We turn this into an algorithm, Adaptive Graduated Autonomy (AGA), that chooses the terminal tier per stream under a multi-objective policy (modality feasibility and a latency budget as hard constraints, accuracy within a significance band, operating cost minimized). It graduates between tiers using a non-inferiority gate, moving to the cheaper tier as soon as that tier ties, not only when it wins. Against the fixed "always graduate to ML" baseline, AGA matches accuracy while using far fewer labeled outcomes.

We do not claim the mechanism as novel. LLM cost-cascades (FrugalGPT) and learning-to-defer already route between models, and paired-McNemar comparison of classifiers is standard. The contributions are: (1) a characterization of which dataset properties predict the cost-optimal tier; (2) an algorithm that exploits it to cut labeled-data cost at equal accuracy; and (3) evidence on where a learned cross-site policy does and does not work, including two negative results.

### 1.1 Contributions

1. An aligned multi-tier benchmark. Rule, zero-shot LLM, few-shot LLM, and a classical-ML curve, all scored on one common test set per dataset, on a shared labeled-outcomes axis, across 19 text datasets and preliminary image and audio.
2. A predictive characterization. Label cardinality (Spearman ρ ≈ −0.7 against outcomes-to-graduate) and rule-over-chance predict which tier wins and how deep ML must train.
3. AGA, which chooses the terminal tier and graduates under a non-inferiority gate, matching fixed-ladder accuracy at about 44% fewer labeled outcomes.
4. A lifecycle-level safety bound. A cumulative Type-I bound over the whole graduation chain, not just one transition, with the rule as a persistent accuracy floor.
5. Two negative results. A learned cross-site tier-predictor does not generalize at 21 datasets, and spectrogram-vision fails on audio. We also give a tunable non-inferiority gate for cost-aware graduation.

## 2. Setup

Tiers. RULE, then MODEL (the LLM, zero- and few-shot), then ML (a classical sklearn head). The LLM is the neural-net tier; ML is the trained classical tier.

Datasets. 19 text datasets, plus CIFAR-10 (image) and ESC-50 (audio).

Aligned protocol. A common test subset per dataset (text n=400). The rule is built from a 100-example seed. ML is evaluated as a budget curve. The LLM (Claude Haiku) is run zero- and few-shot, with at most 40 exemplars. Each tier is placed at its true labeled-outcome cost: rule about 100, zero-shot 0, few-shot k, ML the training budget.

Two costs. We separate the one-time labeled-outcome cost (to train ML or seed the rule) from the perpetual per-call inference cost (the LLM bills every call; rule and trained ML are roughly free per call). AGA's savings are reported on the labeled-outcome axis. The steady-state inference cost is what makes graduating off the LLM valuable.

## 3. The composition varies by stream

Sorted by label cardinality, a gradient appears: high cardinality favors trained ML, while binary and low-cardinality favor the LLM. Two rows are artifacts addressed in Section 6. ESC-50 ML "wins" only because the spectrogram-vision proxy fails, and CIFAR-10's LLM "win" reflects the pixel-logreg baseline; a frozen-ViT baseline reaches 0.95 and beats the LLM's 0.78, so image in fact favors ML.

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

The terminal-tier selection is multi-objective, applied in priority order. First, modality feasibility, a hard filter (a spectrogram image cannot convey words, for instance). Second, the latency budget, a hard constraint; a tier that exceeds the real-time budget is removed. Third, accuracy, where tiers within a Wilson-CI significance band of the best feasible tier are retained. Fourth, operating cost, minimized within that band, counting perpetual inference cost and labeled cost. The LLM is chosen only when it is significantly more accurate and feasible under the latency and modality constraints; otherwise a rule or trained-ML tie graduates off the LLM. Cost is the axis this study measures. Latency and modality are first-class in the policy and default to unconstrained.

ML graduates once it ties the LLM under the non-inferiority gate, not once it beats it. The margin ε is operator-tunable. The router is permanent: it holds the rule floor, watches for drift, and re-escalates to the LLM when the data calls for it. What shrinks is the dependence on the LLM, not the router.

### 4.1 A lifecycle-level safety guarantee

The per-transition gate gives the standard guarantee that advancing to a worse-than-current tier has probability at most α, the McNemar Type-I error. That part is not novel; it is the test's definition applied to a promotion decision. The lifecycle-level statement is what separates AGA from a single gated comparison.

Proposition (cumulative safety). Consider a graduation trajectory of at most m gated transitions, each evaluated by a paired test at level α with non-inferiority margin ε. With probability at least 1 − mα, every advance is to a tier non-inferior (within ε) to the current one. Consequently the deployed accuracy stays within ε of the best previously certified tier, and because the rule is a persistent fallback, operating accuracy is bounded below by (rule accuracy − ε) throughout, for any m.

Proof sketch. Each gated advance to an inferior tier is a Type-I event with probability at most α. A union bound over at most m transitions bounds the probability of any such event by mα. Absent any Type-I event, each advance is within-ε non-inferior, so by induction the deployed accuracy stays within ε of the best certified tier, and the rule floor lower-bounds it. ∎

Remarks. The union bound is loose, and independence across transitions is an idealization, since the tests reuse the accumulating stream. If many transitions are expected, α should be spent with a correction such as α/m. The guarantee is on certified accuracy under the test's assumptions, not a distribution-free promise. What it adds over a single McNemar test is a composable safety statement for a multi-tier lifecycle, which is what a practitioner needs to let the system graduate unattended.

### 4.2 Measured latency and cost

Per-call latency (p50): rule 0.003 ms, classical ML 0.1 ms, MiniLM-embedding ML 6 to 23 ms, LLM 530 to 570 ms. The rule is roughly 200,000 times faster than the LLM, and classical ML about 5,000 times. LLM per-call cost is $0.0001 to $0.0006 and scales with the length of the label prompt; rule and ML are near zero. Under a real-time budget (for example 50 ms) the LLM is excluded by construction, so latency forces graduation to local tiers. See latency_profile.json.

### 4.3 AGA versus the fixed ladder

AGA matches accuracy at much lower labeled-outcome cost.

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
Mean accuracy: AGA 0.762 vs fixed-ladder (ML_PRIMARY) 0.764
Mean labeled outcomes: AGA 2246 vs fixed-ladder 4017
AGA keeps LLM as terminal (skips ML training): 10/21
AGA picks rule/ML over a not-better LLM (avoids perpetual cost): 6/21
```

### 4.4 Negative result: cross-site prediction does not yet generalize

A gradient-boosted predictor of the best tier, trained on the early-observable characteristics, scores below the majority baseline under leave-one-dataset-out at 21 datasets. Its apparent skill also moves with test-set noise, swinging from 0.62 to 0.41 between n=200 and n=400 before significance-aware labeling. We conclude that per-site measured routing is what works today, and that a learned cross-site predictor needs a much larger collection of streams. The practical reading is to measure rather than guess.

## 5. Preliminary results on image and audio

The gate is modality-agnostic, since it consumes (decision, outcome) pairs; only the LLM input path is modality-specific. The rule and ML tiers transfer to CIFAR-10 and ESC-50. The MODEL tier is preliminary. Claude vision on CIFAR images reaches 0.78 zero-shot, though the pixel-logreg ML used in the aligned table is a weak baseline (see Section 6). A spectrogram-to-vision proxy for ESC-50 fails, scoring 0.017, below chance, so a native-audio model is needed. We treat these as preliminary evidence that the lifecycle generalizes across modalities, not as headline results.

## 6. Limitations

The classical-ML tier is the cheap day-N head (TF-IDF, pixels, or MFCC features with logistic regression). To check whether the "LLM wins" results are an artifact of a weak baseline, we ran two stronger baselines. Frozen sentence-transformer embeddings (all-MiniLM-L6-v2) with logistic regression still lose to the LLM on short-text sentiment and moderation (sst2 0.80 vs 0.98; rotten_tomatoes 0.74 vs 0.94; tweet_hate 0.53 vs 0.77). A fine-tuned DistilBERT (4k examples, 3 epochs) narrows the gap but does not close it (sst2 0.89 vs 0.98; imdb 0.87 vs 0.96, about 9 points). On high-cardinality intent, a tuned TF-IDF and logistic regression remained the strongest classical baseline (banking77 0.87, above fine-tuned DistilBERT's 0.80 at this budget), consistent with ML winning there. The central finding therefore holds across baseline strength. Two caveats apply: the fine-tune was light and untuned, so heavier tuning could narrow but is unlikely to erase the sentiment gap, and frozen embeddings are not uniformly stronger (imdb MiniLM 0.78 is below TF-IDF's 0.85 because of truncation). For image, a real baseline resolves the ambiguity: frozen ViT embeddings with logistic regression reach 0.95 on CIFAR-10, above the zero-shot vision LLM's 0.78, so CIFAR's apparent "LLM win" was the pixel-logreg strawman and image favors ML, as high-cardinality text does. The aligned table still lists the pixel-logreg tier; the ViT number is the robustness check.

Other limitations. Latency and cost are measured on a sample (latency_profile.json), and LLM latency is network-bound and will vary. We use a single LLM (Claude Haiku) and a single prompt, so accuracies are sensitive to prompt and model. The multimodal MODEL tier is a proxy (spectrogram or sampled frames); native audio and video models are future work, and spectrogram-vision is shown to fail on environmental audio. The cross-site predictor does not generalize at this number of datasets (Section 4.4). With n=400 test subsets the accuracy confidence intervals are about ±0.04, and we use significance-aware tie bands accordingly.

## 7. Related work

FrugalGPT and related LLM cascades route for cost. Learning-to-defer routes per example. Champion-challenger and shadow deployment are standard MLOps. The paired-McNemar test follows Dietterich. AGA differs by choosing the terminal tier per stream from measurable characteristics and graduating off the LLM under a non-inferiority gate, rather than routing per example or hand-tuning a cascade.

## 8. Reproducibility

A single command, `scripts/reproduce_dmlr.sh`, regenerates every number. The MODEL stages skip automatically without an API key. See REPRODUCE.md.
