# Picking a Judge: A Verifier-Selection Methodology for Production Classification

**Working title.** Alternates: "Above-Chance Picking", "When Should an SLM Judge Your Classifier?".
**Author.** Benjamin Booth, B-Tree Labs.
**Status.** Outline v0.1 — 2026-04-26.
**Target.** arXiv post-launch (cs.LG / cs.CL); companion to "When Should a Rule Learn?".
**Substrate.** `docs/benchmarks/slm-verifier-bench.json` + `docs/benchmarks/slm-verifier-results.md`. Re-run via `python scripts/run_slm_verifier_bench.py`.

---

## Why a separate paper

The main paper ("When Should a Rule Learn?") establishes the graduated-autonomy lifecycle and the McNemar gate. Its $P_2$ / $P_4$ / $P_5$ transitions consume *verdicts*, which are produced by a verifier. The main paper takes "a verifier exists and is good enough" as a black-box assumption and ships an evaluated default (`qwen2.5:7b`, picked by the n=102 bench).

That assumption needs its own paper. Specifically:

1. **The picker formula is non-obvious.** We ship `above-chance = format_rate × max(0, 2·accuracy − 1)`, not multiplicative `format_rate × accuracy`. A model that produces 90% parseable judgments at 53% accuracy is *not* a useful verifier — it's a fast random-number generator wrapped in JSON. The picker formula encodes that intuition. Defending it formally is one section.
2. **The latency-feasibility cutoff matters.** DeepSeek-R1:7b is the highest-accuracy model in the bench (78%) but its 14-second p50 disqualifies it. The cost-quality-latency frontier is three-dimensional; production guidance has to reflect that.
3. **The claim generalizes by domain or it doesn't.** Our bench is ticket-triage. The companion paper should test cross-domain (banking intent, content moderation, clinical coding, security alerts) before claiming "qwen2.5:7b is the right shipped default."

The first two we can write today. The third is exactly what the launch will give us.

## Contribution sketch

1. **The above-chance picker formula** with a derivation showing why it strictly dominates multiplicative scoring on the format-vs-noise frontier.
2. **An n=102+ verifier benchmark** across 11 SLMs with format / accuracy / above-chance / multiplicative / p50/p99 latency, plus the regime split (parseable-but-noisy vs sparse-but-correct).
3. **A latency-feasibility cutoff** at p50 < 1s for production verifiers, with the cost analysis behind it (verifier runs on every classification at sample-rate × throughput).
4. **Production guidance** — what to ship as judge, what to ship as classifier-shadow, when the answer changes (low-cardinality domain → smaller model viable; high-stakes domain → reach for the frontier API).
5. **Open dataset + harness** so the community can extend coverage to their domains.

## Section sketch

### 1. Introduction

- The graduated-autonomy lifecycle eats verdicts; the verifier is the bottleneck. (Reference main paper §3 + §9.3.)
- Why "just use GPT-4" is the wrong default for production: cost, privacy, vendor lock, latency.
- Why "use a 1B local model" is also wrong: 0.0% accuracy on ATIS at zero-shot.
- The right question: which models, at which sizes, with which prompts, on which domains, are *operationally* good enough to drive production graduation?

### 2. Related work

- LLM-as-judge methodology — G-Eval (Liu et al., 2023), MT-Bench / *Judging LLM-as-a-Judge* (Zheng et al., 2023).
- Calibration — Guo et al. (2017) on miscalibration of modern NNs; Kuleshov et al. (2018).
- Verifier / reward-model literature — RLHF reward models (Christiano et al., 2017; Ouyang et al., 2022); process reward models (VersaPRM and Papailiopoulos's recent work).
- Cost-aware model selection — FrugalGPT (Chen et al., 2024); RouteLLM (Ong et al., 2024); Dekoninck et al. (2025) for routing/cascading unification.
- Eval harness design — Trivedy (2026, *Better Harness*); Martin (2023, Auto-Evaluator at LangChain).
- Format-following + structured output — JSON-mode literature, constrained decoding, Outlines/Guidance.

### 3. The above-chance picker formula

- Setup: a verifier returns `correct | incorrect | unknown`. Two failure modes: *format failure* (returns garbage; counted as `unknown`) and *content failure* (returns parseable verdict but wrong).
- Multiplicative score = `format_rate × accuracy_on_judged`. Treats high-format-low-accuracy as equivalent to low-format-high-accuracy. Formally wrong: a 100%-format / 50%-accuracy model is a pure random labeler; it contributes zero signal.
- Above-chance score = `format_rate × max(0, 2·accuracy − 1)`. Maps 50%-accuracy to zero contribution; 100% to `format_rate`. Linearly rewards above-chance signal.
- Theorem (or lemma): under a binary-symmetric noise model on judged rows, the gate's effective sample size scales with $\text{above\_chance}^2$, not with $\text{format} \times \text{accuracy}$. Above-chance is the right picker if you care about gate firing speed.
- Honest caveat: above-chance assumes label noise is symmetric. Asymmetric noise (judge biased toward "correct") complicates the picture; the unknown-rate is one signal that catches it.

### 4. Benchmark methodology

- Corpus: n=102 ticket-triage pairs, balanced 51/51 correct/incorrect, three labels (`bug`, `feature_request`, `question`) at 34 each.
- Why n=102: power-curve analysis showing 102 is enough to separate the 11 models tested at $\alpha = 0.05$ on the above-chance metric.
- Prompt: `_DEFAULT_JUDGE_PROMPT` from `src/postrule/verdicts.py`. Single-template across all 11 models — deliberately fair (no per-model tuning) but a known limitation.
- Stack: Apple M5 / 24GB / Ollama localhost; raw llamafile for Bonsai. Latency reflects single-host home/dev hardware, not optimized inference servers.
- Reproducibility: `python scripts/run_slm_verifier_bench.py` regenerates `slm-verifier-bench.json`.

### 5. Results (current — to extend with launch data)

Lift the table from `docs/benchmarks/slm-verifier-results.md`. Highlights:

- **qwen2.5:7b** — picked default. 85% accuracy on judged rows, 0.363 above-chance, 481ms p50. Sweet spot.
- **deepseek-r1:7b** — would win on accuracy (78%) but disqualified by 14s p50.
- **gemma2:2b** — picked classifier default. 71% accuracy, 242ms p50; small enough to run alongside the judge.
- **llama3.2:3b** — 91% format compliance but only 53% accuracy → above-chance 0.049. The exemplar of the "noisy parseable" failure mode that motivates the picker formula.

### 6. Latency feasibility

- Verifier runs on every sample-rated classification. At 1k req/day with `verifier_sample_rate=1.0`, a 14s p50 verifier consumes 14k seconds of compute / day = 4 cores at 100% utilization.
- The 1s p50 cutoff is therefore not arbitrary: it bounds the verifier-compute side of the operational ledger. Applications with a higher classification volume (10k+ req/day) need a tighter cutoff still.
- Discussion of when frontier APIs (GPT-4-class, Claude) are the right answer instead of local SLMs — privacy, regulatory boundary, latency budget, cost per call.

### 7. Production guidance

A decision tree, calibrated by domain attributes:

- **Low-cardinality, narrow domain** (ATIS-like) → small classifier (gemma2:2b) is enough; judge is qwen2.5:7b.
- **High-cardinality, broad domain** (CLINC150-like) → judge needs more accuracy; consider frontier API or qwen2.5:14b/32b on capable hardware.
- **Regulated / classified** → no external API; SLM-only with `verifier_sample_rate < 1.0` to bound cost.
- **Hot-path latency-bound** → reduce `verifier_sample_rate` and run verifier on a sampled subset; the McNemar gate is robust to lower sample rates as long as the sample is unbiased.
- **Privacy-bound** → bundled `postrule[bundled]` with llama-cpp-python; same model choice, no Ollama daemon.

### 8. Limitations

- Single-domain bench (ticket triage). The two-regime claim from the main paper suggests verifier choice may shift in the high-cardinality regime; we have not measured it.
- Single prompt template across all models. Per-model tuning would change the rankings; the bench is therefore a *zero-shot baseline*, not a "best each model can do."
- Single hardware profile. No CUDA / TPU / cloud-inference numbers. The latency cutoff is laptop-grade; production deployments would shift the frontier.
- Closed test set. Models trained on similar ticket data (qwen / gemma instruction-tuning corpora) may have seen related content; we cannot rule out modest contamination.
- No frontier-API coverage in the current bench. Adding GPT-4-class, Claude-class, Gemini changes the picture and is the highest-priority extension.

### 9. Conclusion + future work

- The above-chance picker is the deliverable. The bench is a *first instance* of applying it; community-extended benchmarks across domains are the path to generalization.
- The latency cutoff is the deliverable. Calendar-time accelerates as inference improves; the cutoff is the *unit*, not the value.
- Companion to the main paper: graduate the rule when the gate fires; pick the verifier that drives gate-fire fastest while staying within budget.

---

_Copyright (c) 2026 B-Tree Labs. Apache-2.0 licensed._
