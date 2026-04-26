# Why your autoresearch loop needs a deployment substrate

> **Autoresearch tells you what to try.**
> **Dendra tells you when it worked.**

## The problem

Autoresearch loops — the pattern Andrej Karpathy and others have
been describing publicly — wire a language model (or agent) to a discovery
cycle: propose a hypothesis, run an experiment, read the result,
iterate. They're great at generating ideas. They're getting
better fast. The pieces — proposal language models, eval harnesses, agent
scaffolds — are converging.

There's one piece missing.

When the loop produces a candidate that looks promising on the
eval set, **what do you do with it?** The honest answer in most
shops today: somebody copies the candidate into a PR, runs CI,
ships it during business hours, and watches the dashboards.

That works when the cost of a wrong promotion is small. It does
not work when the cost is real — when the candidate replaces a
classifier that gates HIPAA-relevant routing, controls retry
budgets at the edge, or decides which support tickets escalate
to oncall. For those, "the eval set looked good" is not the
deployment bar. The deployment bar is **paired statistical
significance against live production traffic, with a rollback
path that survives the candidate going wrong.**

That's the gap Dendra fills.

## The pattern

Dendra ships a `CandidateHarness` that wraps a production
`LearnedSwitch`. An external loop — autoresearch agent, A/B
harness, human running experiments — registers candidate
classifiers with the harness. Every observed input runs through
both production and every candidate. A truth oracle (labeled
validation set, downstream signal, reviewer pool, language-model judge
committee with bias guardrails) provides ground truth. The
harness pairs them, runs a head-to-head significance test on
the discordant pairs (McNemar's exact-binomial under the hood;
swappable via the `Gate` protocol), and returns a
`CandidateReport` with a recommendation.

```python
from dendra import CandidateHarness, LearnedSwitch

# The production switch.
sw = LearnedSwitch(rule=production_rule, ...)

# Truth oracle. In a real loop, this is a labeled validation
# set, a downstream signal that resolves later, a reviewer
# pool's verdict aggregator, or a high-quality language-model judge
# committee with bias guardrails.
def truth(input):
    return ground_truth_lookup[input.id]

harness = CandidateHarness(
    switch=sw,
    truth_oracle=truth,
    alpha=0.05,
)

# The autoresearch loop's iteration:
candidate = autoresearch_agent.propose_candidate(sw.storage)
harness.register("v3_attempt_2", candidate)
harness.observe_batch(evaluation_traffic)
report = harness.evaluate("v3_attempt_2")

print(report.summary_line())
# [PROMOTE] v3_attempt_2: prod=70.0% candidate=88.7% (n=247, b=43, c=12, p=1.20e-05, alpha=0.05)

if report.recommend_promote:
    autoresearch_agent.commit_candidate(candidate)
```

The harness is the loop's substrate. The loop's job is to
propose; Dendra's job is to gate.

## Why every primitive lines up

Autoresearch loops need infrastructure they typically don't
have. Dendra was built for a different stated reason — graduated
ML adoption — but every primitive turns out to be exactly what
the loop needs:

| Autoresearch needs | Dendra ships |
|---|---|
| A way to evaluate candidates against real traffic | `CandidateHarness.observe()` + the switch's shadow phases |
| A statistical bar for "this candidate is better" | Head-to-head evidence gate at configurable `alpha` (`McNemarGate` by default) |
| Rollback if a candidate poisons production | Circuit breaker + rule safety floor (paper §7.1 architectural guarantee) |
| An audit trail of every promotion decision | Full outcome-log audit chain |
| A way to compare N candidates concurrently | `CandidateHarness.evaluate_all()` returns ranked reports |
| Async-aware committee judging for the truth oracle | `JudgeCommittee.ajudge` runs N model judges in parallel |
| HIPAA / PII redaction at the storage boundary | `Storage(redact=fn)` hook |

## What the harness does NOT do

The harness deliberately does **not** modify the production
switch. Candidates run alongside, never instead of. To actually
promote a winning candidate to production, the autoresearch loop
swaps it into the switch via the existing `LearnedSwitch` surface
— typically guarded by your normal deployment process (PR
review, canary rollout, feature flag). The harness's job is to
tell the loop **when** the swap is statistically justified, not
to perform it.

This is deliberate. We've watched too many auto-promote-on-eval
systems put bad changes into production with a green statistical
indicator that turned out to be measuring the wrong thing. The
harness gives you the evidence; the deploy decision still goes
through your team's normal process.

## Worked example

[`examples/19_autoresearch_loop.py`](../examples/19_autoresearch_loop.py)
runs a deterministic-faked autoresearch loop end-to-end. The
production rule catches bugs phrased with the word "crash" — a
day-zero hand-written keyword filter. The world contains bugs
phrased with "error", "down", "stuck", and "broken" too. The
production rule misses them. Production accuracy: **55%.**

The loop ratchets:

```
iter 1: v1_kw2  kw=['crash', 'error']
        prod_acc=55.0%  cand_acc=70.0%  b=15  c=0  p=6.10e-05  -> PROMOTE
iter 2: v2_kw3  kw=['crash', 'error', 'down']
        prod_acc=55.0%  cand_acc=85.0%  b=30  c=0  p=1.86e-09  -> PROMOTE
iter 3: v3_kw4  kw=['crash', 'error', 'down', 'stuck']
        prod_acc=55.0%  cand_acc=90.0%  b=35  c=0  p=5.82e-11  -> PROMOTE
iter 4: v4_kw5  kw=['crash', 'error', 'down', 'stuck', 'broken']
        prod_acc=55.0%  cand_acc=100.0%  b=45  c=0  p=5.68e-14  -> PROMOTE
```

The harness ran a head-to-head significance test on every
candidate against production on the same 100 inputs. Every
iteration cleared `p < 0.05`. The loop knows it can promote
because the statistics say so.

In a production setup, you'd plug a language-model agent in place of the
deterministic ratchet. The agent reads the outcome log
(`sw.storage.load_records(sw.name)`) — finds inputs where
production was wrong — proposes a refinement — registers it —
gets a verdict — iterates. The loop is a few hundred lines of
agent code; the harness handles the gating.

## Where this fits in the roadmap

`CandidateHarness` ships in v1 of the Dendra Python library
(`pip install dendra`). The eval-loop primitives are sync
today; an async peer using the existing `aclassify` /
`abulk_record_verdicts_from_source` surface lands in v1.1. Both
sit on top of the existing `LearnedSwitch` and `Gate` protocols
— if you're already using Dendra for graduated ML adoption, the
harness slots in alongside without disturbing your current
switches.

For builders of autoresearch / agentic systems specifically:
the harness is an explicit, named seam. We expect the
interesting integrations — Anthropic Claude agents,
LangGraph / LlamaIndex flows, custom proposal-eval loops in
research labs — to wire `CandidateHarness` as their evals
substrate. If you're shipping one, [we'd like to hear about it
on GitHub](https://github.com/axiom-labs-os/dendra/issues).

## See also

- [`docs/api-reference.md`](api-reference.md) — `CandidateHarness` and
  `CandidateReport` API.
- [`docs/verdict-sources.md`](verdict-sources.md) — `VerdictSource`
  family for sourcing truth (model judges, committees, webhooks,
  human reviewers). The harness's `truth_oracle` parameter
  accepts any of these.
- [`docs/async.md`](async.md) — async classify / dispatch /
  judge / committee. The harness sync API today; async peer in v1.1.
- [`examples/19_autoresearch_loop.py`](../examples/19_autoresearch_loop.py) —
  runnable end-to-end loop.
- The paper at [arXiv: Dendra — When Should a Rule Learn?](#) (link
  on launch day) covers the statistical machinery the harness uses.
