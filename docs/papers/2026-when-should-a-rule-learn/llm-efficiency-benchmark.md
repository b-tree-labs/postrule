# The LLM Use Efficiency Benchmark — graduation economics

> Evidence for Part II + the "LLM Use Efficiency" positioning + the static
> audit priors. Harness: `scripts/benchmark_efficiency.py`. Honest by design —
> we measure where graduation wins **and** where it doesn't.

## What it measures (not a model-quality leaderboard — an *efficiency* one)

For each dataset, the three tiers a Postrule switch moves through:

| Tier | What it is | Cost/call |
|---|---|---|
| **rule** | day-zero heuristic (nearest cheap-feature centroid) | ~$0 |
| **ml-head** | the **local graduated** classifier (logreg on cheap features) | ~$3/M |
| **llm** | few-shot vision (the expensive *bootstrap* tier) | measured |

The question is **not** "is the LLM good at this?" It's: **can the switch graduate to the cheap local head without losing accuracy?** If yes → cost collapses ~97% while accuracy holds or improves.

## Results

| Dataset (task) | Modality | chance | rule | **ml-head** | llm (few-shot) | acc retained | cost decay |
|---|---|---|---|---|---|---|---|
| ESC-50 (animals/nature) | audio | 0.20 | 0.57 | **0.80** | 0.47 | **+0.33** | 97.7% |
| ESC-50 (mechanical/urban) | audio | 0.20 | 0.47 | **0.60** | 0.45 | **+0.15** | 97.7% |
| CIFAR-10 (animals/vehicles) | image | 0.20 | 0.40 | **0.46** | 0.81 | −0.35 | 93.7% |

(5-class subsets; ESC-50 = full fold-5 test, n=40/task; CIFAR n=100; haiku-4.5 few-shot, 1–2 exemplars/class; "acc retained" = ml-head − llm; measured token cost; total run ≈ \$0.02.)

## The honest finding — the cheap local head is the *right destination* for audio; the LLM is the right tool for native vision

1. **Audio: graduation wins outright on BOTH tasks.** Spectrogram-vision is genuinely **weak** — the LLM scores **0.45–0.47**, barely above the rule. The cheap local head (**0.60–0.80**) **beats it decisively** because spectral features capture acoustic structure. So for audio, graduating is **cheaper *and* more accurate (+15 to +33 points)** — the LLM is a poor bootstrap you should leave fast.
2. **Native images: the LLM is strong (0.81)** and a *naive* cheap head (16×16 logreg, 0.46) lags **35 points**. Here the **head is the lever** — invest in it (small CNN / frozen-embedding features) or stay on the LLM. The graduation *machinery* is fine; head quality decides.

**The unifying truth:** **cost decay is ~constant (~94–98%)**; **accuracy retention varies and is a *choice*** — you set the floor, and graduation captures the savings down to it. "AI that gets cheaper the more you use it" is true wherever cheap features carry the task (decisively so for audio) — and we're honest that for hard native vision you invest in the head or keep paying the LLM.

3. **An honest correction worth noting:** the n=25 pilot over-stated the audio LLM (0.60–0.64); the **full test fold gives 0.45–0.47** — which only *strengthens* the graduate-and-win result. Small-n LLM estimates are noisy; we report the full-set numbers.

4. **Cost is not the constraint.** ~\$0.0001/call → the whole benchmark runs for **~\$0.02**; \$100 buys ~750k+ calls. (HF audio beyond ESC-50 streams unreliably in this environment; the harness is dataset-agnostic and rows extend trivially where downloads cooperate.)

## Why this matters for the product / paper

- **Positioning:** "AI that gets cheaper the more you use it" is *true* where the cheap head suffices (audio, and any task with informative cheap features) — and we're honest about where it isn't yet (hard native vision). That candor is what makes it citable.
- **Audit priors:** the per-tier accuracies + cost-decay here are the **static priors** the efficiency audit applies to a customer's code — no per-customer LLM spend needed (see issue #186 / `positioning-llm-use-efficiency`).
- **Pitfall (Part II):** rendered-media vision (spectrograms) is weak zero-shot — which is *why* the cheap local head, not the LLM, is the destination for audio.

## Routing slice (toward the Postrule router) — an honest negative that shapes the design

Can a **cheap local classifier route LLM traffic** — easy queries → weak/cheap model, hard → strong/expensive — and capture the savings? banking77, weak=haiku-4.5, strong=sonnet-4.6, both models run for full information (n=300):

| Strategy | accuracy | cost | vs always-strong |
|---|---|---|---|
| always-weak (haiku) | 0.81 | $0.027 | 95% cheaper |
| always-strong (sonnet) | 0.84 | $0.553 | — |
| **ORACLE route** (weak when it's right, else strong) | **0.88** | $0.128 | **77% cheaper, ≥ quality** |
| **learned TF-IDF router** | 0.81 | — | 59% cheaper |

**The honest finding:** the **oracle frontier is excellent** (0.88 at 77% cheaper — *more* accurate than always-strong, because per-query routing picks the better model). **But a naive cheap text router can't reach it** — TF-IDF+logreg predicts "needs strong" at **49% (chance)**, so its realized routing is *no better than always-weak*. (An n=40 pilot showed a flattering 90%/85% — small-sample noise; the n=300 number is the truth.)

**Why this matters for the router design (the payoff of measuring before building):**
- The **value is real and large** (the oracle gap), so routing is worth building.
- The hard part is the **routing signal** — query text alone is insufficient. The design must use a *better* signal: the weak model's **uncertainty** (a cascade/deferral architecture: run weak, escalate on low confidence — needs true logprobs, which Anthropic doesn't expose but OpenAI/local models do), richer learned features (embeddings), or **outcome feedback learned off-policy**.
- That last point closes the loop: obtaining "would the other model have done better?" labels in production **is the selective-labels / off-policy problem (Part II)**. The router's core learning challenge *is* the paper's thesis — which is exactly the defensible, non-commodity edge over naive log-fitting routers.

## Router P0 — does a better signal beat the chance-level text router? (no)

Tested richer routing signals on the same banking77 setup (n=200): **sentence
embeddings (MiniLM) vs TF-IDF**, predicting "needs the strong model."

| Strategy | accuracy | note |
|---|---|---|
| always-weak (haiku) | 0.85 | the trivial baseline |
| always-strong (sonnet) | 0.88 | |
| ORACLE route | 0.90 | 81% cheaper, the ceiling |
| TF-IDF router | 0.82 | **dominated by always-weak** |
| embeddings router | 0.83 | **dominated by always-weak** |

**Finding:** both learned routers land *below* always-weak (0.85) — they can't
identify the ~15% of queries that need the strong model, so they default to
~always-weak and their escalations are near-random (which *hurts*). **Richer
text features do not rescue routing.** Two design consequences:
1. The signal is **not in the query text** → the router must use **weak-model
   uncertainty (cascade)** or **off-policy outcome learning** (the moat), not
   upfront text classification.
2. **banking77 is a weak testbed** — only a 3-pt weak↔strong gap, so little
   headroom exists to capture. Routing value requires a **large** gap; the next
   experiment needs a task where the weak model is genuinely worse.

## Router P0-B — the uncertainty cascade on a high-gap reasoning task

P0 ruled out upfront text routing; P0-B tests the architecture the evidence
pointed to — a **cascade**: run the cheap model, escalate to the strong model on
the cheap model's **uncertainty** (proxied by self-consistency across K=5
samples, since Anthropic hides logprobs). Task = **MMLU** (mixed hard subjects →
a real weak↔strong gap; the monetizable *reasoning* regime). n=120, weak=haiku,
strong=sonnet:

| Strategy | accuracy | note |
|---|---|---|
| always-weak | 0.57 | |
| always-strong | 0.69 | +0.12 gap (real headroom, unlike banking77) |
| ORACLE | 0.75 | 18-pt headroom over always-weak |
| cascade, escalate 12% (least consistent) | 0.58 | **only +1 pt** |
| cascade, escalate 100% | 0.69 | = always-strong, no savings |

**Finding (honest negative — and the most useful one):** **self-consistency is a
weak deferral signal on reasoning.** The cheap model is often *confidently wrong*
(unanimous across samples yet incorrect), so escalating on self-disagreement
catches few of its errors — escalating the 12% least-consistent queries lifts
accuracy ~1 point. (An n=8 pilot showed a flattering "25% → full strong
accuracy"; n=120 is the truth — small samples mislead, *report the powered
number*.)

**The synthesized router conclusion (across P0 + P0-B):**
- The routing **opportunity is large** on reasoning (18-pt oracle headroom).
- **Neither *cheap* signal captures it** — query text (dominated) nor
  self-consistency (marginal).
- → Routing value is **gated on the signal**: it needs *calibrated* uncertainty
  (true logprobs → a logprob-exposing provider) or a *learned off-policy*
  deferral model. **Cheap shortcuts fail — which is precisely why the learning
  loop is a moat, not a commodity.** Prior art to cite: FrugalGPT (LLM
  cascades), learning-to-defer (Madras et al.; Mozannar & Sontag).

## The local→cloud cascade — a real, new, monetizable capability (modest magnitude on academic MC)

The signal that finally **beats random**: a **free local model's logprob/margin
confidence**, escalating to the cloud only the low-confidence tail. MMLU, local
Ollama → cloud sonnet, n=200:

| Local model | local acc | cloud acc | gap | mean LIFT over random |
|---|---|---|---|---|
| qwen2.5-1.5b | 0.52 | 0.87 | +0.35 | **+0.030** (signal works) |
| qwen2.5-7b (margin) | 0.69 | 0.88 | +0.18 | **+0.020** (signal works) |

At qwen-7b τ=0.8, escalating the **10% least-confident** lifts 0.69→0.75 at
**90% cloud-cost saved** — a tunable accuracy/cost dial.

**Honest read:**
- **What's real + new:** a **confidence-gated local→cloud cascade** — run a
  capable model free on-prem, escalate only the uncertain tail; the confidence
  signal **reliably beats random** (twice). Current Postrule does rule→model→ML
  graduation, *not* local→cloud confidence escalation. This is a genuinely new,
  sellable capability.
- **What's NOT (yet):** a *dramatic* per-query recovery from a **raw threshold**.
  Capable models are **over-confident** on academic MC (confident even when
  wrong), so margin-thresholding catches a small slice; lift is incremental
  (+0.02–0.03), not spectacular. The N=12 "huge frontier" was the 1.5b model's
  outsized gap — noise-adjacent.
- **The path to the real moat:** replace the raw threshold with a **learned
  deferral model** (predict "cheap model will be wrong" from confidence + answer
  + features), trained on **real-traffic outcome logs** — i.e. fed by
  **monitor-only mode (#193)**. Academic MMLU is the *worst* case (over-confident,
  small mid-band); heterogeneous real traffic is where a learned policy wins.
  The flywheel — monitor → learn → escalate smarter → show the savings delta —
  is the compounding, defensible asset. Signal caveat: needs logprobs
  (Ollama/OpenAI expose; Anthropic doesn't).

## Reproduce

```
POSTRULE_ALLOW_PAID_LLM=1 PYTHONPATH=src python scripts/benchmark_efficiency.py esc50 40
POSTRULE_ALLOW_PAID_LLM=1 PYTHONPATH=src python scripts/benchmark_efficiency.py cifar10 100
POSTRULE_ALLOW_PAID_LLM=1 PYTHONPATH=src python scripts/benchmark_routing.py 300
```
