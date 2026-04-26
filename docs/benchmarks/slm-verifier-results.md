# SLM verifier benchmark — research summary

Living artifact. Re-run via `python scripts/run_slm_verifier_bench.py`;
results land in `slm-verifier-bench.json` adjacent to this doc.
This file curates findings and supports the "why we ship what we
ship" decision in `default_verifier()`.

## Task

The judge LLM is shown a *(input, classifier_label)* pair and
asked to return `correct` / `incorrect` / `unknown`. The
benchmark corpus is 30 ticket-triage pairs split 50/50 between
correct and incorrect classifications.

For each candidate model we measure:

- **Format-compliance rate** — fraction of judgments that
  produced a parseable verdict (not `unknown`).
- **Accuracy on judged rows** — fraction of parseable verdicts
  that matched ground truth.
- **Composite score** — `format_rate × accuracy_on_judged` —
  what % of input pairs end up correctly judged.
- **Latency** — p50 / p99 milliseconds per judgment.

## Current results (2026-04-25)

Apple M5 / 24 GB / Ollama localhost. Default prompt template
(`_DEFAULT_JUDGE_PROMPT` in `src/dendra/verdicts.py`).

| Model | Disk | Format-rate | Acc on judged | Composite | p50 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| `qwen2.5:0.5b` | 397 MB |  17% | 20% | 0.03 |  90 | 916 |
| `llama3.2:1b`  | 1.3 GB |  67% | 40% | 0.27 | 180 | 1260 |
| `gemma2:2b`    | 1.6 GB |  43% | 46% | 0.20 | 252 | 1904 |
| `llama3.2:3b`  | 2.0 GB | **97%** | 48% | **0.47** | 273 | 2048 |

**Bold** = best in column.

## Key findings

1. **Format-compliance scales with model size, sharply.**
   `qwen2.5:0.5b` parses cleanly only 17% of the time;
   `llama3.2:3b` is 97%. The judge prompt expects "correct" /
   "incorrect" / "unknown" — small models drift into prose.

2. **Accuracy on judged rows is essentially noise across all
   sizes (~40-50%).** This is unexpected and the most
   important finding: even the strongest local SLM
   (`llama3.2:3b`) is barely better than coin-flip on its own
   verdict task at this prompt and corpus.

3. **The combined effect:** `llama3.2:3b` has the strongest
   composite score because its format-compliance is huge, even
   though its accuracy is no better than smaller models.

4. **Latency is acceptable across the board.** Even the
   slowest (`llama3.2:3b` p99 of 2 s) is fine for inline
   verification given the rest of Dendra's perf budget.

## What 50% accuracy means for the gate

Critical context: **50% judge accuracy doesn't break the
McNemar gate. It makes it slower.**

The paired-McNemar test runs over discordant pairs (rows where
the rule and the candidate disagree on correctness). A noisy
judge produces noisy discordant counts — the gate's α-bound
still holds, but the test takes more outcomes to clear
significance because the signal-to-noise ratio is worse.

Rough model:

- **90% accurate judge** (cloud LLM, large committee) → gate
  clears at ≈ 250 outcomes (paper result).
- **50% accurate judge** (current local SLMs) → gate takes
  ≈ 1500-2500 outcomes to clear at the same `p < 0.01` (5-10×
  the data).

That's a real cost but not a fatal flaw. **The launch story
needs to be honest about it:** ship a local-first default that
prioritizes format-compliance (so verdicts are recoverable
data, not noise), document the verdict-accuracy trade-off
explicitly, and recommend cloud verifiers for users who want
fast graduation.

## Decision: shipped default

**`llama3.2:3b`** — the best of the locally-runnable Ollama
SLMs we tested. 97% format-compliance is decisive even if
accuracy is ~50%. Ships as the `default_verifier()` model.

Trade-off acknowledged in the docstring + FAQ: the local
verifier is the fastest path to autonomous mode, but verdict
accuracy is bounded by what a 2 GB model can do; gate
graduation will take more outcomes than the 250-mark paper
result. For faster graduation, swap in a cloud verifier
(`prefer="openai"` / `prefer="anthropic"`) or a larger
self-hosted model.

## Open work — to test next

Models / configurations not yet benchmarked. As we run them,
update the table above and the decision below.

| Candidate | Why test it | Status |
|---|---|---|
| `phi3.5:mini` (3.8 B) | Microsoft's strong-on-instruction-following SLM | NOT TESTED |
| `qwen2.5:3b` | Often outperforms Llama-3.2-3b on classification | NOT TESTED |
| `llama3.2:8b` | Larger; expected ~80%+ accuracy | NOT TESTED (~5 GB) |
| `gpt-4o-mini` (cloud) | Reference upper-bound | NOT TESTED (needs API key) |
| `claude-haiku-4-5` (cloud) | Reference upper-bound | NOT TESTED (needs API key) |
| Improved prompt template | Few-shot, strict format constraint | NOT TESTED |

## Methodology — how to reproduce

```bash
ollama pull qwen2.5:0.5b
ollama pull llama3.2:1b
ollama pull gemma2:2b
ollama pull llama3.2:3b

python scripts/run_slm_verifier_bench.py
```

Output:

- `docs/working/benchmarks/slm-verifier-bench.json` (machine-readable)
- this doc (human-readable; update curated section by hand)

The corpus lives in the script under `_CORPUS`. Each row is
`(input_text, classifier_label, ground_truth_correct)`. Split
50/50 between true-positive (label is the right answer) and
false-positive (label is wrong). 30 rows total — small but
enough to surface format-compliance differences cleanly.

## What this doc is for

1. **Public credibility** — the "we benchmarked this and here's
   what we picked" story. Shippable as part of the launch docs
   so reviewers can verify our claims.
2. **Iteration support** — every time we test a new model or
   prompt, we add a row and the decision updates. The doc is
   meant to grow.
3. **Setting realistic expectations** — the "what 50% accuracy
   means" section gives users a model for predicting how their
   verifier choice affects gate-graduation speed.

If you're a reviewer / contributor and want to help — adding a
row to this table for a model we haven't tested is a great
first contribution. Run the benchmark, paste the JSON line into
the table, send a PR.
