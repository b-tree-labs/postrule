# SLM verifier benchmark — research summary

Living artifact. Re-run via `python scripts/run_slm_verifier_bench.py`;
results land in `slm-verifier-bench.json` adjacent to this doc.
This file curates findings and supports the "why we ship what we
ship" decision in `default_verifier()`.

## Task

The judge LLM is shown a *(input, classifier_label)* pair and
asked to return `correct` / `incorrect` / `unknown`. The
benchmark corpus is 102 ticket-triage pairs split 51/51
between correct and incorrect classifications, balanced
across the three labels (`bug`, `feature_request`, `question`)
with 34 prompts each.

For each candidate model we measure:

- **Format-compliance rate** — fraction of judgments that
  produced a parseable verdict (not `unknown`).
- **Accuracy on judged rows** — fraction of parseable verdicts
  that matched ground truth.
- **Above-chance score** — `format_rate × max(0, 2·acc − 1)` —
  the **picker formula we ship on**. Maps a chance-level
  judge (50% acc) to zero contribution, so a high-format /
  noise-accuracy model is correctly recognised as useless.
- **Multiplicative score** — `format_rate × accuracy_on_judged` —
  retained for transparency. Treats high-format-low-accuracy
  as equivalent to low-format-high-accuracy, which is
  measurably wrong (see "Why above-chance, not multiplicative"
  below).
- **Latency** — p50 / p99 milliseconds per judgment.

## Current results — n=102 corpus, 2026-04-25

Apple M5 / 24 GB / Ollama localhost (port 11434) for Ollama
models; raw llamafile (port 8080) for Bonsai. Default prompt
template (`_DEFAULT_JUDGE_PROMPT` in `src/postrule/verdicts.py`).

Sorted by **above-chance** (the picker), latency-feasibility
flagged in the rightmost column.

| Model | Disk | Format | Acc | **Above-ch** | Mult | p50 ms | Feasible¹ |
|---|---:|---:|---:|---:|---:|---:|:---:|
| `deepseek-r1:7b`   | 4.7 GB |  76% | **78%** | **0.421** | 0.588 | 14,312 | ✗ |
| **`qwen2.5:7b`**   | 4.7 GB |  52% |  85% | **0.363** | 0.441 |    481 | ✓ |
| `deepseek-r1:1.5b` | 1.1 GB |  49% |  76% | 0.255 | 0.373 |  3,763 | ✗ |
| **`gemma2:2b`**    | 1.6 GB |  48% |  71% | 0.206 | 0.343 |    242 | ✓ |
| `qwen2.5:3b`       | 1.9 GB |  45% |  63% | 0.118 | 0.284 |    248 | ✓ |
| `llama3.2:1b`      | 1.3 GB |  69% |  54% | 0.059 | 0.373 |    165 | ✓ |
| `llama3.2:3b`      | 2.0 GB |  91% |  53% | 0.049 | 0.480 |    261 | ✓ |
| `qwen2.5:1.5b`     | 1.0 GB |  80% |  52% | 0.039 | 0.422 |    153 | ✓ |
| `phi3.5:3.8b`      | 2.2 GB | **94%** |  51% | 0.020 | 0.480 |    275 | ✓ |
| `qwen2.5:0.5b`     | 397 MB |  17% |  53% | 0.010 | 0.088 |     91 | ✓ |
| `bonsai-1.7b.gguf` | 1.7 GB |  27% |  44% | 0.000 | 0.118 |  3,278 | ✗ |

¹ Feasibility cutoff: p50 < 1 s. Verifier runs on every
classification; latency above 1 s makes the verifier
impractical for production-volume traffic.

**Bold rows** are the v1.0 shipped defaults. **Bold columns**
are the load-bearing metric we picked on.

## Why above-chance, not multiplicative

The multiplicative composite (`format × acc`) treats a 95%-
format / 50%-accuracy judge as equivalent to a 50%-format /
95%-accuracy judge — both score 0.475. **This is measurably
wrong.** The first floods Postrule's gate with confident coin
flips; the second says "I don't know" half the time but the
verdicts it does give are reliable.

The above-chance formula formalises the asymmetry:

```
score = format_rate × max(0, 2 · accuracy − 1)
```

A 50% accurate judge contributes literally zero — every
verdict is a coin flip and adds no information beyond noise.
A 75% judge contributes 0.5 of its format rate. A 100% judge
contributes its full format rate.

In our table the divergence is concrete: multiplicative ranks
`llama3.2:3b` first among latency-feasible models (0.480);
above-chance ranks `qwen2.5:7b` first (0.363). The two
formulas pick **different shipped defaults** on the same data.
We picked above-chance because format-compliance buys
graduation speed; accuracy buys graduation correctness; and
correctness is non-substitutable.

## Decisions: shipped defaults

### Judge / `verifier=` → `qwen2.5:7b`

Best signal among latency-feasible candidates. 85% accuracy
on judged rows means verdicts entering Postrule's gate are real
information, not coin flips. 481 ms p50 is comfortably under
the 1 s feasibility line. 4.7 GB on disk is the cost.

The R1 family produced higher signal (R1:7b at 0.421 above-
chance vs qwen's 0.363) but its 14 s p50 makes it unusable as
a verifier — at 1k req/day, R1:7b would run a full 24 hours
of GPU/CPU time per day on judging alone. Reasoning models
are great for accuracy; their latency cost is fundamentally
incompatible with the verifier role.

### Classifier / `model=` → `gemma2:2b`

71% accuracy on judged is the second-best signal we measured,
at 1.6 GB and 245 ms p50. Different model family from
`qwen2.5:7b`, so the same-LLM guardrail
(`require_distinct_from=`) is satisfied without configuration.

The `model=` benchmark (predicting labels given inputs, not
judging label-input pairs) is a separate task we have NOT
benchmarked yet. The choice of `gemma2:2b` is grounded in
verdict-task generalisation: a model that scores 71% on
judging is signalling real understanding of the label
semantics, which generalises (heuristically) to better
classification. A proper `model=` benchmark is post-launch
work.

### Total bundled-cache footprint: 6.3 GB

Lazy-downloaded from R2 on first use of `default_verifier()`
and `default_classifier()` respectively. Users who want only
one of the two can opt out per-call.

## Why not Bonsai-raw?

Bonsai-1.7B served raw via llamafile scored 26% format / 44%
accuracy / 3.3 s latency at n=102. The 44% is below chance:
Bonsai's verdicts on this task are *anti-correlated* with
truth. Above-chance score: 0.000.

This isn't necessarily a condemnation of Bonsai-in-Axiom —
Axiom's prompt pipeline + RAG layer might be load-bearing for
Bonsai's usability on classification tasks (system prompts,
retrieval context, instruction templates). But Bonsai-raw on
the narrow verdict task does not pull its weight, and we
exclude it from the shipped defaults until a Bonsai-in-Axiom
benchmark proves the wrapped version performs differently.

## What 50% accuracy means for the gate (kept verbatim from prior version)

Critical context for users running models near chance:
**50% judge accuracy doesn't break the McNemar gate. It makes
it slower.** The paired-test α-bound still holds, but it
takes more outcomes to clear significance because the
signal-to-noise ratio is worse.

Rough model:

- **90% accurate judge** (cloud LLM, large committee) → gate
  clears at ≈ 250 outcomes (paper §6 result).
- **50% accurate judge** (current local SLMs at chance) →
  gate takes ≈ 1500–2500 outcomes to clear at the same
  `p < 0.01` (5–10× the data).

For users who can't bring `qwen2.5:7b`-grade quality, this is
a real cost but not a fatal flaw. Document, ship, let users
opt up.

## Variance commentary — n=30 vs n=102

The earlier n=30 run picked `llama3.2:3b` (multiplicative
composite 0.533, "100% format / 53% acc") as the shipped
default. At n=102 the format dropped to 91% and accuracy
held at 53% — which **invalidates that pick under the
above-chance lens**: 53% accuracy is barely-above-chance and
the gate would graduate slowly on noisy verdicts.

Variance in n=30 individual cells was up to 15 pp on
accuracy-on-judged and 10 pp on format-compliance. n=102
tightened this to roughly 5 pp on both. **The corpus
expansion was load-bearing for the default-pick**; n=30
would have shipped the wrong model.

Future re-runs (new model releases, prompt-template changes,
hardware changes) should use n=102+ as the floor.

## Open work — to test next

Models / configurations not yet benchmarked. As we run them,
update the table above and the decisions below.

| Candidate | Why test it | Status |
|---|---|---|
| `bonsai-via-axiom-pipeline` (port 8766) | Verify whether Axiom's pipeline+RAG lifts Bonsai above chance | Pending Axiom HTTP server up |
| `gpt-4o-mini` (cloud) | Reference upper-bound; expected near-perfect | NOT TESTED (needs API key) |
| `claude-haiku-4-5` (cloud) | Reference upper-bound; expected near-perfect | NOT TESTED (needs API key) |
| `phi4` (when released) | Microsoft's next instruction-tuned SLM | Not yet released |
| `qwen3` (when released) | Successor to qwen2.5 family | Not yet released |
| Improved prompt template | Few-shot, strict format constraint, verdict-only output | NOT TESTED — likely lifts the chance-cluster models meaningfully |
| Classifier-task benchmark | Different task: predict label given input. Distinct from verdict task. | Post-v1.0 deliverable; selects `model=` default with evidence |

## Methodology — how to reproduce

```bash
# Install the Ollama models
ollama pull qwen2.5:0.5b qwen2.5:1.5b qwen2.5:3b qwen2.5:7b
ollama pull llama3.2:1b llama3.2:3b
ollama pull gemma2:2b
ollama pull deepseek-r1:1.5b deepseek-r1:7b
ollama pull phi3.5:3.8b

# Optional: run a llamafile-served GGUF on port 8080 for the
# Bonsai/llamafile row (or any other GGUF you want to bench).

python -u scripts/run_slm_verifier_bench.py
```

Output:

- `docs/benchmarks/slm-verifier-bench.json` (machine-readable)
- this doc (human-readable; update curated section by hand)

The corpus lives in the script under `_CORPUS`. Each row is
`(input_text, classifier_label, ground_truth_correct)`. 102
rows, balanced 51/51 across correct/incorrect and 34/34/34
across labels (`bug`, `feature_request`, `question`).

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
