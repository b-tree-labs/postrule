# When does Dendra pay off? — concrete scenarios

The examples in `examples/` advertise specific measurable
benefits. This doc turns each claim into a concrete picture:
the kind of team, workload, and decision where the benefit
actually shows up. Read it if the example's punchline is
abstract and you want to know whether your situation is the
one the example is talking about.

Every claim falls into one of two buckets:

- **Demonstrated** — a measurement from this repository's
  benchmarks or a cited paper. Numbers are real.
- **Reasoned scenario** — an industry-typical situation
  where the claim mechanically applies. Where a third-party
  industry source establishes the surrounding economics
  (volumes, breach costs, fraud losses) we cite it; we do
  not invent dollar figures.

Status of each section is marked inline.

## Industrial-scale fits at a glance

Dendra's primitives apply broadly, but the economic case is
strongest where **decision volume is high** *and* **the cost of
a wrong decision is large** *and* **a hand-written rule already
exists** that the team is afraid to ML-replace. Concretely:

| Industry | Decision | Volume | Stakes (industry-cited) |
|---|---|---|---|
| **Card-fraud screening** at issuing banks | Approve / decline a transaction | Visa alone: ~750M auths/day [^visa] | $33.8B global card fraud, 2022 [^nilson] |
| **AML / sanctions transaction monitoring** | Flag for compliance review | Top-10 banks: 1M+ alerts/day | $36B in AML fines 2008-2023 [^fenergo] |
| **SOC alert triage** at Fortune 500 | Escalate / suppress / auto-resolve | 100k–1M alerts/day per enterprise [^splunk] | $4.45M average breach cost [^ibm] |
| **Healthcare prior-authorization** | Approve / deny / escalate | US payers: ~1.5B requests/yr [^aha] | $93B/yr admin spend on prior auth [^cms] |
| **Adtech brand-safety classification** | Allow / block ad-creative / inventory | RTB: 100B+ auctions/day [^iab] | Advertiser pull-back on misclassification |
| **Returns-fraud triage** at large e-com | Refund / hold / investigate | 100k+ returns/day per top-50 retailer | $101B/yr US returns fraud, 2023 [^nrf] |
| **Customs HTS classification** | Assign tariff code on shipments | ~30M US import lines/yr [^cbp] | Duty mis-classification penalties; CBP audits |

Each row maps onto one or more of the per-benefit scenarios
below. The pattern is the same in every case: a static rule is
the safe baseline; a learned policy lifts accuracy; the cost
of an unsanctioned promotion is large enough that statistical
gating earns its keep.

[^visa]: Visa public investor reporting, ~276B transactions/yr.
[^nilson]: Nilson Report Issue 1232, "Card Fraud Losses Reach $33.83 Billion".
[^fenergo]: Fenergo "AML Fines Tracker" cumulative summary.
[^splunk]: Splunk "State of Security 2024" — median enterprise alert volume.
[^ibm]: IBM "Cost of a Data Breach Report 2024" — global average.
[^aha]: American Hospital Association prior-auth burden studies, 2022.
[^cms]: CMS / Council for Affordable Quality Healthcare administrative cost index.
[^iab]: IAB programmatic-ad market sizing; OpenRTB auction volume.
[^nrf]: National Retail Federation 2023 Consumer Returns Report.
[^cbp]: U.S. Customs & Border Protection annual trade statistics.

## Anti-scenarios: these look like classification but aren't

Code that resembles a classifier but won't fit cleanly under `@ml_switch` (or its v1 native peer, `dendra.Switch`):

- **Tests and fixtures.** A pytest function (`def test_routes_correctly(client): ...`) returns assertions, not labels. The analyzer filters these out; do not wrap them.
- **Validators.** A function that returns `True` / `False` (or raises on invalid) is a binary predicate, not a multi-class classifier. If the predicate is one branch of a real classification problem (allow / deny / escalate), wrap the surrounding decision instead.
- **Output is a tuple, dict, dataclass, or computed scalar.** A function that returns `(price, currency, jurisdiction)` is computing a value, not picking a label. The classifier should pick the strategy; the strategy returns the value.
- **Decisions that need hidden out-of-process state we can't expose.** If the rule's branch depends on a remote service whose response cannot be packed into evidence, the LLM/ML head will never see what the rule saw. Auto-lift refuses with a specific diagnostic (see [`limitations.md`](./limitations.md) section 3).
- **Order-dependent / state-machine functions.** If `f(x)` at time T depends on what `f(...)` returned at T-1, the function is a state machine. Per-input independence is load-bearing for paired-correctness math; lifting a state machine breaks the gate's evidentiary content.

For the full enumeration with version tags and the path forward for each category, see [`limitations.md`](./limitations.md).

## False-promotion rate, bounded by `alpha`

> *Example 19 claim: a loop that promotes any candidate with
> a positive accuracy delta has a noise-floor false-promotion
> rate close to 50% on small wins. ``CandidateHarness`` caps
> false promotions at the alpha you pass.*

Status: **Demonstrated** (the math is from the McNemar
literature; this repo's example 19 produces p-values that
match the predicted distribution).

### Where it pays off

**Scenario A — autoresearch loop running unattended overnight.**
You let a language-model agent propose 30 classifier refinements while
you sleep. Without statistical gating, the agent ships
whichever variant happened to win on tonight's traffic — even
the ones that won by 1pp on 50 samples (which is well inside
noise). With Dendra's gate, only the ~1.5 candidates that
actually beat the rule at p < 0.05 get the recommendation,
and you read a clear "promote / hold" signal on each in the
morning.

**Scenario B — multi-tenant SaaS where each customer has a
custom rule.** You're shipping rule refinements per-tenant.
Without gating, every "looks better" change goes live and you
discover the bad ones via support tickets. With gating, the
recommendations queue is small enough to review by hand, and
the false-promotion floor is 5% — survivable.

**Scenario C (industrial) — card-fraud rule refinement at an
issuing bank.** A bank's fraud team tests new rules monthly
against live authorization traffic. Without statistical
gating, a rule that looks "1.5pp better" on this month's
sample ships — and false-decline rates spike on real customers
in the rollout cohort. False declines are a $1B+ cost center
at top issuers in their own right [^aitellis]; the bank's
fraud-prevention team won't approve any new rule that doesn't
clear a head-to-head significance bar. ``CandidateHarness``
gives them that bar with a documented audit trail per
proposed rule.

[^aitellis]: Aite-Novarica Group, "False Declines in Card-Not-Present" — the consensus industry estimate of false-decline cost dwarfs fraud losses themselves.

**Scenario D (industrial) — SOC detection-rule tuning.** A
SOC team at a Fortune 500 trials 5–10 detection-rule
refinements per week against historical alert traffic.
Promoting noise-level wins floods the on-call queue with
extra alerts; alert fatigue is the documented root cause of
missed-breach incidents (IBM 2024 puts the average breach at
$4.45M [^ibm]). Statistical gating bounds how often the team
ships a "looks slightly better" rule that actually inflates
noise.

### Where it doesn't matter

- One-shot offline experiments where you can re-run the
  comparison freely.
- Workloads where the candidate is dramatically better than
  the rule (massive label expansion, e.g. rule covers 5
  intents and ML covers 64 — you'll clear any reasonable
  alpha trivially).

## Faster convergence: head-to-head vs independent-samples

> *Example 19 claim: head-to-head testing reaches confident
> promotion (p < 0.01) by ≤ 250 outcomes on every shipped
> benchmark; independent-samples testing on the same data
> needs 500–1,500.*

Status: **Demonstrated** on four NLU benchmarks (ATIS,
HWU64, Banking77, CLINC150) shipped with this repo. Raw
numbers in `docs/benchmarks/` and the paper's results dir.

### Where it pays off

**Scenario A — slow verdict streams.** Your verdicts come
from human reviewers (50/day) or a delayed downstream signal
(72-hour lag on subscription cancellation). Independent-
samples testing wants 500–1,500 verdicts before it can
promote. Head-to-head testing on the same traffic wants
≤ 250. That's the difference between "graduate in 5 days" and
"graduate in 30 days" on the same workload.

**Scenario B — high-stakes/low-volume decisions.** Insurance
claim triage at 200 decisions/day; legal-doc routing at 100
decisions/day. You can't easily 10× your sample size. Head-
to-head testing makes the data you do have go further.

**Scenario C (industrial) — AML transaction-monitoring rule
tuning at a top-10 bank.** Decision volume is high (1M+ alerts/
day) but verdict volume is low: a compliance team labels on the
order of 1k alerts/week as true-positive vs false-positive, and
each labeled alert is expensive (analyst hours, legal review).
Independent-samples testing on that corpus needs 5–10× the
labeled volume the bank can produce in a quarter; head-to-head
testing closes the gap on the same labels. Cumulative AML fines
across the industry have exceeded $36B since 2008 [^fenergo],
so faster, statistically defensible rule iteration has direct
regulatory value.

**Scenario D (industrial) — payer prior-authorization rule
updates.** A health payer ships rule updates quarterly across a
catalog of thousands of CPT/HCPCS codes. Reviewer-verdicts come
from clinical staff and arrive slowly. Faster head-to-head
convergence means a quarter's rule update graduates on this
quarter's data, not on data half-a-year stale — material for
audit-bound HIPAA/CMS workflows where the rule's training
window is itself a compliance artifact.

### Where it doesn't matter

- High-volume + fast-verdict workloads (1M req/day with
  immediate feedback). Both tests clear in under an hour;
  the 1.7–6× speedup is invisible.

## Time to first verdict (autonomous-verification default)

> *Example 20 claim: with `verifier=default_verifier()`,
> verdict latency is < 1 s per classify. Without it, it's
> the slowest signal you have — reviewer queue (hours-days)
> or downstream label aggregator (days-weeks).*

Status: **Demonstrated for shipped default**
(`docs/benchmarks/slm-verifier-results.md`). Reviewer-queue
and downstream-aggregator numbers are typical industry
ranges.

### Where it pays off

**Scenario A — pre-launch products with no labeled data.**
You're building a triage classifier for a new product line.
There's no historical labeled data, no validation set. With
the verifier default, the moment a real ticket arrives, a
verdict lands. Your switch starts accumulating evidence on
day one. Without it, you're either (a) waiting weeks for a
reviewer queue to seed the log, or (b) shipping
naively without evidence.

**Scenario B — workloads where the downstream signal lags
days behind the decision.** Refund-routing where "did this
trigger a chargeback?" takes 30 days. Lead-scoring where
"did this lead convert?" takes 90 days. Without the
verifier, your gate evaluates on month-old decisions; with
it, the gate evaluates today's traffic in seconds.

**Scenario C (industrial) — returns-fraud triage rollout at a
large e-commerce platform.** US returns fraud was estimated at
$101B in 2023 [^nrf]; large retailers process 100k+ returns/
day on which a triage classifier (refund / hold / investigate)
needs evidence to graduate. Without an autonomous verifier, the
classifier waits weeks for the chargeback / restocking signal
to arrive before the gate fires. With ``verifier=`` running on
a local SLM at decision time, the gate sees evidence on day 1
and the rule-to-ML graduation completes in days, not quarters.
Each week of delay = continued fraud bleed-through at the
existing rule's accuracy floor.

**Scenario D (industrial) — clinical decision-support
deployment in regulated environments.** FDA Software-as-a-
Medical-Device (SaMD) frameworks require an evidence trail per
classification before any model is allowed to graduate from
"shadow advisory" to "primary recommendation". Without a
verifier, that evidence trail is operator labor (a clinician
reviewing every output). With a verifier (typically a
distinct-model judge or a held-out reference model — see the
self-judgment section below), the evidence trail accumulates
automatically and is auditable.

### Where it doesn't matter

- Workloads with a fast, accurate downstream signal already
  wired up (ad-click "did the user click?" within seconds).
  In those cases the downstream signal is your verifier;
  `default_verifier()` is redundant.

## Cycles to `ML_PRIMARY` at typical verdict rates

> *Example 20 claim: at 1k req/day with `verifier=default`,
> reaching ML_PRIMARY takes ~1.5–6 days; at 5% reviewer-
> queue verdict-rate, the same path takes 30+ days.*

Status: **Reasoned** — the cycle math comes from the gate's
shipped defaults (`auto_advance_interval=500` records;
`McNemarGate.min_paired=200`; three gated transitions on the
path to `ML_PRIMARY`). Specific calendar-time numbers depend
on the candidate's true accuracy lift and traffic patterns.

### Where it pays off

**Scenario A — small-team production rollouts.** You're a
3-person team shipping an internal-tools classifier. You
don't have a labeling budget. Reviewer-queue verdict-rates
of 5–10% are realistic. Without `verifier=`, your switch
takes 30+ days to graduate; with it, ~5 days. That's the
difference between "we'll see if it works" and "we shipped
the upgrade this sprint."

**Scenario B — multi-product rollout where each product
needs its own switch.** You can't manually seed 50 reviewer
queues. The verifier default makes each switch self-
graduating. Time-to-graduation per product collapses from
weeks to days.

**Scenario C (industrial) — insurance underwriting rule
modernization.** A carrier's UW rule covers ~40 product
variants and routes ~5M decisions/yr. The modernization plan
is "ML_PRIMARY in 90 days." At typical 5% reviewer-verdict
rates, the math says 180+ days — slipping the modernization
into the next fiscal year. The verifier default cuts the
calendar to ~30 days. Each quarter of delay is a quarter of
continued mispricing on a $300B+ industry claims book [^iii].

[^iii]: Insurance Information Institute, total US P&C claims paid annually.

**Scenario D (industrial) — security-classifier rollout in
new SaaS tenants.** A multi-tenant security product (phishing,
DLP, insider-risk) needs each new tenant's classifier to
graduate quickly. Without a verifier, the tenant's security
team owns labeling — the rollout stalls behind their headcount.
With a verifier, week-1 graduation per tenant; the product's
"time to demonstrated value" compresses by an order of
magnitude, which is the metric that closes the renewal.

### Where it doesn't matter

- Backfill-heavy workloads where you can preload thousands
  of historical labeled records via `bulk_record_verdicts`
  on day zero (see example 10). Cold-start preload swamps
  the per-request graduation rate.

## Self-judgment bias guardrail

> *Example 11 claim: when the same language model grades its own
> classifications, perceived accuracy inflates by 5–15
> percentage points relative to a held-out reference judge.
> The `require_distinct_from=` guardrail catches this at
> construction time.*

Status: **Cited literature.** G-Eval (Liu et al., 2023),
MT-Bench (Zheng et al., 2023), Chatbot Arena bias studies.
Range varies by task; 5–15 pp is the published spread.

### Where it pays off

**Scenario A — single-vendor language model stack.** You use OpenAI for
everything. Without the guardrail, it's natural to point
both classifier and judge at `gpt-4o-mini`, save a billing
line, and ship. With the guardrail, construction fails until
you wire a distinct judge (e.g. Anthropic Haiku, or a local
SLM via Ollama). Without that catch, your gate evaluates on
inflated accuracy and graduates a model that's worse than
the rule on the held-out distribution.

**Scenario B — A/B tests of competing prompts on the same
language model.** You're testing prompt v1 vs v2 on `gpt-4o-mini` and
using `gpt-4o-mini` as judge for both. Same-language model bias
flatters whichever prompt the judge's training distribution
prefers. Distinct-language model judging (or human-in-the-loop) gives
you a clean comparison.

**Scenario C (industrial) — clinical decision-support
audit.** A hospital uses a language model both to suggest a triage
disposition and to score whether the suggestion was sound
(post-hoc). The literature's documented 5–15pp self-judgment
inflation translates here into safety claims that don't survive
external audit. Regulators (FDA SaMD, EU MDR) increasingly
require independent evaluation; the guardrail enforces it
in code rather than in a policy document that gets ignored
under deadline pressure.

**Scenario D (industrial) — financial-advice / robo-advisor
output review.** Robo-advice and chat-based financial guidance
are subject to FINRA suitability review. A single-language model stack
that grades its own outputs would inflate "suitability"
ratings; using a distinct language model (or a committee, see
``JudgeCommittee``) keeps the review credible to a FINRA
auditor. Falling on the wrong side of suitability review
draws fines that scale with AUM.

### Where it doesn't matter

- Workloads where the verdict source is ground truth (a
  labeled validation set, a downstream behavioral signal).
  The guard is for LLM-as-judge specifically.

## Accuracy lift at `ML_PRIMARY` end-state

> *Example 06 claim: a trained ML head replacing a hand-
> written rule lifts accuracy by +18.7 pp (ATIS), +81.8 pp
> (HWU64), +86.5 pp (Banking77), +76.1 pp (CLINC150) at
> final training depth.*

Status: **Demonstrated** on four NLU benchmarks. Numbers in
the paper's results dir and `docs/benchmarks/`.

### Where it pays off

The bigger the label space, the bigger the rule-vs-ML gap:

| Labels in your problem | Rule-vs-ML lift to expect |
|---|---|
| ≤ 10 | small (rule can plausibly compete) |
| 10–30 | moderate (~10–20 pp typical) |
| 30–100 | large (~50–80 pp on our benchmarks) |
| 100+ | rule was never a viable baseline; lift is the rule's gap to literacy |

**Scenario A — intent routing with growing intent catalog.**
Your day-zero rule covers 10 intents. Product expands to 50.
Hand-coding 50 intents is ergonomically hostile and the
keyword overlap explodes. ML head trained on the outcome
log handles all 50 with measurable accuracy.

**Scenario B — support-ticket triage as the catalog grows.**
6 ticket categories on day zero, 30 by month 12. Rule
quality degrades as new categories shrink the keyword
distinctiveness; learned policy keeps up.

**Scenario C (industrial) — adtech brand-safety classifier
across IAB taxonomy.** RTB processes 100B+ auctions/day
[^iab]. The IAB Content Taxonomy has 1,000+ category nodes;
hand-written rules cover the obvious top-level categories
while ML reaches into the long tail. Each percentage point of
brand-safety accuracy moves real ad revenue (advertisers pay a
premium for narrow targeting; misclassification triggers
opt-out). At adtech volumes, the lift implied by Dendra's
benchmark numbers (rule accuracy ~1–2% at 77 labels;
ML accuracy ~88%) is a direct revenue lever.

**Scenario D (industrial) — customs HTS code classification.**
US Customs imports declare ~30M lines/yr [^cbp] across the
17,000+ Harmonized Tariff Schedule codes. A rule covering the
top 100 codes is industry-standard; learned policies on the
long tail are how brokers stay competitive. Mis-classification
penalties under 19 USC §1592 are material and the audit chain
matters — Dendra's outcome log makes the audit chain trivial.

### Where it doesn't matter

- 2-class problems with crisp keyword separation (spam/ham
  on a known corpus). Rule wins, ML at best matches.

## Failure cost: rule floor vs naive ML deployment

> *Example 06 claim: a naive ML deployment without a rule
> fallback ships the model's worst day to your users on
> every model-server hiccup. With ML_PRIMARY's circuit
> breaker, an ML failure routes back to the rule's accuracy
> floor.*

Status: **Reasoned scenario.** The circuit-breaker mechanism
is tested in this repo; the cost of *not* having it is
typical production-ML lore (Sculley et al. 2015 "Hidden
Technical Debt", and any "model server outage" postmortem).

### Where it pays off

**Scenario A — model-server dependency you don't control.**
Your ML head is hosted on Vertex / SageMaker / Replicate.
Their availability is 99.9% — i.e. ~9 hours of downtime per
year. Without the rule floor, those 9 hours are 500s served
to your users. With the rule floor and breaker,
those hours route back to the day-zero rule — your users
see classifications, just at the rule's accuracy.

**Scenario B — model-version regressions.** You ship ML
v2.3, latency-pinned tests pass, real traffic hits, accuracy
craters because the training distribution drifted. Without
the breaker, you ship the regression. With the breaker, an
elevated error rate trips it back to the rule floor while
you investigate.

**Scenario C (industrial) — payments authorization at
issuing bank.** A bank running ~1M card auths/day at the ML
head is exposed to model-server availability. A 99.9% SLA on
the model server is ~14 min/day of downtime. Without a rule
floor, those 14 minutes are declines for every cardholder —
public outage, customer complaints to the regulator, lost
interchange revenue. With the rule floor + breaker, those 14
minutes route to the static auth rule's accuracy floor;
cardholders see decisions, not 500s. The pattern recurs at any
issuer running an ML-enhanced auth path; the rule floor is the
incident-response answer to "what happens when the ML stack
goes down."

**Scenario D (industrial) — cloud security policy enforcement
at hyperscaler scale.** AWS / Azure / GCP run policy-evaluation
classifiers on the order of 100M+ policy-decisions/day. An ML
model-server outage without a rule floor is policy-bypass for
the duration — a catastrophic compliance event; the rule floor
turns "complete bypass" into "fall back to the conservative
hand-written policy" while the on-call rolls back the model.

### Where it doesn't matter

- Pure offline batch workloads where you can re-run failed
  predictions overnight. Online-serving constraints are
  what make the rule floor load-bearing.

## How to write a measurable claim for your own example

If you're contributing an example, the structure that lands:

1. Lead with the **specific number** (a percentage, a
   speedup factor, a calendar-time delta).
2. Cite the **source** — paper §, benchmark file path,
   or "industry-typical" with a citation.
3. Point to **this doc** with the section header so readers
   can see the scenario where the number actually plays out.

Don't promise numbers we haven't measured. Don't extrapolate
benchmark numbers to workloads they weren't measured on.
Bound the claim to its evidence and let the scenario
describe the situation where it generalizes.
