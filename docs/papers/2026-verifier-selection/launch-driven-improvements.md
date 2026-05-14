# Launch-driven improvements — what to harvest for paper #2

**Purpose.** A live list of things we expect the May 13 launch + the months
after to surface — feedback, data, model releases, adversarial probes —
that would materially improve the verifier-selection companion paper.

The companion paper ships post-launch (task #85). The launch period is
not "wait for paper #2" — it is the **data-collection window** for paper
#2. If we plan it now, we know what to listen for, what to ask early
adopters to share, and what to instrument before the floods arrives.

Each item below has the form:
- **What we expect** (the signal the launch will produce).
- **Why it improves the paper** (the section it would strengthen).
- **What to do during launch** (instrumentation / outreach / harvest).

---

## Tier 1 — almost certain; plan instrumentation now

### 1. Cross-domain benchmark contributions

**Expect.** Early adopters running Postrule on their own domains (banking
intent, security alerts, content moderation, clinical coding, support
triage variants). Some will publish or share verifier benchmarks for
their corpus.

**Improves.** §5 (Results) and §7 (Production guidance). The current
bench is single-domain (ticket triage); generalizing across 5–10 domains
makes the "qwen2.5:7b is the right default" claim defensible at scale.
The two-regime story from the main paper predicts verifier choice should
shift in high-cardinality domains — this is exactly where to test.

**During launch.**
- Ship `scripts/run_slm_verifier_bench.py` as a reproducible, well-documented
  CLI that anyone can point at their own corpus.
- Add a **community benchmarks** section to `docs/benchmarks/` and call
  for contributions in the launch posts.
- The Postrule `CandidateHarness` already records paired-correctness
  per checkpoint; verifier benchmarks fall out of `harness.report()`
  if we wire a `--verifier-only` mode. Worth a small PR pre-launch.

### 2. Frontier-API coverage

**Expect.** Reviewers will ask: "where's GPT-4-class? where's Claude?".
Some early adopters will run paid-API verifiers and share results.

**Improves.** §5 and §6 directly. Without frontier coverage the bench
is incomplete; with it, the cost-quality-latency frontier becomes a real
3D plot and the `default_verifier(prefer="auto")` ranking can be
empirically justified.

**During launch.**
- Run the bench against `claude-haiku-4-5`, `gpt-4o-mini`, `gemini-2.5-flash`
  ourselves before T-1 — this is a few hours and budget allows.
- Add the API-cost column to the bench (cost per million judgments at
  list price) so the frontier plot has a budget axis.

### 3. Real verifier-selection telemetry from users

**Expect.** When `default_verifier(prefer="auto")` is on by default,
operators will pick *something*. Which one they pick is a signal.

**Improves.** §7 (Production guidance) — currently the decision tree is
inferred from the bench; with telemetry we can show the empirical
distribution of user choices and discuss why operators deviate from
the recommendation.

**During launch.**
- Confirm telemetry posture: Postrule v1.0 ships **no phone-home**; we have
  no aggregate verifier-pick stats unless adopters volunteer them.
- Add a `postrule doctor --share-anonymous` opt-in that emits the
  verifier choice + an anonymized hardware fingerprint to a community
  endpoint. **Decision required: do we want this at all?** Memory says
  Ben is privacy-conscious; this might be a no.
- Alternative: ask the first 5–10 early adopters directly via DM.
  Smaller n, but cleaner consent.

### 4. New SLM releases between now and submission

**Expect.** The 2025–2026 release cadence is high: qwen3, llama4, gemma3,
phi-4, gpt-oss, Mistral-Nemo successors, deepseek-r2 — most likely several
of these land between May and the companion-paper submission window
(target Q3 2026).

**Improves.** §5 (Results) directly. A bench dated May with no qwen3 is
stale by July; a bench dated September with qwen3 + llama4 is the new
state of the art.

**During launch.**
- Pin the bench script's model list as `[explicit list]` so future re-runs
  are diff-able against the May 2026 baseline.
- Plan a re-run cadence: monthly through the submission window, then on
  every major-release as a sustaining update.
- The `slm-verifier-bench.json` is a living artifact; commit each re-run
  with a dated filename so the time-series is preserved.

### 5. Adversarial probes and prompt-engineering pushback

**Expect.** Someone in HN comments will post an input that fools
`gemma2:2b` into the wrong verdict. Someone else will demonstrate that
the default prompt template is brittle. A third person will claim a
better template and post benchmark numbers.

**Improves.** §3 (the picker formula) and §8 (Limitations). Adversarial
inputs surface the noise-model weakness; per-model prompt tuning
demonstrates the bench's "fairness-by-uniformity" tradeoff.

**During launch.**
- Pre-empt: in the launch posts, lead with "the bench uses a single
  prompt template, deliberately, to compare apples to apples; per-model
  tuning would change rankings."
- Catalog every adversarial probe surfaced in HN / X / GitHub issues.
  These become §8.x case studies in the companion paper.
- Run the catalog through the bench post-launch as an "adversarial
  hold-out" eval.

---

## Tier 2 — likely; less certain on timing

### 6. Calibration analysis

**Expect.** Reviewers familiar with G-Eval / MT-Bench will ask about
verifier calibration: when the judge says "correct", how often is it
correct? When it says "unknown", is it really unknown?

**Improves.** §3 (picker formula derivation under asymmetric noise) and
a new §3.x on the unknown-rate as a signal.

**During launch.**
- Re-instrument the bench to record the judge's *internal* confidence
  (where exposed — Anthropic doesn't expose, OpenAI logprobs do, Ollama
  via the response object).
- Plot a reliability diagram per model. Add to §5.

### 7. Multi-prompt sensitivity

**Expect.** Someone will run the bench across 3–5 prompt templates and
show that the model rankings shift.

**Improves.** §4 (methodology) — quantifies the prompt-tuning ceiling
and motivates a per-model "best prompt" comparison.

**During launch.**
- Author a small grid of prompt variants (zero-shot, few-shot, CoT,
  structured-output-mode, JSON-schema-constrained).
- Re-run the bench with each. Probably 4× the runtime; manageable.

### 8. Production-domain shift data

**Expect.** A small number of early adopters will run the same model
on their own corpus and find the rankings shift (e.g., qwen2.5:7b is
worse than gemma2:2b on a security-alert domain).

**Improves.** §7 (Production guidance) — converts the decision tree
from heuristic to evidence-based.

**During launch.**
- DM the first 5–10 production adopters at the 30-day mark; ask for
  per-domain benchmark data with anonymization permission.
- Aggregate as case studies in §7 (with attribution if they want it,
  anonymized otherwise).

### 9. Cost-per-million-judgments analysis

**Expect.** A practitioner will publish a cost analysis comparing
"running qwen2.5:7b on a $200/mo VPS" vs "calling Claude Haiku per
classification" at various traffic volumes.

**Improves.** §6 (Latency feasibility) becomes "Latency + cost
feasibility." The 3D frontier (accuracy / latency / $/M judgments) is
the real production decision space.

**During launch.**
- Don't wait for the community; we can author this directly.
- Build a small cost-calculator harness that takes
  `(model, throughput, host_cost)` and returns total $/M.

### 10. Comparison vs human-reviewer ground truth

**Expect.** A research adopter will run a parallel
`HumanReviewerSource` queue against an SLM verifier and report
agreement rates.

**Improves.** §3 noise-model section and §7 — gives an empirical anchor
for "how good *is* the SLM judge in absolute terms?", not just relative
to other SLMs.

**During launch.**
- Build the comparison harness ourselves on the n=102 corpus (we have
  the ground truth) and report agreement rate as a sanity check.
- Solicit larger-n parallel runs from early adopters.

---

## Tier 3 — possible; opportunistic

### 11. Constrained-decoding integration

**Expect.** Someone will demonstrate that Outlines / Guidance /
JSON-mode raises format compliance to ~100% across all models, changing
the picker-formula derivation (the format axis collapses).

**Improves.** §3 (picker formula) — handles the constrained case as a
limit; §4 (methodology) — adds a constrained-decoding column to the bench.

**Action during launch.** Watch for HN / Twitter posts; iterate on the
bench when someone surfaces a clean comparison.

### 12. Adversarial-judge / judge-bias literature

**Expect.** A subset of MT-Bench / G-Eval reviewers will push back on
the LLM-as-judge framing in general — citing length bias, position bias,
self-preference bias.

**Improves.** §2 (Related work) and §8 (Limitations). A more thorough
treatment of judge bias and how the McNemar gate's *paired* nature
contains some bias modes.

**Action during launch.** Track the citations they raise; integrate the
strongest into the §2 lit review.

### 13. Hardware diversity

**Expect.** Latency numbers from CUDA / Metal / TPU / cloud-inference
posted by adopters with different hardware.

**Improves.** §4 (methodology) and §6 (latency feasibility) — converts
"laptop p50" into a hardware-portable cutoff.

**Action during launch.** Catalog every hardware setup that gets
mentioned; ask the 2–3 most diverse for raw numbers.

### 14. Reflection on `default_verifier(prefer="auto")` heuristic

**Expect.** Someone will claim our auto-selection logic is wrong (it
prefers local Ollama → bundled → OpenAI → Anthropic, in that order).
They might argue privacy-first should *always* win, or cost-first.

**Improves.** §7 (Production guidance). Surfaces what users actually
want from the auto path; might motivate a v1.1 reordering.

**Action during launch.** Watch for it; document the design rationale
in `docs/api-reference.md` so the response is "here's why we picked
this order; what's your case for changing it?"

### 15. Explicit OOS-handling regime

**Expect.** CLINC150-flavored adopters will note that the bench doesn't
include an out-of-scope label. Verifiers fail differently on OOS than
on in-distribution wrong predictions.

**Improves.** §3 (picker formula) — `unknown` may legitimately be the
right answer, not a format failure. The current picker treats them
identically.

**Action during launch.** Author a small OOS-corpus extension and
re-run; report as a §5.x sub-result.

---

## Pre-launch decisions worth making now

These influence what we *can* harvest from launch — better to settle
before May 13 than to discover a missing affordance after.

1. **Telemetry posture.** Default off, opt-in on, or never? Affects
   Tier-1 item 3.
2. **Prompt template stability.** If we ship `_DEFAULT_JUDGE_PROMPT`
   v1.0 today, every benchmark for the next 6 months is on that
   template. Lock it deliberately or expect it to evolve?
3. **Bench corpus extension.** Is n=102 the right launch number, or
   should we expand to n=300 / n=500 before launch to be more
   defensible? Current bench took ~45 min per model on M5; n=500 would
   be 4 hours per model × 11 models = ~44 hours. Doable in a weekend if
   we want it.
4. **API-frontier inclusion.** Should we run the launch-day bench
   against Claude / GPT-4o / Gemini ourselves, or wait for community
   contribution?

Recommend addressing 1 and 2 explicitly before May 13; 3 and 4 are
nice-to-have.

---

## Capture mechanism

A simple Markdown file (`docs/papers/2026-verifier-selection/launch-feedback-log.md`,
not yet created) — to be opened on launch day, appended to as feedback
arrives, with each entry tagged by source (HN URL, X thread, GitHub issue,
private DM with consent).

Then on D+30 we triage the log into the companion-paper outline above
and start drafting from accumulated evidence rather than imagined future
input.

---

_Copyright (c) 2026 B-Tree Labs. Apache-2.0 licensed._
