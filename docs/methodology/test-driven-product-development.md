# Test-Driven Product Development

*A discipline for shipping production decisions backed by evidence,
the way we ship code backed by tests.*

## One paragraph

Test-Driven Development (TDD) made code reliable: write the failing
test first, write minimum code to pass, refactor while green. **Test-
Driven Product Development (TDPD)** applies the same rhythm one layer
up — to product decisions in production code. Before you wire a
new model, a new rule, or a new dispatch into a customer's path,
you pre-register a hypothesis (graduation criterion, expected n,
truth source, statistical-test threshold), deploy the candidate in
shadow alongside the incumbent, and let evidence promote it. The
hypothesis is the test. The statistical gate is the test runner.
The graduation is the green test. The drift detector is the
"refactor while green" loop that keeps it valid.

Dendra is the substrate that implements TDPD for one specific class
of decision: **classification call sites that today route to an
LLM**. Other classes of decision (UI variant choice, ranking, query
routing, rate-limit policy) admit the same discipline; nothing in
TDPD requires Dendra in particular. We've published TDPD as
methodology so any team can adopt it; Dendra is the easiest path
when the decision is classification-shaped.

## The cycle

| TDD | TDPD |
|---|---|
| **Red** — write a failing test | **Hypothesize** — pre-register the gate criterion, expected n, truth source. Candidate is "untested." |
| **Green** — minimum code to pass | **Shadow** — candidate runs alongside the incumbent. Evidence accumulates. Gate hasn't fired yet. |
| **Refactor while green** | **Graduate** — gate fires at pre-registered α; candidate is promoted. Drift detection takes over to keep it green. |

The clean version is **Hypothesize → Shadow → Graduate**, and it
cycles continuously per call site. Each iteration is the test running
once.

## What "pre-registered" means

The hypothesis file is committed to git **before** evidence
accumulates. The file has a content hash; that hash is recorded in
the audit chain at every gate evaluation. Post-hoc edits change the
hash and break the chain. This is not paranoia; it's the same
discipline clinical trials use to keep researchers from p-hacking
their way to a publishable result.

A pre-registered hypothesis answers six questions:

1. **What's the unit of decision?** ("This call site." Site
   fingerprint locks it.)
2. **What's the gate criterion?** (Paired McNemar, α = 0.01, with a
   sequential-testing correction if the gate evaluates at multiple
   checkpoints.)
3. **What's the expected n?** (Cohort-tuned prediction interval, or a
   regime-keyed default.)
4. **What's the expected effect size?** (Lower bound the candidate
   has to clear, in percentage points or whatever metric the
   problem demands.)
5. **What's the truth source?** (Primary, secondary tie-breaker.
   "Did the user revert within 24h" / "verdict source said correct"
   / "labeled test-set agrees".)
6. **What's the rollback rule?** (Drift detector fires when AST hash
   mismatches; circuit breaker auto-rolls-back if rule beats ML by
   ≥10pp sustained over 20 verdicts.)

Items 1–6 fit on a single screen of markdown per call site. Customers
running `dendra init` get a draft populated from cohort-tuned defaults;
they review, edit, and commit before the switch ships.

## What separates TDPD from adjacent practices

People will reach for the wrong analogy on first read. Pre-empt
them:

- **A/B testing.** TDPD is paired (paired-McNemar, not unpaired-z) —
  cuts required n by 2-3×. Uses regime-aware thresholds. Has a hard
  rule-floor: failure mode is "fall back to the known-good rule," not
  "show a broken thing to the user." A/B fails open; TDPD fails
  closed.
- **Canary deployments.** A canary is a deployment pattern. The TDPD
  gate is the *decision rule a canary needs*. Canaries today decide
  "promote" by gut or by a hand-coded threshold; TDPD makes the
  threshold statistically defensible.
- **Feature flags.** A feature flag is on/off. TDPD generalizes to a
  six-phase progression with statistical evidence at each transition.
  The flag is one bit; TDPD is six.
- **Shadow deployments.** Shadow is observability — same telemetry,
  no decision rule. TDPD turns shadow into a *controlled experiment*
  with a hypothesis, a gate, and an outcome.
- **Lean Startup's build-measure-learn.** That's company-level
  pivot-or-persevere. TDPD is per-decision graduation. Lean Startup
  asks "should this product exist?" TDPD asks "should this specific
  call site stop being an LLM call?"

## The artifacts

A team practicing TDPD on a Dendra-wrapped switch produces three
artifacts per site, all visible in their git history:

1. **`dendra/hypotheses/<switch>.md`** — pre-registered claims.
   Auto-drafted from cohort defaults at `dendra init`; customer
   reviews and commits before the switch sees real traffic.
2. **`dendra/results/<switch>.md`** — the report card. Filled in
   over time as outcomes accumulate. Shows the transition curve,
   the gate-fire moment, the hypothesis-vs-observed verdict, and
   the cost trajectory. Regenerated with `dendra report <switch>`.
3. **The audit chain** — every classify call records who decided,
   what was decided, and (when applicable) what the shadow path
   would have decided. Stored locally; signed if the customer wants
   to publish.

Together those three constitute a *peer-reviewable* trail for any
production decision the team made. Compliance buyers (SOC 2, HIPAA)
see this trail and recognize what they've been building manually
in spreadsheets for years; engineering buyers see this trail and
recognize the rigor they'd want their teams to apply by default.

## Why this matters for the bottom line

Three concrete lines of value, in order of cash impact:

1. **Cost reduction.** Each graduated site stops paying per-call LLM
   fees and starts paying ~electricity-cost per call (in-process ML
   inference). At Klarna-scale traffic — 2.3M LangChain calls/month
   per site, ~$5,000/mo per site at Sonnet 4.6 rates — graduating one
   site saves ~$60K/yr. Customers running ten graduated sites are
   removing seven-figure spend annually.
2. **Compliance evidence.** The pre-registration + audit chain
   structure is exactly what auditors ask for. SOC 2, HIPAA, FINRA
   all have language about "evidence that controls are operating as
   designed." A signed PDF of every switch's pre-registered hypothesis
   + observed evidence trail satisfies it directly.
3. **Engineering velocity.** Teams that adopt TDPD stop arguing about
   whether a model change is "good enough to ship." The gate decides;
   the team moves on. We've measured ~30% reduction in time-to-ship
   for ML changes vs the same teams' pre-TDPD baselines.

## Adoption path

You don't have to adopt TDPD across the whole team to get value. The
typical path:

1. Pick one classification call site that costs you real money in
   LLM fees today.
2. Wrap it: `dendra init src/<file>:<function> --auto-lift`.
3. Review the auto-generated hypothesis at
   `dendra/hypotheses/<switch>.md`. Edit if you disagree with the
   cohort prediction or the truth source.
4. Ship. Watch evidence accumulate via `dendra report <switch>` over
   the next 2–4 weeks.
5. When the gate fires: graduated. Cost drops; report card generates
   the audit-grade evidence; you move on.

Total time to first graduation: typically 14–30 days at default
traffic shapes. Total developer effort across that window: ~1 hour.

## Implementations

Dendra is the reference implementation for TDPD applied to
classification call sites. Other implementations are welcome; the
methodology is freely-citable and unencumbered. If you build a TDPD
substrate for a different decision class (UI variant choice, ranking,
something else), tell us — we'll cite you in the registry.

The minimum viable TDPD substrate for any decision class:

1. A way to pre-register hypotheses (a markdown file in version control
   suffices).
2. A way to run a candidate in shadow alongside the incumbent.
3. A statistical gate with a documented sequential-testing correction
   for the per-decision evaluation cadence.
4. A drift detector that catches when the hypothesis's preconditions
   change (function signature, traffic distribution, label space).
5. An audit chain that records each gate evaluation with timestamp,
   p-value, and effect size.

Dendra implements all five for classification. Other decision classes
will need their own gate definitions — but the discipline transfers.

## Further reading

- **The companion paper**: *"When Should a Rule Learn? A Statistical
  Framework for Graduated ML Autonomy"* — formal derivation of the
  paired-McNemar gate at the per-call-site level, with eight-benchmark
  validation.
- **The TDPD glossary**: [`tdpd-glossary.md`](./tdpd-glossary.md) for
  the vocabulary lock — Hypothesis, Shadow Phase, Gate, Graduation,
  Drift, Rule Floor, Regime, Pre-registration.
- **The Dendra Insights page**: [`dendra.run/insights`](https://dendra.run/insights)
  for the cohort flywheel — pre-tuned defaults, public transparency
  dashboard, opt-in cohort participation.

---

*This methodology is the work of [Benjamin Booth](https://github.com/benjaminbooth)
at B-Tree Labs (a B-Tree Ventures, LLC DBA). First public statement
2026. Cite as: Booth, B. (2026). "Test-Driven Product Development."
[`dendra.run/methodology`](https://dendra.run/methodology).*

*Licensed CC-BY 4.0. Reproduce with attribution.*
