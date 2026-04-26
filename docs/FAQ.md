# Dendra FAQ

Answers to the questions people ask first. Updated 2026-04-22.

## What is Dendra, in one sentence?

A Python decorator that wraps a classification function and lets
it graduate from rule → model-shadow → language model → ML-shadow → ML —
with a paired-proportion statistical gate at every transition
and the original rule retained as the safety floor.

## Do I actually need this? Can't I just use if/else?

For some classifiers, yes — and we'll say so directly.

If your classifier has 3-5 stable cases, no meaningful drift,
no audit requirement, and lives in a non-critical path, **you
probably don't need Dendra**. A plain if/else block is the
right tool. We'd rather you ship that.

Dendra is for classifiers where one of these is true:

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
We mean it. Dendra is opinionated about being a primitive for
production-grade classification — not a general-purpose
dispatcher.

## Why not just use shadow mode / A-B testing / a feature flag?

Shadow mode says "run both, log both." Dendra says "run both,
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

## How is this different from Vowpal Wabbit / online learning?

Vowpal Wabbit is continuous adaptation with no rule floor and no
formal phase vocabulary. Dendra is specifically about the
*migration* from human-authored classifier to learned classifier,
with the migration gated by evidence and the human-authored
version preserved throughout. Different problem.

## Is Dendra "machine learning"?

Strictly, no. Dendra is an **MLOps framework** — an
orchestration runtime for graduating production classifiers
from rule to language model to learned ML, with paired-statistical gates
and a rule safety floor. The ML happens *inside* the switch
(your sklearn pipeline, your language-model adapter, your fine-tuned
model); Dendra is the deployment scaffold around it.

The closest formal ML subfield is **online model selection /
cascade routing** (FrugalGPT lineage, Dekoninck et al.). The
statistical machinery (paired McNemar, two-sided exact-binomial
on discordant pairs) is **classical sequential hypothesis
testing**, not ML proper.

Calling Dendra "ML" overclaims. Calling it "the deployment
runtime that ML ships into" is precise.

## How is this different from AutoML / H2O / Sagemaker Autopilot?

AutoML platforms search the model-and-hyperparameter space
**offline**, against a static labeled dataset, and output "the
best model." That's a useful tool for kicking off a greenfield
ML project. It's a different problem from what Dendra solves.

Dendra is the **online** companion: candidates flow in (from
AutoML output, from an autoresearch loop, from a human running
experiments), Dendra shadows them against live production
traffic, runs a head-to-head significance test on the same
inputs against a truth oracle, and tells you which candidates
statistically clear the bar. The rule safety floor protects production from bad
candidates throughout. A useful one-liner:

> **AutoML automates offline model selection.**
> **Dendra automates online model promotion.**

Where AutoML stops — at "here's the best candidate" — Dendra
picks up. The two compose: AutoML finds candidates; Dendra
gates their deployment.

See [`docs/autoresearch.md`](autoresearch.md) and
[`examples/19_autoresearch_loop.py`](../examples/19_autoresearch_loop.py)
for the full picture.

## How does this relate to Karpathy's "autoresearch" loop pattern?

> **Autoresearch tells you what to try. Dendra tells you when it worked.**

The autoresearch pattern is a *discovery* primitive: a language model (or
agent) proposes candidate classifiers / prompts / gating
thresholds, reads results, iterates. Where it falls down is the
last mile — getting candidates from "this looks promising on
the eval set" to "deployed in production with statistical
confidence." Teams duct-tape evals harnesses around their loops
and call it MLOps.

Dendra is the missing substrate. We ship a
[`CandidateHarness`](../src/dendra/autoresearch.py) that wraps a
live `LearnedSwitch`, lets an external loop register candidates,
shadows them against production traffic, and returns paired-
McNemar verdicts on whether each candidate beats the live
decision. The autoresearch loop reads
`report.recommend_promote`; the rule floor of the underlying
switch protects production from bad proposals throughout.

```python
from dendra import CandidateHarness, LearnedSwitch

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
Dendra already ships:

| Autoresearch needs | Dendra ships |
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

At Phase.RULE with `auto_record=False`, **0.50 µs p50** over the
bare rule call. With the default `auto_record=True` it's 1.67 µs
p50 (writes an UNKNOWN outcome record each call). Measured in
`tests/test_latency_pinned.py` on Apple M5 / Python 3.13.

At Phase.ML_WITH_FALLBACK with a TF-IDF + logistic head, ~105 µs
p50 — well inside typical web-request budgets. At MODEL_PRIMARY
with a local llama3.2:1b, ~250 ms (dominated by the language model, not
Dendra). `persist=True` (batched FileStorage) adds 33 µs p50;
per-call fsync durability is an explicit 195 µs opt-in for
regulated workloads. See `docs/benchmarks/v1-audit-benchmarks.md`
for the full matrix.

## What's the cost overhead?

Rule calls: negligible (your existing function is still there).
model calls: the API bill for whatever provider you point at.
ML calls: TF-IDF + LR is CPU-cheap and scikit-learn-shaped;
sentence-transformer heads cost more but are still local.

## Does Dendra call language models on my behalf without asking?

No. You configure an adapter (`OpenAIAdapter` / `AnthropicAdapter` /
`OllamaAdapter` / `LlamafileAdapter`) with your own credentials
and you pick the phase. At Phase.RULE the language model is never called.
At Phase.MODEL_SHADOW the language model is called but doesn't affect output.
At Phase.MODEL_PRIMARY the language model is called and its output is the
decision unless confidence is below a configured threshold.

## Is my data sent anywhere?

By default, nothing leaves your process. Verdict records go to
whatever storage backend you configure — `InMemoryStorage`,
`FileStorage`, or a custom `Storage` implementation. No Dendra
cloud, no telemetry home-call, no phone-home.

When Dendra Cloud ships (Q2 2026), opt-in hosted storage will
be a separate tier. The OSS library will never call home.

## What's the licensing situation?

Dendra is split-licensed:

- **Client SDK** (what you `import` — decorator, storage,
  adapters, telemetry, viz, benchmarks): **Apache License 2.0**.
  Free for any commercial use, including embedding in your own
  SaaS and selling it.
- **Dendra-operated components** (analyzer, ROI reporter,
  research/graduation tooling, CLI, future hosted surfaces):
  **Business Source License 1.1** with Change Date 2030-05-01
  auto-converting to Apache 2.0. You can use them in your own
  organization for any purpose (the Additional Use Grant
  explicitly permits production use against your own code);
  the only prohibited use is offering a hosted
  Dendra-derivative service to third parties.

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
to offer a competing Dendra-derivative as a paid service.
Individual developers and companies using Dendra have nothing
to worry about.

## What counts as a "competing hosted service"? Can I run the analyzer internally?

Yes, internal use is explicitly permitted by the Additional Use
Grant in `LICENSE-BSL`. The grant language:

> Internal use within Your organization, including use by Your
> affiliates under common control, is not a competing offering.

The prohibited pattern is: taking Dendra's analyzer (or ROI
reporter, or future hosted surfaces), wrapping them in a UI,
and selling access to third parties as a service that competes
with what we plan to ship. If you're not sure whether your use
case crosses the line, email `licensing@b-treeventures.com` and
we'll tell you plainly.

## Are the benchmarks real or cherry-picked?

Real. The four benchmarks (ATIS, HWU64, Banking77, CLINC150)
are all public classification corpora; the rules are
hand-written (or straightforward extractions from the training
set); the ML heads are scikit-learn defaults; the paired
McNemar tests use the full evaluation sets. The benchmark
loaders and rule definitions are in `src/dendra/benchmarks/`
for anyone to inspect and reproduce with `dendra bench
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
full-time on Dendra. The structural commitment we can put in
the open repo: if we can't sustain the business, the BSL code
automatically converts to Apache 2.0 on 2030-05-01 —
regardless of whether B-Tree Ventures exists by then, the
code is still there for the community.

## Who's behind Dendra?

Benjamin Booth, sole inventor and sole operator of B-Tree
Ventures, LLC (dba Axiom Labs). Clean B-Tree Ventures work, no
academic or institutional co-ownership.

## How do I try it?

```bash
pip install dendra
dendra analyze /path/to/your/python/code
```

Gallery of runnable examples in
[`examples/`](../examples/). Start with
`01_hello_world.py`. No accounts, no API keys, no phone-home.

## How do I get help?

See [SUPPORT.md](../SUPPORT.md). Short version:

- GitHub issues for bugs / features / questions.
- GitHub Discussions for open-ended conversations.
- Private security advisories for vulnerabilities.
- `licensing@b-treeventures.com` for commercial licensing.
- `partners@b-treeventures.com` for the design-partner program.
