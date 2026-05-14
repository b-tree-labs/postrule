# Postrule FAQ

Answers to the questions people ask first. Updated 2026-05-11.

## What is Postrule, in one sentence?

The graduated-autonomy primitive for production classification:
rules earn their way to ML through statistically-gated phase
transitions, with the rule preserved as the safety floor.

## How does Postrule know when a switch is ready to graduate?

Every gate evaluation is a paired-McNemar test on accumulated
correctness data. When the ML head's correct-vs-rule margin
clears α (default 0.01) on at least 30 paired samples, the gate
fires and the switch advances a phase. The full statistical
framework — including the regime taxonomy, the
sequential-testing posture, and the eight-benchmark validation —
is in the [companion paper](papers/2026-when-should-a-rule-learn/paper-draft.md)
and the [Test-Driven Product Development methodology
reference](methodology/test-driven-product-development.md).
The short version: every graduation is a pre-registered, paired,
statistically-defensible decision. Not vibes; not a hand-coded
threshold. The gate fires *because evidence justified it*.

## What does the report card show me?

When a wrapped switch graduates (or hits a drift event), Postrule
writes a markdown report card at `postrule/results/<switch>.md`. It
captures everything the gate saw and decided:

- **Phase + graduation timestamp** — which lifecycle phase the
  switch is in, when it last advanced, after how many outcomes
- **Gate evidence** — the configured gate (default `McNemarGate`),
  the α it cleared, the p-value at fire, the effect size in
  percentage points
- **Transition curve** — rule accuracy vs ML accuracy over outcomes,
  rendered as a PNG. The crossover point + the gate-fire point are
  both labelled.
- **p-value trajectory** — gate p-value over outcomes (log scale).
  The dashed α line + the fire-point are labelled. A monotone-
  strict-decreasing trajectory after the fire is the signal we look
  for to confirm the graduation isn't a sampling fluke.
- **Phase timeline** — Mermaid Gantt chart showing the lifecycle
  history (RULE → MODEL_SHADOW → ... → ML_PRIMARY) with timestamps
- **Cost trajectory** — per-call cost over time, with a table
  showing pre/post-graduation reduction in $ and latency
- **What-if** — re-run the cost numbers under a different LLM with
  `postrule report <switch> --model claude-haiku-4.5` etc.
- **Drift posture** — whether the drift detector is currently green,
  what the last check measured, and what would trigger a demotion

Three commands produce the evidence trilogy:

| Command | Card |
|---|---|
| `postrule analyze --report` | initial-analysis discovery card — which sites are candidates for graduation |
| `postrule report <switch>` | per-switch graduation card — what the gate saw and when it fired |
| `postrule report --summary` | project rollup — cockpit view across every wrapped switch |

Sample cards are committed in [`docs/sample-reports/`](sample-reports/)
so reviewers can see the full evidence shape before installing.

## When does the report card update?

After every gate evaluation. Default config evaluates on every 50th
outcome, so on a switch seeing 1,000 verdicts/day the card updates
~20 times per day. The card is always current with the most recent
audit-chain state — re-run `postrule report <switch>` any time, or
let CI re-render it on a schedule (the `aggregator.yml` workflow
template does this nightly).

If the drift detector trips, the card re-renders immediately with
the drift event highlighted at the top and a `**Action required**`
callout. That's the version that should land in the on-call
notification.

## Do I actually need this? Can't I just use if/else?

For some classifiers, yes — and we'll say so directly.

If your classifier has 3-5 stable cases, no meaningful drift,
no audit requirement, and lives in a non-critical path, **you
probably don't need Postrule**. A plain if/else block is the
right tool. We'd rather you ship that.

Postrule is for classifiers where one of these is true:

- **Outcome data is accumulating and you're not using it.**
  Every day your rule misclassifies and the data sits
  unanalyzed is optionality you're throwing away.
- **There's a "we should ML this" backlog ticket that doesn't
  move.** That's exactly the migration we're a primitive for.
- **The decision has audit / compliance implications.** HIPAA,
  export-control, regulated industries need an auditable chain
  on every classification.
- **Wrong classifications cost real money or trust.** Production-
  grade classifiers warrant the safety floor + circuit breaker
  even when the average case is fine.
- **You're running an autoresearch / agent loop.** The
  `CandidateHarness` is the missing deployment substrate.

If none of the above match your classifier, keep your if/else.
We mean it. Postrule is opinionated about being a primitive for
production-grade classification — not a general-purpose
dispatcher.

## What can't I Dendrify?

Some shapes of code look like classification but aren't, or have constraints that block lifting cleanly. The concrete list:

- **pytest tests, fixtures, validators, setup code.** No labels, no production routing decision. The analyzer filters these out by default.
- **Async generators** (`async def f(): yield ...`). The classifier protocol expects a label return, not a stream. Async coroutines that return a label work via the async API peers (`aclassify`, `adispatch`).
- **Classes used as callables** (`MyClass(input)` where `__call__` runs classification). Wrap the `__call__` body in a `@ml_switch` function, or migrate to the native `postrule.Switch` class authoring pattern (v1).
- **Multi-positional + `**kwargs` without clean signatures.** Multi-arg auto-packing (v1) requires `inspect.signature(...)` to recover parameter names and types. Functions assembled dynamically (`functools.partial` chains, `*args` only with no type hints) need an explicit signature before they can lift.
- **Decisions that need hidden out-of-process state we can't see.** If the rule consults a remote service or database state, that state has to be exposed as evidence (auto-lift, or `@evidence_inputs`). If the state can't be exposed, the LLM/ML head can never see what the rule saw, and Postrule refuses with a specific diagnostic.

The full list, with version tags and the path forward for each item, is in [`limitations.md`](./limitations.md).

## Does it work with LangChain agents (and the other broker frameworks)?

Yes. The classification sites that Postrule wraps live inside the
framework code, not your code. We've already run the v1 analyzer
against the eight largest LLM-broker libraries (LangChain,
LlamaIndex, Haystack, AutoGen, CrewAI, DSPy, LiteLLM, Instructor)
and surfaced 919 classification sites across 10,889 Python files.
Most of the high-fit sites sit on class methods, which the v1.5
lifters reach.

You don't replace the framework. You point Postrule at your
project's import surface or at the framework you depend on; the
wrapping is opt-in and per-site. To see the breakdown for any of
these libraries on your machine, clone the repo and run
`postrule analyze .` against it.

## Will `postrule init --auto-lift` break my agent graph?

No, by construction. `--auto-lift` writes opt-in lifters that
live alongside the original function and apply via decorator.
The original control flow still runs underneath; the gate
simply routes the call once a candidate has earned it on real
traffic.

If a candidate site looks unsafe to lift (hidden state, side
effects inside a branch, non-pure rule), the analyzer refuses
with a specific diagnostic instead of silently lifting. The
drift detector (`postrule refresh --check`) tells you if the
underlying function changed since the lift was written, and
`postrule doctor` reports any site whose AST hash no longer
matches.

The first thing to do after `--auto-lift` runs is your existing
test suite. The lifters preserve return shape and exception
behavior; if anything regresses, the diff is small enough to
read in one sitting.

## Why not just use shadow mode / A-B testing / a feature flag?

Shadow mode says "run both, log both." Postrule says "run both,
log both, **and tell me when the evidence is strong enough to
switch**." The statistical transition gate is the load-bearing
piece — without it, teams either switch too early (regression
risk) or never switch at all (the "we'll migrate to ML one day"
tech-debt backlog every production team has). See the paper's
§3.3 for the theorem bounding the probability of
worse-than-rule behavior by the test's α.

## Why is there a *rule*? Isn't the language model supposed to replace the rule?

The rule is the **safety floor**. In the highest-autonomy phase
(ML_PRIMARY) it's still there, watched by a circuit breaker that
reverts to the rule on ML failure or anomaly. For authorization-
class decisions, setting `safety_critical=True` makes
construction in ML_PRIMARY throw at construction time — the
rule-grounded floor can't be removed without a code change.

This matters because silent ML failure is a real failure mode
(a classifier that keeps answering but has silently gone wrong
from drift, a data-pipeline outage, or adversarial input). The
rule is cheap; having it means you always have somewhere to
fall back to that you can reason about.

## What's the difference between `ML_WITH_FALLBACK` and `ML_PRIMARY`?

One thing changes: **the confidence threshold is removed.**

| | `ML_WITH_FALLBACK` (P4) | `ML_PRIMARY` (P5) |
|---|---|---|
| Take ML head's prediction | only if `conf_H ≥ θ` | always |
| On low confidence | cascade through language model, then rule | (this branch doesn't exist) |
| Hard ML failure (exception, NaN, timeout) | fall to rule | trip circuit breaker, fall to rule |
| Latency | mostly microseconds; uncertain rows can incur an LLM call | always microseconds |
| Cost | per-row token cost on uncertain rows only | zero per-call token cost |

Two distinct McNemar gates earn the two transitions:

- **P3 → P4** asks: "is the ML head reliably better than the language model on the rows where the ML head is confident enough to commit?"
- **P4 → P5** asks: "is the ML head reliably better than the language model on *every* row, including the long tail where the ML head reported low confidence?"

Crossing the first does not entail crossing the second. A model can be excellent on its high-confidence majority and embarrassing on its low-confidence tail (Guo et al., 2017). The lifecycle separates these two trust statements so an operator can ship at P4 with the cascade catching uncertainty, and only commit to P5 once the long-tail evidence is in. P4 is the regulatory ceiling (`safety_critical=True` caps here). P5 is the latency / cost ceiling.

## When the ML head is uncertain, why does it fall back through the language model first instead of straight to the rule?

Because you already proved the language model beats the rule. Throwing that evidence away is wasteful.

The lifecycle's load-bearing rule is **predecessor-cascade fallback**: each phase's low-confidence path is its predecessor's full routing, recursively.

| Phase | Routing |
|---|---|
| P0 (RULE) | `R(x)` |
| P2 (MODEL_PRIMARY) | `M(x) if conf_M ≥ θ else R(x)` |
| P4 (ML_WITH_FALLBACK) | `H(x) if conf_H ≥ θ else (M(x) if conf_M ≥ θ else R(x))` |
| P5 (ML_PRIMARY) | `H(x)` |

Each promotion adds a tier *on top of* the existing cascade. P2 added M above R. P4 added H above M-then-R. The fallback from any phase walks down through every tier you earned, in the order you earned them.

If you retire the language model (drop the `model=` slot on the switch), the cascade collapses gracefully: low-confidence H falls straight to R, identical to the v1.0 pre-cascade behavior. No surprises for installs that drop M to save cost.

## Why isn't there a phase where the language model decides without a confidence threshold?

Because the language model is a borrowed brain.

The language model `M` (Phase 2's primary) is a generic LLM. You did not train it on your verdict log. You cannot improve its calibration by feeding it more outcomes. Its low-confidence outputs on long-tail inputs are inherently more dangerous than just "statistically uncertain" — they're hallucination / jailbreak territory. The confidence threshold on M is a permanent guardrail, not a transition state.

The trained ML head `H` is yours. H trains on the verdict log Postrule collects. As outcomes accumulate, H's calibration improves *specifically on your distribution*. The McNemar gate at P4 → P5 fires when H is reliably better than M even on the rows where H itself reported low confidence. That gate has actual evidentiary content because verdict data tightens H's calibration. There's no analogous gate for M because no analogous evidence exists — verdicts don't change M.

The deeper rule: **you can only remove a confidence threshold for a tier you own and can train.** The lifecycle never trusts a tier with no learning loop end-to-end.

If you fine-tune a per-task language model on your verdict log, *that* object is structurally an `H`-shaped object (yours, trainable on outcomes), not an `M`-shaped object, and the lifecycle will treat it accordingly.

## How can the ML head exceed the language model, when its labels came from the language model?

This is the classic distillation question every reviewer asks. The answer depends on which truth-oracle posture you're in.

**Case 1 — real-world feedback as truth.** A human reviewer, a downstream success signal ("did the user click", "did the ticket get reopened"), a business outcome — these record verdicts independently of the language model. H trains on `(input, true_label)` pairs; the language model was just keeping production running while ground-truth data accumulated. H exceeding M is unsurprising — H learns the true function, M was the stand-in.

**Case 2 — the `JudgeCommittee` is the oracle.** When human labels aren't available, Postrule supports LLM-as-judge: a `JudgeCommittee` (multiple model calls, voted) records the verdicts. H's training labels come from the model itself. This is the case the question implicitly asks about. Five mechanisms make it work:

1. **Smoothing over teacher noise.** M's per-call output is stochastic (sampling temperature, prompt-position effects, reasoning-chain variance). H trained over many calls learns the *modal* behavior and discards per-call noise. M's mistakes are themselves noisy and partially average out.

2. **Inductive bias as regularization.** H is a small classifier (sklearn pipeline, gradient-boosted trees, small transformer). For tasks where the true function is simpler than M's free-form reasoning — most production classification: intent, sentiment, routing, tagging — H converges to a cleaner approximation. M is *too expressive* for the actual task.

3. **Dark-knowledge distillation.** Even with hard labels only, the aggregate distribution of M's calls carries soft information: it gets borderline cases right 60% / wrong 40%, but consistently. H learns from the *consistency*, not the per-call answer. If you preserve judge confidences from the `JudgeCommittee`, distillation matches or beats teacher accuracy on held-out data — the DistilBERT-and-descendants result.

4. **The gate's criterion is equivalence, not dominance.** Paired McNemar at ML_SHADOW → ML_WITH_FALLBACK asks "do H and M agree on the population, with statistical confidence?" — not "is H strictly better?". On a task where M's labels are consistent, the gate fires because H matches. On a task where M's labels are inconsistent, H never graduates. The framework only promotes H when the equivalence is real.

5. **Production wants the dominant answer.** Query M on the same input 100 times with slight prompt variations — you'll get 100 different answers. H emits the dominant one deterministically. For consistency and user trust, that's usually what production actually wants, even when individual M calls occasionally outperform.

**The clean framing for Case 2:**

> ML_PRIMARY isn't "we got better than the language model in some absolute sense." It's "we identified the language model's *consistent* function on this task and replaced the language model with a 1000×-cheaper, 1000×-faster implementation of that function."

The inevitable follow-up — *"isn't this just distillation?"* — yes, plus a statistical gate that decides *when* the distillation is good enough to take the teacher offline. The novelty isn't the distillation; it's the gate plus the lifecycle that lets a switch go from "no labels yet" to "production ML running unsupervised" through six phases, each with their own stop conditions.

## What is H, physically? What's actually stored and executing?

In the v1 reference (`SklearnTextHead`), H is a scikit-learn `Pipeline` held in memory with two stages:

1. **A `TfidfVectorizer`** — a learned vocabulary (token → integer ID) plus IDF (inverse document frequency) weights per token. Built from the rows in your verdict log where the outcome was correct. The vectorizer turns input text into a sparse feature vector by counting token occurrences and scaling each by its IDF (rare tokens count more than common ones).

2. **A `LogisticRegression`** — a weight matrix of shape `(n_classes, n_vocab)`, one weight vector per output label, learned by L2-regularized maximum-likelihood. At inference, it produces a softmax probability over labels; the argmax is the prediction and the max probability is the confidence.

Concretely:

```
input  →  serialize_input_for_features()    # dict → "title: foo | body: bar"
       →  vectorizer.transform()            # text → sparse TF-IDF vector
       →  lr.predict_proba()                # vector → softmax over labels
       →  argmax + max                      # → (label, confidence)
```

Memory footprint scales with `n_classes × n_vocab` floats: ~1 MB for ATIS (26 labels), ~18 MB for CLINC150 (151 labels). Inference latency on commodity hardware: microseconds.

What's persistent vs ephemeral:

| | Persistent? |
|---|---|
| Verdict log (records, outcomes) | yes — in-memory rotator / file / sqlite / pluggable |
| Lifecycle phase + circuit-breaker state | yes — audit chain |
| The trained `Pipeline` itself | **no** in v1 — re-fit from the log on demand |

The verdict log is the source of truth; H is a function of the log. On process restart, H is recomputed from the log. Cost: one fit pass at startup. Benefit: the model can never drift from the data because it *is* the data.

The `MLHead` protocol is pluggable (`fit / predict / model_version`). When you outgrow TF-IDF + LR, plug in transformers, ONNX-exported models, XGBoost, anything that satisfies the protocol. Lifecycle, gates, audit chain, and cascade are unchanged.

## Can the lifecycle go backward?

Yes. The same paired-McNemar machinery that promotes a tier can also demote it.

When `auto_demote=True` (the v1 default), every $N$ verdicts the switch evaluates the *reverse-direction* gate: "is the rule reliably better than the current decision-maker on the recent paired-correctness evidence?" If that gate fires, the lifecycle steps back **one phase**. Multi-step retreats accumulate across successive cycles if drift persists.

This is how Postrule handles concept drift on the accuracy axis: not as a separate detector with its own thresholds, but as the same gate primitive called with the rule as the comparison target. Type-I error is bounded by the same α as the advancement direction.

The rule R is always reachable, structurally, from any phase. There is no point in the lifecycle where the operator cannot return to "your code, deterministic" by demoting back to P0.

Operators can also demote manually via `switch.demote(reason="...")` for ops-driven rollbacks (incident response, regulator request, etc.). The reason is required and lands in the audit chain.

The paper's §10.5 (Future Work) flags empirical characterization of demotion timing under realistic drift profiles as the natural follow-on to the transition curves.

## How is this different from Vowpal Wabbit / online learning?

Vowpal Wabbit is continuous adaptation with no rule floor and no
formal phase vocabulary. Postrule is specifically about the
*migration* from human-authored classifier to learned classifier,
with the migration gated by evidence and the human-authored
version preserved throughout. Different problem.

## Is Postrule "machine learning"?

Strictly, no. Postrule is an **MLOps framework** — an
orchestration runtime for graduating production classifiers
from rule to language model to learned ML, with paired-statistical gates
and a rule safety floor. The ML happens *inside* the switch
(your sklearn pipeline, your language-model adapter, your fine-tuned
model); Postrule is the deployment scaffold around it.

The closest formal ML subfield is **online model selection /
cascade routing** (FrugalGPT lineage, Dekoninck et al.). The
statistical machinery (paired McNemar, two-sided exact-binomial
on discordant pairs) is **classical sequential hypothesis
testing**, not ML proper.

Calling Postrule "ML" overclaims. Calling it "the deployment
runtime that ML ships into" is precise.

## How is this different from AutoML / H2O / Sagemaker Autopilot?

AutoML platforms search the model-and-hyperparameter space
**offline**, against a static labeled dataset, and output "the
best model." That's a useful tool for kicking off a greenfield
ML project. It's a different problem from what Postrule solves.

Postrule is the **online** companion: candidates flow in (from
AutoML output, from an autoresearch loop, from a human running
experiments), Postrule shadows them against live production
traffic, runs a head-to-head significance test on the same
inputs against a truth oracle, and tells you which candidates
statistically clear the bar. The rule safety floor protects production from bad
candidates throughout. A useful one-liner:

> **AutoML automates offline model selection.**
> **Postrule automates online model promotion.**

Where AutoML stops — at "here's the best candidate" — Postrule
picks up. The two compose: AutoML finds candidates; Postrule
gates their deployment.

See [`docs/autoresearch.md`](autoresearch.md) and
[`examples/19_autoresearch_loop.py`](../examples/19_autoresearch_loop.py)
for the full picture.

## How is this different from FrugalGPT, model routing, or LLM-cost cascades?

Routing picks which LLM to call for a given request. Every
routed call is still a remote LLM call; the savings come from
sending cheaper or smaller calls when the input allows it.

Postrule graduates the *site* off LLMs entirely once a small
in-process head has earned it. Once the paired-McNemar gate
fires for that site, the call drops from "LLM round-trip" to
"sub-millisecond local inference," and the per-call cost line
goes from cents-or-fractions-of-cents to electricity.

The two compose. Route to a cheaper LLM while you're
accumulating evidence; graduate to in-process inference once
the gate clears. Routing reduces the unit cost of a remote
call. Graduation removes the unit.

## How does this relate to Karpathy's "autoresearch" loop pattern?

> **Autoresearch tells you what to try. Postrule tells you when it worked.**

The autoresearch pattern is a *discovery* primitive: a language model (or
agent) proposes candidate classifiers / prompts / gating
thresholds, reads results, iterates. Where it falls down is the
last mile — getting candidates from "this looks promising on
the eval set" to "deployed in production with statistical
confidence." Teams duct-tape evals harnesses around their loops
and call it MLOps.

Postrule is the missing substrate. We ship a
[`CandidateHarness`](../src/postrule/autoresearch.py) that wraps a
live `LearnedSwitch`, lets an external loop register candidates,
shadows them against production traffic, and returns paired-
McNemar verdicts on whether each candidate beats the live
decision. The autoresearch loop reads
`report.recommend_promote`; the rule floor of the underlying
switch protects production from bad proposals throughout.

```python
from postrule import CandidateHarness, LearnedSwitch

sw = LearnedSwitch(rule=production_rule, ...)

def truth(input):
    return labeled_validation_set[input.id]  # or your reviewer/judge

harness = CandidateHarness(switch=sw, truth_oracle=truth, alpha=0.05)

# The autoresearch loop's iteration:
candidate = autoresearch_agent.propose_candidate(switch.outcome_log)
harness.register("v3_attempt_2", candidate)
harness.observe_batch(evaluation_traffic)
report = harness.evaluate("v3_attempt_2")

if report.recommend_promote:
    autoresearch_agent.commit_candidate(candidate)
```

Every primitive an autoresearch loop needs lines up with what
Postrule already ships:

| Autoresearch needs | Postrule ships |
|---|---|
| A way to evaluate candidates against real traffic | `CandidateHarness.observe()` + shadow phases |
| A statistical bar for "this candidate is better" | Head-to-head evidence gate at configurable `alpha` (`McNemarGate` by default) |
| Rollback if a candidate poisons production | Circuit breaker + rule floor |
| An audit trail of every promotion decision | Full outcome-log audit chain |
| A way to compare N candidates concurrently | `CandidateHarness.evaluate_all()` |

See [`examples/19_autoresearch_loop.py`](../examples/19_autoresearch_loop.py)
for the full loop end-to-end — a deterministic-faked agent
ratchets keyword-expansion candidates from a 55%-accurate
production rule up to 100% across four iterations, gated by
McNemar at every step.

## What's the latency overhead?

At Phase.RULE: `classify` is 0.96 µs p50 / 1.04 µs p95; `dispatch`
(classify + invoke matched action) is 1.00 µs p50 / 1.08 µs p95.
~24× a bare Python call (42 ns) in relative terms; ~1 µs in
absolute terms.

At Phase.MODEL_PRIMARY (model verifier stubbed): 1.46 µs p50.
At Phase.ML_PRIMARY (ML head stubbed): 1.50 µs p50. Real-LLM and
real-ML latency is dominated by the model, not by Postrule — for
the shipped local default `qwen2.5:7b` via Ollama, ~481 ms p50.

Storage: `BoundedInMemoryStorage` (default for ephemeral state)
sustains 12M writes/sec. `FileStorage` with batching (production-
recommended) sustains 245K writes/sec at 4.1 µs per write with a
~50 ms crash window. `FileStorage` unbatched per-call fsync is the
explicit opt-in for regulated workloads at 28K writes/sec (4
threads concurrent).

Full methodology + reproduce instructions in
[`docs/benchmarks/perf-baselines-2026-05-01.md`](benchmarks/perf-baselines-2026-05-01.md).

## What's the cost overhead?

Rule calls: negligible (your existing function is still there).
model calls: the API bill for whatever provider you point at.
ML calls: TF-IDF + LR is CPU-cheap and scikit-learn-shaped;
sentence-transformer heads cost more but are still local.

## Does Postrule call language models on my behalf without asking?

No. You configure an adapter (`OpenAIAdapter` / `AnthropicAdapter` /
`OllamaAdapter` / `LlamafileAdapter`) with your own credentials
and you pick the phase. At Phase.RULE the language model is never called.
At Phase.MODEL_SHADOW the language model is called but doesn't affect output.
At Phase.MODEL_PRIMARY the language model is called and its output is the
decision unless confidence is below a configured threshold.

## Is my data sent anywhere?

By default, nothing leaves your process. Verdict records go to
whatever storage backend you configure — `InMemoryStorage`,
`FileStorage`, or a custom `Storage` implementation. No Postrule
cloud, no telemetry home-call, no phone-home.

When Postrule Cloud ships (Q2 2026), opt-in hosted storage will
be a separate tier. The OSS library will never call home.

## What's the licensing situation?

Postrule is split-licensed:

- **Client SDK** (what you `import` — decorator, storage,
  adapters, telemetry, viz, benchmarks): **Apache License 2.0**.
  Free for any commercial use, including embedding in your own
  SaaS and selling it.
- **Postrule-operated components** (analyzer, ROI reporter,
  research/graduation tooling, CLI, future hosted surfaces):
  **Business Source License 1.1** with Change Date 2030-05-01
  auto-converting to Apache 2.0. You can use them in your own
  organization for any purpose (the Additional Use Grant
  explicitly permits production use against your own code);
  the only prohibited use is offering a hosted
  Postrule-derivative service to third parties.

See `LICENSE.md` and `LICENSING.md` for the developer-facing
breakdown. Commercial licensing that removes the BSL
restrictions is available — contact
`licensing@b-treeventures.com`.

## Why not Apache 2.0 throughout, like most libraries?

Because there's a patent. The filed US provisional (2026-04-21)
covers the graduated-autonomy method with statistically-gated
transitions. Apache 2.0's patent grant licenses every adopter
to practice the invention — which is fine for the client SDK
(we want people to adopt it), but leaves the analyzer and future
hosted components with no durable commercial lever against
hyperscaler cloning. BSL 1.1 gives the commercial components a
four-year moat-build window; they auto-convert to Apache 2.0
after the Change Date. This is the same pattern HashiCorp,
CockroachDB, and Sentry use.

## Is the patent going to be used against me?

No. Every Apache 2.0 client-SDK user automatically gets a
perpetual patent license via the Apache 2.0 patent grant
(Section 3 of the license). The patent's commercial use is
against third parties who (a) don't use the code, and (b) try
to offer a competing Postrule-derivative as a paid service.
Individual developers and companies using Postrule have nothing
to worry about.

## What counts as a "competing hosted service"? Can I run the analyzer internally?

Yes, internal use is explicitly permitted by the Additional Use
Grant in `LICENSE-BSL`. The grant language:

> Internal use within Your organization, including use by Your
> affiliates under common control, is not a competing offering.

The prohibited pattern is: taking Postrule's analyzer (or ROI
reporter, or future hosted surfaces), wrapping them in a UI,
and selling access to third parties as a service that competes
with what we plan to ship. If you're not sure whether your use
case crosses the line, email `licensing@b-treeventures.com` and
we'll tell you plainly.

## Can I depend on Postrule in a commercial library I ship to customers?

Short answer: **yes**, almost certainly without preconditions.
The SDK is Apache 2.0 — ship it freely, including in proprietary
commercial libraries. The four BSL 1.1 files
([`analyzer.py`](../src/postrule/analyzer.py),
[`cli.py`](../src/postrule/cli.py),
[`research.py`](../src/postrule/research.py),
[`roi.py`](../src/postrule/roi.py))
carry an explicit "production self-hosted use is permitted" carve-
out via the Additional Use Grant in `LICENSE-BSL`. The only
prohibited use case is operating a hosted Postrule-clone service to
third parties.

Keep three concerns separate (they have separate remedies and
shouldn't blur together):

1. **Copyright** (Apache 2.0 / BSL 1.1) covers the source-code text.
2. **Patent** (US provisional filed 2026-04-21) covers the
   graduated-autonomy primitive itself. Apache 2.0 §3 grants
   downstream users an implicit patent license **when they
   implement through Postrule**. Re-implementing the same primitive
   from scratch is exposed.
3. **Trademark** (POSTRULE, pending USPTO) covers the name.
   Nominative fair use ("my-lib integrates Postrule") is fine;
   brand-prominent use (`PostrulePro`, `Postrule-Enterprise`) needs
   permission — see [`TRADEMARKS.md`](../TRADEMARKS.md).

**Three reseller patterns + their exposure:**

- **A — Runtime SDK use only.** Zero BSL exposure. Your library
  can ship under any license, including proprietary. Most common
  case.
- **B — Build/dev-time analyzer/CLI use.** Fine; the BSL
  Additional Use Grant covers production self-hosted use. Only
  blocked: building a hosted analyzer-as-a-service.
- **C — Re-export of Postrule surface.** Copyright fine; trademark
  is the gotcha — you can't brand it as "Postrule-something."

**Attribution required for any redistribution:**

- Preserve the Apache 2.0 `NOTICE` (one-paragraph mention in
  your docs).
- Include `LICENSE-APACHE` (and `LICENSE-BSL` if you redistribute
  any BSL source verbatim).
- Preserve SPDX headers and copyright lines; don't remove or
  modify the per-file SPDX identifiers.

The three things a reseller could plausibly get a letter about:
operating a hosted SaaS that replicates Postrule's cloud surface
(BSL); stripping `NOTICE`/attribution and reshipping (Apache §4);
or using the POSTRULE mark in a brand-prominent / endorsement-
implying way (trademark). Everything else: ship freely. Friction-
case commercial licensing is available — contact
`licensing@b-treeventures.com`.

## What happens if my Postrule-using product gets acquired?

For the overwhelming majority of acquisitions, **nothing
changes**. The Postrule dependency shows up as one SBOM line item
during diligence:

```
postrule==X.Y.Z  Apache-2.0 (SDK) + LicenseRef-BSL-1.1 (4 files)
```

For ~95% of acquirers this is a green-light find — preserve
attribution, move on. No contract to sign, no renewal to budget,
no notification owed to B-Tree Labs. Apache 2.0 + BSL 1.1 are
both self-executing.

Three "ignore" senses, three different answers:

1. **Operationally invisible to B-Tree Labs?** Yes, almost
   entirely. A downstream acquirer never needs to contact us, pay
   us, notify us, or renew anything.
2. **Invisible in the acquired stack?** No — it'll appear as a
   line item in SBOM diligence. Usually green-light.
3. **Can the acquirer rip Postrule out?** Legally yes, economically
   usually negative-ROI (losing the Apache 2.0 §3 patent grant,
   re-implementing 6–12 engineer-months of SDK, losing cloud-
   feature compatibility).

**Three friction cases that DO matter at acquisition:**

1. **Acquirer is itself an ML-platform / classification-platform
   company.** BSL "no competing hosted service" applies downstream
   too. Three exits: keep the acquired product self-hosted, buy
   a commercial license, or rip out the four BSL files and keep
   only the Apache SDK.
2. **Acquirer has procurement policy rejecting non-OSI licenses.**
   BSL is source-available, not OSI-approved. Two exits: a
   commercial license that strips BSL, or keep only the Apache
   SDK and replace dev/CI tooling (feasible — analyzer/CLI/
   research/ROI are dev tools, not runtime).
3. **Acquirer wants to white-label and brand-prominently re-skin.**
   Trademark gate, separate from copyright. Nominative use
   ("based on Postrule") stays free; brand-prominent rebranding
   needs a trademark license.

The clean pitch-deck line for an acquisition-aware customer:

> "LibX depends on Postrule (Apache 2.0 SDK + per-file BSL 1.1 on
> CLI/analyzer/dev tools, with a production self-hosted carve-
> out). In an acquisition this shows up as a green-light SBOM
> line item for ~95% of acquirers. The exceptions: if the
> acquirer is itself an ML platform offering a competing hosted
> service, has an OSI-only procurement policy, or wants to
> white-label with their own brand front-and-center. In those
> cases B-Tree Labs sells commercial licenses that resolve the
> friction in a single transaction."

If a friction case IS in play, route to
`licensing@b-treeventures.com`.

## Are the benchmarks real or cherry-picked?

Real. The four benchmarks (ATIS, HWU64, Banking77, CLINC150)
are all public classification corpora; the rules are
hand-written (or straightforward extractions from the training
set); the ML heads are scikit-learn defaults; the paired
McNemar tests use the full evaluation sets. The benchmark
loaders and rule definitions are in `src/postrule/benchmarks/`
for anyone to inspect and reproduce with `postrule bench
<dataset>`.

## Why are you publishing this now?

Because the filed provisional patent locks the priority date,
and waiting any longer risks a fast-follower shipping their own
version with their own priority date. The paper, library,
analyzer, and benchmarks are ready; the launch is happening
now.

## Is this abandoned in six months?

We have a three-year business plan and a year-one revenue
target that is bootstrap-sustainable. The founder is working
full-time on Postrule. The structural commitment we can put in
the open repo: if we can't sustain the business, the BSL code
automatically converts to Apache 2.0 on 2030-05-01 —
regardless of whether B-Tree Ventures exists by then, the
code is still there for the community.

## Who's behind Postrule?

Benjamin Booth, sole inventor and sole operator of B-Tree
Ventures, LLC (dba B-Tree Labs). Clean B-Tree Ventures work, no
academic or institutional co-ownership.

## How do I try it?

```bash
pip install postrule
postrule analyze /path/to/your/python/code
```

Gallery of runnable examples in
[`examples/`](../examples/). Start with
`01_hello_world.py`.

## How do I get help?

See [SUPPORT.md](../SUPPORT.md). Short version:

- GitHub issues for bugs / features / questions.
- GitHub Discussions for open-ended conversations.
- Private security advisories for vulnerabilities.
- `licensing@b-treeventures.com` for commercial licensing.
- `partners@b-treeventures.com` for the design-partner program.
