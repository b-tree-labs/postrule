# Dendra FAQ

Answers to the questions people ask first. Updated 2026-04-22.

## What is Dendra, in one sentence?

A Python decorator that wraps a classification function and lets
it graduate from rule → LLM-shadow → LLM → ML-shadow → ML —
with a paired-proportion statistical gate at every transition
and the original rule retained as the safety floor.

## Why not just use shadow mode / A-B testing / a feature flag?

Shadow mode says "run both, log both." Dendra says "run both,
log both, **and tell me when the evidence is strong enough to
switch**." The statistical transition gate is the load-bearing
piece — without it, teams either switch too early (regression
risk) or never switch at all (the "we'll migrate to ML one day"
tech-debt backlog every production team has). See the paper's
§3.3 for the theorem bounding the probability of
worse-than-rule behavior by the test's α.

## Why is there a *rule*? Isn't the LLM supposed to replace the rule?

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

## How is this different from AutoML / H2O / Sagemaker Autopilot?

AutoML platforms pick a model given a training set. They don't
address the rule-to-ML migration path, don't provide a safety
floor, and don't graduate phases based on production outcome
data. Dendra is a primitive for production systems that already
have a rule; AutoML is a tool for kicking off a greenfield ML
project with labeled data.

## What's the latency overhead?

At Phase.RULE with `auto_record=False`, **0.50 µs p50** over the
bare rule call. With the default `auto_record=True` it's 1.67 µs
p50 (writes an UNKNOWN outcome record each call). Measured in
`tests/test_latency_pinned.py` on Apple M5 / Python 3.13.

At Phase.ML_WITH_FALLBACK with a TF-IDF + logistic head, ~105 µs
p50 — well inside typical web-request budgets. At MODEL_PRIMARY
with a local llama3.2:1b, ~250 ms (dominated by the LLM, not
Dendra). `persist=True` (batched FileStorage) adds 33 µs p50;
per-call fsync durability is an explicit 195 µs opt-in for
regulated workloads. See `docs/working/v1-audit-benchmarks.md`
for the full matrix.

## What's the cost overhead?

Rule calls: negligible (your existing function is still there).
LLM calls: the API bill for whatever provider you point at.
ML calls: TF-IDF + LR is CPU-cheap and scikit-learn-shaped;
sentence-transformer heads cost more but are still local.

## Does Dendra call LLMs on my behalf without asking?

No. You configure an adapter (`OpenAIAdapter` / `AnthropicAdapter` /
`OllamaAdapter` / `LlamafileAdapter`) with your own credentials
and you pick the phase. At Phase.RULE the LLM is never called.
At Phase.MODEL_SHADOW the LLM is called but doesn't affect output.
At Phase.MODEL_PRIMARY the LLM is called and its output is the
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

We have a three-year plan, documented publicly in
`docs/working/roadmap-2026-04-20.md` and
`docs/marketing/entry-with-end-in-mind.md`. Year-one revenue
target (bootstrap-sustainable) is $225k-$540k; we're building
toward a $10M-ARR business over three years with the
Snyk-Temporal hybrid pattern. The founder is working full-time
on Dendra. If we can't sustain that business, we've promised
the BSL code automatically converts to Apache 2.0 by
2030-05-01 — regardless of whether B-Tree Ventures exists by
then, the code's still there for the community.

## Who's behind Dendra?

Benjamin Booth, sole inventor and sole operator of B-Tree
Ventures, LLC (dba Axiom Labs). Full commercial + IP provenance
documented in `docs/working/patent-strategy.md` §7 — clean
B-Tree Ventures work, no academic or institutional
co-ownership.

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
