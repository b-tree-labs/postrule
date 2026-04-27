# When Should a Rule Learn? Transition Curves for Safe Rule-to-ML Graduation

**Author.** Benjamin Booth, B-Tree Ventures LLC.
**Status.** Draft v0.1 — 2026-04-26.
**Target.** arXiv (cs.LG / cs.SE), 2026-05-13.
**Code + data.** `https://github.com/axiom-labs-os/dendra`. Apache 2.0.

---

## Abstract

Production classification systems overwhelmingly start as hand-written rules — an `if "crash" in title: return "bug"` for ticket triage, a lookup table for intent routing, a threshold over a heuristic score for content moderation — because training data does not yet exist on day one. Over time, outcome data accumulates, yet the rules stay frozen: replacing a rule with a machine-learned classifier requires custom migration engineering at every decision point, and there is no shared statistical criterion for when the migration is justified. We formalize this migration as a *graduated-autonomy lifecycle* with six phases (`RULE` → `MODEL_SHADOW` → `MODEL_PRIMARY` → `ML_SHADOW` → `ML_WITH_FALLBACK` → `ML_PRIMARY`) in which the rule remains a structural safety floor at every stage. We prove that, when transitions are gated by a paired McNemar test at significance level $\alpha$, the marginal probability of a worse-than-rule transition is bounded above by $\alpha$ per gated step. We measure how many recorded outcomes a learned classifier needs before it convincingly beats the rule (paired McNemar $p < 0.01$). We call this curve the *transition curve* and report it on four public intent-classification benchmarks (ATIS, HWU64, Banking77, CLINC150). Two regimes emerge cleanly: a low-cardinality narrow-domain regime where rules give a usable baseline that ML eventually beats by ~19 points, and a high-cardinality broad-domain regime where rules are non-viable from day one (0.5–1.8% accuracy) and the entire ML migration is gated by outcome-data accumulation rather than statistical significance. Every benchmark crosses paired McNemar significance at the *first* checkpoint of 250 outcomes. We release a reference implementation (the Dendra library), the full transition-curve dataset, and the benchmark harness that reproduces the result. The gate primitive itself is direction-agnostic: the same paired-test machinery that justifies advancement also justifies demotion when accumulated evidence shows the rule has reclaimed the lead, and extends naturally to additional safety axes (latency, cost, coverage) under a union-bound joint guarantee. We discuss this generalization in §10.

**Keywords.** classification, graduated autonomy, rule-to-ML migration, paired McNemar test, production ML, LLM cascading, statistical gating, MLOps.

---

## 1. Introduction

### 1.1 The pattern nobody has formalized

Open any production codebase. There are functions that take an input and return one of a fixed set of labels: `triage_ticket(t) → {bug, feature_request, question}`; `route_intent(u) → one of 77 banking labels`; `classify_content(c) → {safe, review, block}`; `pick_retrieval_strategy(q) → {dense, sparse, hybrid}`. On day zero these functions are written as hand-rules — `if "crash" in title: return "bug"` — because no training data exists yet, the engineer has 100 examples to look at, and the deadline is now. The decision quality is whatever the rule's keyword coverage admits.

By day $N$ outcome data has accumulated. CSAT surveys, resolution codes, downstream audit trails, user corrections, A/B test conversion data — production systems generate a stream of evidence that lets the engineer compute, for each historical classification, whether the rule was right. A learned classifier trained on that evidence would, in principle, do better.

In practice the rule still runs. Replacing it requires its own engineering effort: outcome plumbing, feature pipelines, training, deployment, shadow evaluation, monitoring, rollback. Each classification site is migrated independently; the migration logic is rewritten each time; the *statistical question* — has the candidate accumulated enough evidence to justify replacing the floor? — gets answered by intuition. We have no shared primitive that captures the pattern.

### 1.2 Why this matters

Three failure modes follow:

1. **Rules calcify.** Distribution shifts that would be obvious to a learned model surface only as gradually degrading accuracy. Sculley et al. (2015) catalog this as *boundary erosion* — one of the load-bearing technical-debt categories of production ML. Paleyes et al. (2022) compile case studies in which calcified rules were the single largest source of silent regression in the post-deployment lifecycle.

2. **ML-from-day-one fails.** Without sufficient training data, learned models produce arbitrary outputs on inputs the training set did not anticipate. The cold-start problem is not solved by AutoML (Hutter et al., 2019), which presupposes labeled data; nor by online learning (Bottou, 1998; Langford et al., 2007), which updates parameters continuously without a structural safety floor.

3. **Ad-hoc migration has no contract.** Most production `try_ml_else_fall_back_to_rule` code embeds an implicit confidence threshold and an implicit assumption that ML is at least as good as the rule. Both go untested. Breck et al. (2017) provide a rubric — the ML Test Score — for production-readiness; statistical-evidence-before-promotion is a row on the rubric, but no widely-used library implements it.

What is missing is a *primitive* that captures the rule-to-ML migration uniformly, with safety guarantees, so every classification site graduates by the same contract.

### 1.3 Contribution

We make four contributions:

1. **A six-phase graduated-autonomy lifecycle** (Table 1) with formal transition criteria. The rule is the structural safety floor at every phase; the lifecycle is closed under failure recovery.

2. **A safety theorem** (Theorem 1, §3) bounding the per-transition probability of worse-than-rule behavior by the Type-I error of a paired McNemar test on the gated transition. The paired test is the methodologically correct choice when two classifiers are evaluated on the same row stream (Dietterich, 1998), and the bound is tighter than the unpaired alternative.

3. **Empirical transition curves on four public benchmarks** (ATIS, HWU64, Banking77, CLINC150). Two regimes emerge: the narrow-domain regime (ATIS) where rule coverage saturates at ~70% and ML wins by ~19 points; and the high-cardinality regime where rules are functionally unusable (0.5–1.8%) and the migration is gated entirely by outcome accumulation. Under the paired McNemar test at $\alpha = 0.01$, every benchmark crosses significance at 250 outcomes — an order of magnitude tighter than the unpaired-z-test depths in earlier exploratory runs.

4. **An open-source reference implementation** — the Dendra Python library — with the transition-curve dataset, the benchmark harness, and signed Apache 2.0 licensing on the client SDK so practitioners can adopt the primitive in commercial workloads.

The conceptual contribution is the framing — *graduated autonomy* as the natural primitive for the rule-to-ML transition — and the empirical contribution is the demonstration that the McNemar gate is both methodologically clean and tight enough at small outcome volumes that production teams can use it to make the call.

A note on scope. This paper presents the primitive on a single safety axis (decision accuracy), with the rule serving as the reference baseline that the gate compares against. The same gate primitive is direction-agnostic by construction (the test asks "is the comparison target reliably better than the current decision-maker?", not "should we advance?"), so it generalizes both to the demotion direction (drift detection on the same axis) and to additional axes (latency, cost, coverage) where a paired test admits a Type-I error bound. The single-axis story we present here is the v1 deliverable; we sketch the n-axis generalization and its joint safety bound in §10.

---

## 2. Related Work

We position the contribution against six adjacent literatures. The taxonomy follows §A–H of the project's annotated bibliography.

**LLM cascade routing.** The closest contemporary lineage is the LLM cascade. *FrugalGPT* (Chen, Zaharia, & Zou, 2024) introduced the *weakest-model-first, escalate-on-low-confidence* pattern that reduces cost while preserving quality on benchmark suites. *RouteLLM* (Ong et al., 2024) extended cascading to *learned* routing from preference data, recovering 95% of GPT-4 quality at 15% of the cost on MT-Bench. *A Unified Approach to Routing and Cascading for LLMs* (Dekoninck et al., 2025) provided a theoretical unification, framing both as instances of a meta-classifier over a model pool with formal optimality conditions. Our work generalizes the cost-quality tradeoff into a *production-deployment lifecycle*: where the cascade literature optimizes inference-time routing among already-trained models, we add the rule floor as a structural prior and the migration over time as the unit of analysis. The McNemar gate (§3) is the statistical analog of the routing literature's preference-trained selectors — both decide *use the alternative when it's good enough* — but anchored in a paired test rather than a learned router.

**Statistical methodology — paired tests for ML.** The McNemar test (McNemar, 1947) compares correlated proportions and was canonized as the recommended statistical tool for paired classifier comparison by Dietterich (1998), whose treatment remains the standard reference for evaluating two classifiers on the same test set. Demšar (2006) extends the framework to multi-dataset comparison; Bouckaert (2003) discusses calibration concerns. The *paired* version is essential when two classifiers are scored on the same input stream — the unpaired two-proportion z-test discards per-example correlation and is conservative by 3–5× in our experiments (§5.4). Recent extensions (e.g., Block-regularized 5×2 cross-validated McNemar, 2023) further refine the methodology for cross-validated evaluation. We use the standard paired McNemar test on a held-out test split, which matches the single-evaluation case Dietterich identifies as the right scope for the original test.

**Production ML safety.** Sculley et al. (2015) document the categories of hidden technical debt that production ML systems incur — boundary erosion, hidden feedback loops, configuration drift, glue code. Breck et al. (2017) provide the *ML Test Score*, a 28-item rubric for production readiness. Polyzotis et al. (2018) survey data-lifecycle challenges. Paleyes et al. (2022) compile a case-study survey of real ML deployment failures; their *graduated trust* framing is conceptually adjacent to our six-phase lifecycle. Amodei et al. (2016) frame the problem from the AI-safety direction with their *Concrete Problems in AI Safety* taxonomy; the safety-floor-by-construction property of our lifecycle implements their *robust fallback* desideratum at the classification-primitive level. None of these works provide a *primitive* — they provide the diagnostic vocabulary that motivates one.

**LLM-as-judge and evaluation harnesses.** The verdict-source layer of our reference implementation (§9) supports an LLM-as-judge pattern that draws on Liu et al. (2023, *G-Eval*) and Zheng et al. (2023, MT-Bench, *Judging LLM-as-a-Judge*). The same-LLM-as-classifier-and-judge bias they document motivates our `JudgeSource` self-judgment guardrail. Chiang et al. (2024) provide the *Chatbot Arena* preference data that is the substrate for downstream learned routers like RouteLLM. Recent practitioner work has emphasized the *harness* as a first-class object: Trivedy (2026) describes "harness hill-climbing" as the iterative discipline of evaluating evaluation-harnesses themselves, and Martin's *Auto-Evaluator* tooling at LangChain (2023) was an early production realization of LLM-as-judge for retrieval-quality measurement. Our `CandidateHarness` (§9.4) is in this lineage — a substrate for autoresearch loops that propose, evaluate, and either promote or discard candidate classifiers under a statistical gate.

**AutoML and online learning — for differentiation.** AutoML (Hutter et al., 2019; Feurer et al., 2015) addresses *offline* model selection given a fixed labeled training set. It does not address the cold-start case where labels accumulate post-deployment, nor the safety-floor preservation that production migration requires. Online learning (Bottou, 1998; Langford et al., 2007) updates model parameters continuously over a stream of labeled examples; Vowpal Wabbit is the canonical production system. Our differentiation is on two axes: (1) we graduate *phases discretely* with a statistical gate, rather than updating parameters continuously; and (2) the rule floor is preserved structurally, whereas online learning replaces the floor with the learner. We do not compete with these techniques — VW remains the right choice when a stable feature pipeline already exists and the question is purely "track distribution shift." We address the earlier-stage question: when does the migration begin?

**Cascade learning — historical lineage.** The cascade pattern predates LLMs by 25 years. Viola and Jones (2001) introduced the boosted cascade of simple features for face detection — *use a cheap classifier first, escalate on uncertainty* — establishing the architectural pattern that FrugalGPT and RouteLLM operationalize at the LLM scale. Trapeznikov and Saligrama (2013) provide a sequential-classification-under-budget formulation that is conceptually closer to our phase-transition framing. Citing this lineage emphasizes that our contribution is not the cascade architecture per se but the production-lifecycle generalization with a rule floor and statistical gating.

**Agent / autoresearch loops.** Recent work has popularized the *autoresearch* pattern — agentic loops in which an LLM proposes candidate hypotheses, evaluates them against a test corpus, and refines (Wang et al., 2023, *Voyager*; Shinn et al., 2023, *Reflexion*; Wei et al., 2022, *Chain-of-Thought*; Karpathy, 2025, on the autoresearch loop). These works focus on the agent's reasoning trajectory rather than the production-deployment substrate. Our `CandidateHarness` is the production substrate underneath: it accepts a stream of agent-proposed candidates, runs them in shadow mode against live production traffic, and either promotes via the McNemar gate or discards. The substrate is the unsexy plumbing that makes autoresearch loops production-safe at classification primitives.

**Calibration.** Guo et al. (2017) document that modern neural networks are systematically miscalibrated, motivating the explicit `confidence_threshold` parameter in our adapter layer. Kuleshov et al. (2018) extend calibration analysis to deep regression. Calibration is a load-bearing assumption when the cascade pattern routes by confidence; we do not contribute to this literature, but we cite it because the system depends on it being well-handled at the adapter boundary.

**Drift detection.** Lu et al. (2018) and Gama et al. (2014) survey concept-drift adaptation. Drift detection is *complementary* to graduation: graduation answers when to first migrate; drift detection answers when the migrated classifier has degraded enough relative to the rule that a partial retreat in the lifecycle is justified. The same paired-test machinery we use for advancement reverses cleanly for demotion (current decision-maker compared against the rule), and the v1 reference implementation ships an autonomous demotion path on the accuracy axis driven by the same Gate primitive defined in §3. Each fired demotion steps the lifecycle back by one phase, not all the way to the rule; multi-step retreats accumulate across successive cycles if drift persists. Empirical characterization of the demotion-curve analog of the transition curves we report (how much drift accumulates before the gate fires) is follow-on research; here we establish that the primitive is direction-agnostic and the safety theorem (§3.3, Remark) applies symmetrically.

---

## 3. Formal Framework

### 3.1 The graduated-autonomy lifecycle

A learned switch is one object that holds three optional decision-makers (the rule, an LLM-style model, and a trained ML head) plus a phase counter that tracks which decision-maker is currently routing classifications. The phase counter steps forward, or back, as the gates introduced in §3.2 and §3.3 fire on accumulated outcome evidence; the routing logic at each phase is shown in Table 1 below.

Formally, we define a *learned switch* over a label set $\mathcal{L}$ as a tuple $S = (R, M, H, \phi)$ where $R: \mathcal{X} \to \mathcal{L}$ is the rule, $M: \mathcal{X} \to (\mathcal{L}, [0,1])$ is an optional model classifier returning a label and confidence, $H: \mathcal{X} \to (\mathcal{L}, [0,1])$ is an optional ML head, and $\phi \in \{P_0, \ldots, P_5\}$ is the lifecycle phase. The decision function is:

| Phase | Decision rule | Rule role |
|---|---|---|
| $P_0$ — `RULE` | $R(x)$ | self |
| $P_1$ — `MODEL_SHADOW` | $R(x)$; $M$ logged but inert | floor |
| $P_2$ — `MODEL_PRIMARY` | $M(x)$ if $\text{conf}_M \ge \theta$; else $R(x)$ | fallback |
| $P_3$ — `ML_SHADOW` | $M(x)$ or $R(x)$; $H$ logged but inert | floor |
| $P_4$ — `ML_WITH_FALLBACK` | $H(x)$ if $\text{conf}_H \ge \theta$; else $R(x)$ | fallback |
| $P_5$ — `ML_PRIMARY` | $H(x)$ | circuit-breaker only |

**Table 1.** *The six-phase graduated-autonomy lifecycle.* Three transitions ($P_2 \leftarrow P_1$, $P_4 \leftarrow P_3$, $P_5 \leftarrow P_4$) are statistically gated; two ($P_1 \leftarrow P_0$, $P_3 \leftarrow P_2$) are operator- or construction-driven (shadow modes are no-risk additions of an inert candidate). The rule $R$ is structurally preserved in every phase except $P_5$, where it remains as the circuit-breaker target on detected ML failure. A `safety_critical=True` configuration caps the lifecycle at $P_4$, refusing $P_5$ at construction time.

### 3.2 Transition guards

Each gated transition $P_k \to P_{k+1}$ admits a guard $G_k(\mathcal{D})$ that returns `advance | hold` over an outcome dataset $\mathcal{D}$. The default guard is the paired McNemar test, which counts only the rows where the two classifiers disagree and asks whether the disagreements are lopsided enough toward the candidate to be unlikely by chance. Three knobs control how strict the answer must be: the significance level $\alpha$ caps the chance of a wrong promotion at the rate the operator picks (we default to 1%); the minimum-pair threshold $n_{\min}$ keeps the gate from firing on too few disagreements to be reliable; and the directional condition rules out ties, so the gate stays put if the two classifiers are equally good. Together these three turn "should we replace this rule" from an intuitive call into a reproducible, auditable decision.

**Definition (paired McNemar gate).** Given two classifiers $A$ (incumbent) and $B$ (candidate) evaluated on the same $n$ rows with paired correctness $(a_i, b_i) \in \{0,1\}^2$, let:
- $b = |\{i : a_i = 0, b_i = 1\}|$  (rows where $B$ is right and $A$ is wrong)
- $c = |\{i : a_i = 1, b_i = 0\}|$  (rows where $A$ is right and $B$ is wrong)

The paired McNemar test rejects "$A$ and $B$ have equal accuracy" at significance level $\alpha$ when the two-sided exact binomial $p$-value of $\min(b, c)$ under the null $\text{Binomial}(b + c, 0.5)$ is below $\alpha$. The gate $G(\mathcal{D}) = $ `advance` iff $b > c$ and $p < \alpha$ and $b + c \ge n_{\min}$.

The minimum-pair condition $n_{\min}$ (we use $n_{\min} = 200$) prevents a runaway low-volume rejection. The directional condition $b > c$ ensures the test only advances when the candidate is *the better* classifier, not merely when the two differ.

### 3.3 Safety theorem

The result of this section is a pair of calibrated safety guarantees. If a candidate classifier is genuinely no better than the one it would replace, the gate has at most a 1% chance of wrongly promoting it on any single evaluation at $\alpha = 0.01$, by construction of the paired McNemar test's null distribution. Three such evaluations sit between the rule and full ML autonomy in our lifecycle; even in the worst case where every gate "rolls the dice" independently, the joint probability of any wrong promotion across the lifecycle stays under 3%, and the single most-consequential step (handing decisions fully to ML) stays at the per-evaluation 1%. That sits in the same range as the false-failure rate production teams already tolerate from CI regression tests, and the operator can drive it lower by tightening $\alpha$ at construction time.

**Theorem 1 (per-transition safety).** Let $G$ be a paired McNemar gate at significance $\alpha$ with minimum-pair threshold $n_{\min}$. Let $A$ be the incumbent classifier and $B$ the candidate, both evaluated on a stream of paired-correctness rows from the same input distribution $\mathcal{X}$. If $B$ has true accuracy on $\mathcal{X}$ no greater than $A$, then the probability that $G$ advances is bounded above by $\alpha$.

*Proof sketch.* The paired McNemar test's null hypothesis is "$P(B \text{ right} | A \text{ wrong}) = P(A \text{ right} | B \text{ wrong})$," equivalently "$E[b] = E[c]$" over the discordant-pair distribution. If $B$ is no better than $A$ in true accuracy on $\mathcal{X}$, the distribution of discordant pairs satisfies $E[b] \le E[c]$, so $\min(b, c) = b$ in expectation. The rejection rule rejects when the two-sided binomial $p$-value of $\min(b, c)$ falls below $\alpha$; under the null and the directional condition $b > c$, this happens with probability at most $\alpha / 2$. The marginal advance probability is therefore at most $\alpha$. $\blacksquare$

**Corollary (lifecycle safety).** With three statistically-gated transitions (as in Table 1), each guarded at $\alpha$, the per-switch worst-case probability of *any* worse-than-rule advance is bounded by $3\alpha$ (union bound), and exactly $\alpha$ for the single canonical transition the field most cares about ($P_4 \to P_5$). At $\alpha = 0.01$, this is a 1% per-step worst-case ceiling — the same order of magnitude as the conservative regression-test FPR teams already accept in production CI/CD.

**Remark (direction-agnosticism and n-axis generalization).** The theorem statement is symmetric in $A$ and $B$: the gate's question is "is the comparison target reliably better than the current decision-maker on the paired-correctness evidence?", with the directional interpretation (advance, demote, lateral move) supplied by the caller. Reusing the same gate with the rule passed as the comparison target produces a demotion test whose Type-I error is bounded by the same $\alpha$, giving a bidirectional safety guarantee on the accuracy axis. The same machinery applies to any axis where paired observations admit a hypothesis test (latency via Wilcoxon, cost via threshold, coverage via paired McNemar on a confident-answer indicator). Across $k$ axes evaluated independently per cycle, the joint per-cycle FPR is bounded by $k\alpha$ via union bound. We treat the multi-axis extension as a deliberate design affordance and discuss it in §10.

### 3.4 Why this gate, not an alternative

The natural alternatives are:

1. **Unpaired two-proportion z-test.** The version most often seen in practitioner writeups. It discards per-example correlation; in our experiments (§5.4) it requires 2–6× more outcomes to clear at the same $\alpha$. The extra wait is paid in calendar time on whichever side has the bigger production signal.
2. **Accuracy margin** ("ML beats rule by $\ge \delta$"). Simple, but has no Type-I-error interpretation; reviewers who care about formal claims will ask what $\delta$ corresponds to.
3. **Bayesian decision theory** (e.g., learned routers from preference data, in the lineage of RouteLLM). Powerful, but requires preference data the day-one user does not have. We instead provide the statistical-gate option as the default and let the practitioner swap in a learned router via the `Gate` protocol when their data supports it.
4. **Composite gates** (logical conjunctions, e.g., paired-McNemar AND minimum-volume AND accuracy-margin). Available as `CompositeGate` in the reference implementation. Useful when domain rules require an additional floor; we report the paired-McNemar single-gate result as the default.

The choice of the paired test follows the methodological convention of Dietterich (1998); the choice of $\alpha = 0.01$ rather than the conventional $0.05$ is a deliberate conservatism appropriate to *production* decisions, where Type-I error is a per-deployment cost rather than a per-experiment cost. Recent evaluation work in adjacent areas — Papailiopoulos's *ReJump* framework for LLM reasoning evaluation (2025) and Tzamos et al.'s theoretical-foundation work on classifier comparison — supports the more general claim that production-grade ML evaluation should use paired, statistically-grounded gates rather than ad-hoc thresholds.

---

## 4. Experimental Setup

### 4.1 Benchmarks

We evaluate on four public intent-classification datasets selected for diversity across label cardinality, domain breadth, and distributional structure:

| Dataset | Labels | Train | Test | Domain |
|---|---:|---:|---:|---|
| **ATIS** (Hemphill et al., 1990) | 26 | 4,978 | 893 | Single (flight booking) |
| **HWU64** (Liu et al., 2019) | 64 | 8,954 | 1,076 | Multi (21 scenarios) |
| **Banking77** (Casanueva et al., 2020) | 77 | 10,003 | 3,080 | Single (banking) |
| **CLINC150** (Larson et al., 2019) | 151 | 15,250 | 5,500 | Multi (10 + OOS) |

**Table 2.** *Benchmarks.* All are public, leaderboarded, and reproducible. CLINC150 includes an out-of-scope (OOS) class which is a known stress test for keyword rules.

### 4.2 Rule construction

The day-zero rule is automated and deliberately simple. For each dataset, `dendra.benchmarks.rules.build_reference_rule` constructs an `if/elif` cascade by:

1. Inspecting the first $k = 100$ training examples (paper default).
2. Computing the top-5 distinctive keywords per label by relative frequency (TF-IDF).
3. Generating an `if keyword in input: return label` cascade in lexical order.
4. Falling back to the modal label in the seed window (or `out_of_scope` for CLINC150).

This construction is reproducible and *deliberately not cleverly-tuned* — we want a lower bound on rule quality that approximates a real day-zero engineering effort. We test sensitivity to the seed size in §5.4.

### 4.3 ML head

The ML head is `dendra.ml.SklearnTextHead` — TF-IDF features with sublinear term-frequency, plus an L2-regularized logistic regression. Deliberately simple: a transformer would produce higher absolute accuracy but would conflate the *transition-curve shape* (the contribution) with the *ML ceiling* (well-studied elsewhere). The shape result is robust to ML choice; replication with a transformer is left to follow-on work.

### 4.4 Verdict simulation

Production systems accumulate verdicts (correct/incorrect outcomes) over time. We simulate this by streaming the training set through the switch:

1. Initialize at $P_0$ (rule).
2. For each training example, classify with the rule; record the prediction and the ground-truth label as a paired outcome.
3. Every 250 outcomes, retrain the ML head on the accumulated outcome log and evaluate both rule and ML on the held-out test split.
4. Apply the paired McNemar gate ($\alpha = 0.01$, $n_{\min} = 200$) to the test-set paired-correctness arrays.

This produces the *transition curve* — outcome volume against accuracy and against gate-statistic — for each benchmark.

### 4.5 Metrics

- **Final ML accuracy.** Test-set accuracy after the full training stream.
- **Transition depth.** The smallest checkpoint at which the paired McNemar gate advances at $p < 0.01$ — the headline metric.
- **Crossover delta.** ML accuracy minus rule accuracy at the transition point.
- **McNemar discordant pair counts** ($b$, $c$) at the final checkpoint — the underlying statistical evidence.

### 4.6 Reproducibility

All code is at `https://github.com/axiom-labs-os/dendra` (Apache 2.0). All fixed seeds are documented in the benchmark JSONL output. The transition-curve dataset is released as `dendra-transition-curves-2026.jsonl` accompanying this paper. The benchmark harness ships in `src/dendra/research.py::run_benchmark_experiment` and the McNemar computation in `src/dendra/gates.py::McNemarGate`.

---

## 5. Transition Curves — Main Results

### 5.1 Headline result

Table 3 reports the primary numbers from the paired-McNemar re-run (2026-04-24).

| Benchmark | Labels | Rule acc | ML final | $b$ | $c$ | McNemar $p$ (final) | Transition depth ($p < 0.01$) |
|---|---:|---:|---:|---:|---:|---:|---:|
| **ATIS** | 26 | 70.0% | **88.7%** | 191 | 24 | $1.8 \times 10^{-33}$ | **≤ 250** ($p = 1.6 \times 10^{-3}$) |
| **HWU64** | 64 | 1.8% | **83.6%** | 881 | 1 | $< 10^{-260}$ | **≤ 250** ($p = 2.0 \times 10^{-3}$) |
| **Banking77** | 77 | 1.3% | **87.7%** | 2,665 | 4 | $\approx 0$ | **≤ 250** ($p = 3.8 \times 10^{-11}$) |
| **CLINC150** | 151 | 0.5% | **81.9%** | 4,478 | 6 | $\approx 0$ | **≤ 250** ($p = 6.9 \times 10^{-18}$) |

**Table 3.** *Headline transition-curve results.* Every benchmark crosses paired McNemar significance at $\alpha = 0.01$ at the very first checkpoint (250 training outcomes). The discordant-pair counts ($b$ vs $c$) reveal the magnitude of the win — at CLINC150's final checkpoint, ML wins on 4,478 of 4,484 discordant rows.

The headline is striking: under the paired McNemar gate, the *minimum measurable* transition depth (250 outcomes, our checkpoint resolution) is sufficient on every benchmark. Earlier exploratory runs using the unpaired two-proportion z-test had reported looser depths of 500–1,500 outcomes (§5.4) — the gap is the methodology, not the data.

### 5.2 Two regimes

The benchmarks split cleanly into two regimes (Figure 1, transition-curve panels).

**Regime A — Low cardinality, narrow domain (ATIS).** With 26 labels and a single domain (flight booking), a 100-example keyword rule achieves 70.0% test-set accuracy from day one. The rule is *usable* — a triage system could ship with this baseline. ML crosses the rule at 250 outcomes (paired $p = 1.6 \times 10^{-3}$) with a 6-point margin (75.6% vs 69.5% on the seed=500 finer-grained run), and reaches 88.7% by training-set exhaustion. The interesting question for Regime A users is *when to graduate* — the rule is good enough that the team has the luxury of waiting.

**Regime B — High cardinality, broad domain (HWU64, Banking77, CLINC150).** With 64–151 labels, a 100-example seed cannot possibly cover the label space; the day-zero rule is essentially non-functional (0.5–1.8% accuracy, at or below chance for a 77+ label space). ML climbs from single digits at 1k outcomes to the low 80s at full training. The "transition depth" metric loses its narrative force here: the rule was never a viable baseline, and no team could have shipped a 1%-accurate keyword classifier as the user-visible decision in the first place. High-cardinality workloads in production start at Phase 2 with an off-the-shelf zero-shot LLM, or wait on hand-labeled training data before launching at all. Dendra's role in this regime is therefore not graduation but cold-start substrate: outcome-logging from day one regardless of which decision-maker is in front, with an explicit migration path to a trained ML head once enough data accumulates.

The two regimes correspond to different product conversations:

- **Regime A user:** "Our rule works; should we replace it?" → "Yes, here is the statistical evidence; the McNemar gate would advance you at 250 outcomes."
- **Regime B user:** "We need a 77-way intent classifier and we don't have training data yet." → "Start at Phase 2 with a zero-shot LLM in front of Dendra's outcome-logging layer; the log it generates is the training-data source for the eventual ML head."

### 5.3 Seed-size sensitivity

To check that Regime B is not an artifact of a stingy seed window, we re-ran every benchmark with a 10× seed (1,000 examples instead of 100):

| Benchmark | Labels | Rule (seed=100) | Rule (seed=1000) | $\Delta$ |
|---|---:|---:|---:|---:|
| ATIS | 26 | 70.0% | 72.3% | +2.3 pp |
| HWU64 | 64 | 1.8% | 5.9% | +4.1 pp |
| Banking77 | 77 | 1.3% | 6.8% | +5.5 pp |
| CLINC150 | 151 | 0.5% | 5.0% | +4.5 pp |

**Table 4.** *Rule sensitivity to seed size.* Even with 10× more examples to inspect, the high-cardinality rules remain 75–85 percentage points below the ML ceiling. *Label cardinality* — not engineer effort — is the dominant variable. This is the most load-bearing validation of the two-regime story.

### 5.4 Paired vs unpaired test

Under the unpaired two-proportion z-test on the same data:

| Benchmark | Unpaired transition depth | Paired transition depth | Tightening |
|---|---:|---:|---:|
| ATIS | 500 | 250 | 2× |
| HWU64 | 1,000 | 250 | 4× |
| Banking77 | 1,000 | 250 | 4× |
| CLINC150 | 1,500 | 250 | 6× |

**Table 5.** *Paired vs unpaired McNemar transition depth.* The paired test is uniformly tighter, as Dietterich (1998) predicts — the unpaired test discards per-example correlation and is conservative when the same test rows are scored by two classifiers. The paired result is both methodologically correct (it is the right test for the data structure we have) and operationally meaningful (production teams pay for the wait in calendar time at low verdict rates).

### 5.5 LLM-shadow capability curve

Phase 1 (`MODEL_SHADOW`) lets a language model run alongside the rule with zero risk: the model's prediction is logged but the rule still decides. Whether that model can subsequently graduate to Phase 2, where it becomes the primary decision-maker, depends on its zero-shot accuracy on the target task. We probed three locally-hosted models on ATIS (26 labels) and one on Banking77 (77 labels), 100-row samples, single default prompt.

| Model | Params | ATIS (rule = 70.0%) | Banking77 (rule = 1.3%) |
|---|---:|---:|---:|
| `llama3.2:1b` | 1B | 0.0% | n/a |
| `gemma2:2b` | 2B | 42.0% | n/a |
| `qwen2.5:7b` | 7B | **59.0%** | **52.0%** |

**Table 6.** *Zero-shot Phase-1 accuracy across model size and benchmark cardinality.* 100-row test sample per cell, default prompt, locally-hosted via Ollama.

The picture is regime-dependent.

**Regime A (ATIS, 26 labels, rule = 70%).** No model in our local-hosted bench beats the rule zero-shot. qwen2.5:7b's 59% is the closest, an 11-point gap. The Phase 1 → Phase 2 transition on ATIS-shaped workloads requires a larger model (Llama-70B class), per-model prompt tuning, or a frontier API. The smallest probe (llama3.2:1b at 0.0%) is a useful negative result: a commodity 1B parameter model is not a viable zero-shot shadow labeler on a 26-way compound-label task.

**Regime B (Banking77, 77 labels, rule = 1.3%).** qwen2.5:7b's 52% dominates both the rule (1.3%) and the cold ML head (2.6% at the 250-outcome checkpoint). High-cardinality workloads can start at Phase 2 with a 7B-class local LLM and accumulate outcome data via Dendra's logging substrate while the trained ML head warms up. This is the empirical anchor for the §5.2 claim that Regime B is not a graduation problem but a cold-start substrate problem.

Two caveats. First, our probes use a single default prompt; per-model prompt tuning would shift the rankings. Second, 100 rows is a small sample, reported as the entry-level capability measurement rather than the final word. The reference implementation's adapter layer (`OllamaAdapter`, `AnthropicAdapter`, `OpenAIAdapter`, `LlamafileAdapter`) is wired and ready; extending the curve to Llama-70B / Mistral-Large / frontier APIs is one method call away.

---

## 6. Category Taxonomy — Predicting Transition Depth

If transition depth is predictable from dataset attributes, practitioners can estimate graduation timing *before* they deploy. Five attribute dimensions are available without training:

1. **Label cardinality** (count of distinct labels).
2. **Distribution stability** (KL divergence on a rolling window).
3. **Verdict latency** (seconds → days).
4. **Verdict quality** (direct human label > inferred outcome > heuristic proxy).
5. **Feature dimensionality** (low for hand-crafted features, high for embeddings).

A fitted regression on the four-benchmark sample is under-powered — four points cannot identify five coefficients — and we do not report one. What the four-benchmark sample *does* support is the qualitative claim that label cardinality is the dominant variable, with distribution stability as the moderator of $b/c$ growth rate. The taxonomy is operationally useful as heuristics:

- **Direct human labels + cardinality < 30 + stable distribution** → Regime A; expect transition by ~250 outcomes (matches ATIS).
- **Cardinality > 60 + multi-domain** → Regime B; rule is symbolic; transition depth is bounded below by *outcome accumulation rate*, not statistical power. The McNemar gate fires immediately on the discordant-pair counts.
- **High-stakes / regulated** → cap the lifecycle at Phase 4 (`safety_critical=True`); the rule remains the legal contract.

A larger benchmark suite (10–20 datasets) is required to identify the regression precisely. We release the harness to enable community contributions to that suite.

---

## 7. Safety and Governance

### 7.1 Safety-critical caps

For classification sites where the rule is a regulatory contract — content moderation under HIPAA-bound clinical decisions, authorization decisions, export-control labeling, identity-verification routing — the lifecycle caps at $P_4$ (`ML_WITH_FALLBACK`). The reference implementation refuses the $P_4 \to P_5$ transition at construction time when `safety_critical=True` is set, and the `Gate` protocol cannot be subverted by an operator without modifying source code. The construction-time refusal is a deliberate ergonomic choice: the operator who would otherwise reach for `force_advance(P_5)` at 3 AM under incident pressure cannot. The contract is enforced by the type system, not by discipline.

### 7.2 Approval backends and audit chain

Phase transitions emit signed *advance proposals* — content-addressed JSON artifacts containing the proposing gate's name, the McNemar statistics, the $b/c$ counts, the test set hash, the ML head version, and a UTC timestamp. The proposal is logged before the transition takes effect. An `ApprovalBackend` protocol allows the proposal to be reviewed by an external system (a manual queue, a conservative auto-approver, a strict policy engine) before the transition is committed.

The audit chain is append-only by convention (POSIX `flock` + atomic rotate; cryptographic tamper-evidence is left for v2). Every classification, every verdict, every advance proposal, every gate-decision call is in the log. This is the substrate on which compliance frameworks (HIPAA, SOC 2, the EU AI Act's high-risk classifier audit requirements) build their evidence packages.

### 7.3 Circuit breaker

When ML routing fails — exception, timeout, NaN confidence — the breaker trips and routing falls back to the rule until an operator resets it. Breaker state persists across process restart when durable storage is configured. This is the operational instantiation of Amodei et al.'s (2016) *robust fallback* desideratum: the system gracefully degrades to the safety floor under any classifier failure without operator intervention.

---

## 8. Limitations

We list the limitations that reviewers should weigh.

1. **Four benchmarks is not every category.** The taxonomy claim (§6) is qualitative; a regression-grade taxonomy needs 10–20 datasets. Image classification, structured-data routing, and content-moderation corpora are obvious extensions.

2. **Intent classification is one family.** The transition-curve shape generalizes if our hypothesis is correct, but we cannot prove generalization without testing — text classification with longer inputs, structured ranking, sequence labeling, document-level taxonomy assignment all need independent runs.

3. **Rule construction is automated.** A thoughtful day-zero engineer might build a stronger keyword rule than `build_reference_rule` produces. The seed-size sensitivity analysis (§5.3) shows this does not change the regime conclusion, but it does shift the rule baseline modestly. We publish the rules for replication and scrutiny.

4. **The ML head is deliberately simple.** TF-IDF + logistic regression is a 2010-era baseline. A transformer would raise the ML ceiling and tighten the McNemar $p$-value further. We held the ML choice fixed to keep the *shape* of the transition curve as the contribution; replication with a transformer is a clean follow-on.

5. **Verdict quality is treated as oracle.** In our experiments, verdicts come from the ground-truth label. In production, verdicts are inferred from downstream signals (resolution codes, CSAT, A/B conversion) or a slower reviewer queue. The reference implementation's `verifier=` slot supports an autonomous LLM-judge verdict source (and ships an evaluated default), but the noise model of inferred verdicts is a research direction in itself, complementary to this work.

6. **The McNemar gate's safety guarantee is per-step.** The lifecycle's union-bound corollary gives $3\alpha$ across the three gated transitions per switch. At fleet scale (thousands of switches), a per-switch FPR at $0.01$ is a calibration knob the operator should set explicitly; we do not provide a fleet-aggregated guarantee.

7. **No production case study (yet).** The four-benchmark result is a controlled-laboratory demonstration. A production case study — a real classification site with real outcome plumbing and real downstream signal — is the natural next paper. One is in progress at the time of submission; we will report when it lands.

---

## 9. The Dendra Reference Implementation

The reference implementation realizes the lifecycle as a Python library (`pip install dendra`). The design decisions worth surfacing:

### 9.1 The `@ml_switch` decorator

How do you wire classification, dispatch, and the eventual rule-to-ML migration through a single call site, so the same `triage_rule(ticket)` invocation is what production code calls on day one and on day thirty? The simplest invocation is a decorator over the rule function:

```python
from dendra import ml_switch
from myapp.queues import engineering, product, support

# Each label is paired with the system action it routes to. Classify
# and dispatch happen in one call.
@ml_switch(labels={
    "bug":             engineering.add,
    "feature_request": product.add,
    "question":        support.notify,
})
def triage_rule(ticket: dict) -> str:
    title = (ticket.get("title") or "").lower()
    if "crash" in title or "error" in title:
        return "bug"
    if title.endswith("?"):
        return "question"
    return "feature_request"

triage_rule(ticket)  # classifies AND fires the matching handler
```

Calling `triage_rule(ticket)` classifies the ticket and fires the matching handler in a single call: classification and routing are wired together at the decorator. Later, when downstream signals reveal whether the routing was right (a resolution code on the ticket, a CSAT score on the interaction, an A/B conversion), `triage_rule.switch.record_verdict(record_id, Verdict.CORRECT)` registers an outcome; the gate fires automatically every $N$ verdicts and graduates the underlying classifier when evidence justifies it. What §1 named as the typical cost of replacing a rule by hand (outcome plumbing, feature pipelines, training, deployment, shadow evaluation, monitoring, rollback) is what the gate handles internally, once and uniformly across every site that uses the decorator.

Why not skip the decorator and inline the routing as `if "crash" in title: engineering.add(ticket)`? Because the decorator turns the call site into a discoverable, auditable, gradable seam. Every classification auto-logs with shadow observations from any `model=` or `ml_head=` slot also wired in; every verdict feeds the gate; the analyzer (§9 implementation) can find the site by AST pattern; the audit chain captures every decision; the safety floor and circuit breaker stay intact across the lifecycle. The inlined version works on day one and gives up every one of those affordances by day thirty.

This is the entire user-facing API for the common case: one decorator wires classification, dispatch, and graduation; production code calls one function.

The point: the body of `triage_rule` is the exact `if`/`else` you would have inlined. The decorator is the only addition. Everything else (outcome logging, gate evaluation, lifecycle migration, audit chain, circuit breaker) happens for free. **You write your rule once; Dendra upgrades it from a hand-written keyword check to a trained ML head, in production, with no rewrite.** On the four benchmarks of §5, that upgrade is the difference between 70.0% and 88.7% accuracy on ATIS, and between 0.5–1.8% and 81.9–87.7% accuracy across the high-cardinality regime. Production code calls `triage_rule(ticket)` on day one and on day thirty; the function on the inside is what changed.

### 9.2 Storage and durability

The default storage is a bounded in-memory rotator (10,000 records FIFO). Production deployments pass `persist=True` to switch to a resilient file-backed store (`FileStorage`) wrapped in an in-memory fallback (`ResilientStorage`) that buffers on disk failure and drains on recovery. A `SqliteStorage` backend ships for concurrent multi-process write workloads. The storage layer is pluggable via the `Storage` protocol; users with existing audit infrastructure (Kafka, Redshift, Snowflake) can plug those in.

### 9.3 The verifier slot

Verdict acquisition is the rate-limiting step in production graduation: the McNemar gate cannot fire on outcomes that haven't been collected. The library ships an autonomous-verifier default (`verifier=default_verifier()`) that auto-detects a local Ollama model (`qwen2.5:7b` by default, selected by an SLM benchmark we report in a companion write-up) or an OpenAI/Anthropic API key, and uses the LLM-as-judge pattern (Liu et al., 2023; Zheng et al., 2023) to produce verdicts for every classification. The same-LLM-as-classifier-and-judge bias is enforced at construction time: the `JudgeSource` constructor refuses a judge model that resolves to the same identity as the classifier, with a pointer to the bias literature in the error message. Other verdict sources include `WebhookVerdictSource`, `HumanReviewerSource` (for cold-start labeling and periodic-drain workflows; refused on the inline classify hot path because of its blocking semantics), and `LLMCommitteeSource` for ensemble verdicts.

### 9.4 The `CandidateHarness`

For the autoresearch use case, `CandidateHarness` accepts a stream of agent-proposed candidate classifiers, runs them in shadow mode against live production traffic, and either promotes via the McNemar gate or discards. This is the production substrate underneath the autoresearch loops described by Karpathy (2025), Shinn et al. (2023), and Wang et al. (2023). The harness is the unsexy plumbing — outcome logging, paired-correctness tracking, gate evaluation, signed advance proposals — that makes agent-driven model search production-safe at the classification primitive. Trivedy's (2026) *harness hill-climbing* framing is the methodology that operates *on top of* the harness; ours is the substrate underneath.

### 9.5 Licensing and governance

The client SDK (everything end-users `import dendra` reaches) is Apache 2.0. The analyzer / research / ROI components — `analyzer.py`, `cli.py`, `research.py`, `roi.py` — are BSL 1.1 with a Change Date of 2030-05-01 (Change License: Apache 2.0). The split is enforced at PR time by a CI workflow that validates SPDX headers and a path allowlist. The licensing rationale, threat model, and trademark policy are published in `LICENSE.md`, `docs/THREAT_MODEL.md`, and `TRADEMARKS.md` respectively.

---

## 10. Discussion

### 10.1 What this paper changes for practitioners

The headline shift is in the *vocabulary*. Before this work, "should we replace this rule with ML?" was an intuitive engineering call. After this work, it is a paired-McNemar gate evaluation that fires automatically every 250 outcomes once $n_{\min} = 200$ paired outcomes accumulate. The decision is reproducible, auditable, and bounded above by $\alpha$ in Type-I error.

The methodological shift is in *making the safety floor structural*. Production ML's history is full of safe-fallback designs that worked in production for a while and then were quietly removed because the operator forgot the original rationale (Sculley et al., 2015's *boundary erosion* category). The lifecycle's structural rule preservation is not a discipline; it is a type-system fact. It survives operator turnover.

### 10.2 What this paper does not change

We do not claim to solve verdict acquisition. The McNemar gate eats verdicts; it cannot generate them. The autonomous-verifier default reduces the verdict-rate constraint dramatically (from a reviewer-queue-bound 5% to a model-bound 100%), but the underlying problem of verdict *quality* is a research direction in itself. The companion write-up on small-LLM verifier selection is a first step.

We do not claim to solve drift in full. v1 handles one specific kind, slow accuracy degradation of the deployed classifier relative to the rule, detected via the same paired-test machinery used for advancement (§3.3, Remark) and resolved by stepping the lifecycle back one phase when the gate fires. What remains unsolved: distributional drift on input features (the gate operates on outcome paired-correctness, not input distribution); adversarial or poisoned-verdict drift; drift on axes other than accuracy (latency, cost, coverage; see §10.5); and the empirical characterization of multi-step demotion trajectories under sustained drift. The drift-detection literature (Lu et al., 2018; Gama et al., 2014) frames these as separable problems; integrating their strongest results into our Gate protocol is a natural follow-on.

We do not claim that one library fits all classification sites. Some sites have abundant day-one training data and should just train a classifier (no rule, no graduation). Some are well-served by an off-the-shelf zero-shot LLM (skip directly to $P_2$). Some have no outcome signal at all (graduation impossible). The lifecycle is for the modal production case — rule today, evidence accumulates, graduate when the gate clears — and we do not pretend to displace the alternatives where they fit better.

### 10.3 Implications for the cascade-routing literature

The cascade and routing literature (FrugalGPT, RouteLLM, Dekoninck et al.) optimizes inference-time routing among already-trained models. Our work generalizes the question to the *lifecycle in which models are introduced and retired*: a `MODEL_PRIMARY` phase is structurally identical to a FrugalGPT cascade with two stages (rule + LLM), and the $P_4 \to P_5$ transition is the moment when the cascade's escalation tier becomes unnecessary. A future synthesis would treat the cascade and the lifecycle as instances of the same meta-decision-process, with the rule floor as a structural constant and the model layers as the time-varying decision variable. Dekoninck et al.'s formal-foundation work is the obvious starting point; our paired-McNemar gate is one constructive instantiation among many.

### 10.4 Implications for the autoresearch / agent literature

The autoresearch loop pattern (Karpathy, 2025; Shinn et al., 2023; Wang et al., 2023; Wei et al., 2022) is currently bottlenecked by the *evaluation harness*. An agent that proposes 100 candidate classifiers per hour but cannot statistically distinguish them will converge on whichever proposal happens to overfit the evaluator's bias. The McNemar gate is one solution: a statistically-grounded, paired comparison that yields a $p$-value the agent's stopping rule can read directly. Trivedy's (2026) "Better Harness" framing — that harness hill-climbing is the iterative discipline of evaluating the evaluator — operates one level above ours; our gate is the underlying primitive. Lance Martin's (2023) Auto-Evaluator is another primitive in this space, focused on retrieval-quality evaluation; the unification — paired, calibrated, statistically-grounded comparison across the full evaluation surface — is a research program.

### 10.5 Future work

- **Production case studies.** Four benchmarks is methodological; one production deployment is operational. Both are needed to claim the framework generalizes.
- **Bidirectional drift handling.** The v1 reference implementation ships an autonomous demotion path: the same paired-test machinery, called with the rule as the comparison target, fires when accumulated evidence shows the current decision-maker has been overtaken by the rule. The paper above proves the Type-I error symmetry; the open work is empirical characterization of demotion timing under realistic drift profiles (gradual vs adversarial, bounded vs unbounded distribution shift) and the demotion-curve analog of the transition curves we report.
- **Multi-axis gating.** Production decisions involve more than one safety axis. Latency, cost, distributional coverage, and provider availability all admit paired observations and statistical tests in the same shape as the accuracy gate (Wilcoxon for continuous metrics, paired McNemar for binary indicators, threshold-based for constant-per-source quantities). The Gate protocol is direction- and axis-agnostic by construction; multi-axis support is a configuration extension, not a redesign. Joint safety follows from the union bound across axes (k axes, each bounded by $\alpha$, give a per-cycle joint bound of $k\alpha$). A companion paper on the multi-axis safety story is the natural follow-on.
- **Federated training.** Aggregating outcome pools across institutions without raw-data sharing. Does federation accelerate transitions?
- **Adversarial transitions.** What happens to the gate under deliberate distribution shift?
- **Verifier quality.** The companion write-up on small-LLM verifier selection establishes that a 7B parameter model can serve as a credible judge on intent-classification corpora; characterizing the verdict-quality / verdict-rate frontier across model classes is a paper in itself.
- **Theoretical strengthening.** The safety theorem is per-transition and per-axis. A stronger statement, that the joint distribution over transitions and axes admits a better-than-union-bound guarantee, would require characterizing the dependence between gates. Tzamos et al.'s theory work on classifier-comparison testing is the relevant adjacent literature.

---

## 11. Conclusion

Rule-to-ML graduation is a universal production pattern that has been left to per-project engineering effort, with no shared statistical primitive and no structural safety floor. Formalized as a six-phase graduated-autonomy lifecycle, it admits a paired McNemar gate at $\alpha = 0.01$ that bounds per-transition Type-I error, and empirical transition curves on four public benchmarks demonstrate that the gate is tight at small outcome volumes (250 outcomes for every benchmark, paired $p < 0.01$). Two regimes emerge: a low-cardinality narrow-domain regime where rules give a usable baseline that ML beats by ~19 points, and a high-cardinality regime where rules are non-viable from day one and graduation is the only path to a working classifier. The Dendra reference implementation, the transition-curve dataset, and the benchmark harness are released so that practitioners can adopt the primitive in commercial workloads and so that the empirical claim can be reproduced and extended.

The framing is graduated autonomy. The contribution is a *primitive* — one library, one gate, one safety theorem — for a pattern that thousands of production codebases re-solve from scratch. The transition curve is the empirical anchor that lets a practitioner answer, before deployment: *when should this rule learn?*

---

## Acknowledgments

The author thanks the reviewers and early adopters who provided feedback on drafts of this work.

---

## References

*Notation: arXiv IDs and venue names are inline; this section will be regenerated as a `\bibliography{}` block in the LaTeX submission.*

Amodei, D., Olah, C., Steinhardt, J., Christiano, P., Schulman, J., & Mané, D. (2016). Concrete Problems in AI Safety. arXiv:1606.06565.

Bottou, L. (1998). Online Learning and Stochastic Approximations. In D. Saad (Ed.), *Online Learning in Neural Networks*. Cambridge University Press.

Bouckaert, R. R. (2003). Choosing Between Two Learning Algorithms Based on Calibrated Tests. *ICML 2003*.

Breck, E., Cai, S., Nielsen, E., Salib, M., & Sculley, D. (2017). The ML Test Score: A Rubric for ML Production Readiness and Technical Debt Reduction. *IEEE Big Data 2017*.

Casanueva, I., Temčinas, T., Gerz, D., Henderson, M., & Vulić, I. (2020). Efficient Intent Detection with Dual Sentence Encoders. *Proceedings of the 2nd Workshop on NLP for ConvAI*.

Chen, L., Zaharia, M., & Zou, J. (2024). FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance. *Transactions on Machine Learning Research (TMLR)*. arXiv:2305.05176.

Chiang, W.-L., Zheng, L., Sheng, Y., Angelopoulos, A. N., Li, T., Li, D., Zhang, H., Zhu, B., Jordan, M., Gonzalez, J. E., & Stoica, I. (2024). Chatbot Arena: An Open Platform for Evaluating LLMs by Human Preference. *ICML 2024*.

Dekoninck, J., et al. (2025). A Unified Approach to Routing and Cascading for LLMs. *ICML 2025*. arXiv:2410.10347.

Demšar, J. (2006). Statistical Comparisons of Classifiers over Multiple Data Sets. *Journal of Machine Learning Research, 7*, 1–30.

Dietterich, T. G. (1998). Approximate Statistical Tests for Comparing Supervised Classification Learning Algorithms. *Neural Computation, 10*(7), 1895–1923.

Feurer, M., Klein, A., Eggensperger, K., Springenberg, J., Blum, M., & Hutter, F. (2015). Efficient and Robust Automated Machine Learning. *NeurIPS 2015*.

Gama, J., Žliobaitė, I., Bifet, A., Pechenizkiy, M., & Bouchachia, A. (2014). A Survey on Concept Drift Adaptation. *ACM Computing Surveys, 46*(4).

Guo, C., Pleiss, G., Sun, Y., & Weinberger, K. Q. (2017). On Calibration of Modern Neural Networks. *ICML 2017*.

Hemphill, C. T., Godfrey, J. J., & Doddington, G. R. (1990). The ATIS Spoken Language Systems Pilot Corpus. *Proceedings of the DARPA Speech and Natural Language Workshop*.

Hutter, F., Kotthoff, L., & Vanschoren, J. (Eds.) (2019). *Automated Machine Learning: Methods, Systems, Challenges*. Springer.

Karpathy, A. (2025). On the autoresearch loop. *Public talks and writings*. [Cited as the most visible recent advocacy for LLM-driven research loops; primary venue references in final version.]

Kuleshov, V., Fenner, N., & Ermon, S. (2018). Accurate Uncertainties for Deep Learning Using Calibrated Regression. *ICML 2018*.

Langford, J., Li, L., & Strehl, A. (2007). Vowpal Wabbit. *Online machine learning system*. https://vowpalwabbit.org

Larson, S., Mahendran, A., Peper, J. J., Clarke, C., Lee, A., Hill, P., Kummerfeld, J. K., Leach, K., Laurenzano, M. A., Tang, L., & Mars, J. (2019). An Evaluation Dataset for Intent Classification and Out-of-Scope Prediction. *EMNLP-IJCNLP 2019*.

Liu, X., Eshghi, A., Swietojanski, P., & Rieser, V. (2019). Benchmarking Natural Language Understanding Services for Building Conversational Agents. *Proceedings of the 10th International Workshop on Spoken Dialogue Systems*.

Liu, Y., Iter, D., Xu, Y., Wang, S., Xu, R., & Zhu, C. (2023). G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment. *EMNLP 2023*. arXiv:2303.16634.

Lu, J., Liu, A., Dong, F., Gu, F., Gama, J., & Zhang, G. (2018). Learning under Concept Drift: A Review. *IEEE Transactions on Knowledge and Data Engineering*.

Martin, L. (2023). Auto-Evaluator: an open-source tool for LLM evaluation on retrieval QA. LangChain blog. https://blog.langchain.com/auto-evaluator-opportunities/

McNemar, Q. (1947). Note on the Sampling Error of the Difference between Correlated Proportions or Percentages. *Psychometrika, 12*(2), 153–157.

Ong, I., Almahairi, A., Wu, V., Chiang, W.-L., Wu, T., Gonzalez, J. E., Kadous, M. W., & Stoica, I. (2024). RouteLLM: Learning to Route LLMs with Preference Data. *ICLR 2025*. arXiv:2406.18665.

Paleyes, A., Urma, R.-G., & Lawrence, N. D. (2022). Challenges in Deploying Machine Learning: A Survey of Case Studies. *ACM Computing Surveys, 55*(6).

Papailiopoulos, D., et al. (2025). ReJump: A reasoning-evaluation framework for large language models. [Recent MSR AI Frontiers Lab work; final venue/citation in submission.]

Polyzotis, N., Roy, S., Whang, S. E., & Zinkevich, M. (2018). Data Lifecycle Challenges in Production Machine Learning: A Survey. *SIGMOD Record, 47*(2).

Sculley, D., Holt, G., Golovin, D., Davydov, E., Phillips, T., Ebner, D., Chaudhary, V., Young, M., Crespo, J.-F., & Dennison, D. (2015). Hidden Technical Debt in Machine Learning Systems. *NeurIPS 2015*.

Shinn, N., Cassano, F., Gopinath, A., Narasimhan, K., & Yao, S. (2023). Reflexion: Language Agents with Verbal Reinforcement Learning. *NeurIPS 2023*. arXiv:2303.11366.

Trapeznikov, K., & Saligrama, V. (2013). Supervised Sequential Classification Under Budget Constraints. *AISTATS 2013*.

Trivedy, V. (2026). Better Harness: A Recipe for Harness Hill-Climbing with Evals. LangChain blog. https://blog.langchain.com/better-harness-a-recipe-for-harness-hill-climbing-with-evals/

Tzamos, C., et al. *Theoretical foundations for classifier comparison testing.* [Working reference; final citation in submission. Tzamos's broader theory portfolio at the ML/stats interface motivates the principled-test-statistic framing.]

Viola, P., & Jones, M. (2001). Rapid Object Detection Using a Boosted Cascade of Simple Features. *CVPR 2001*.

Wang, G., Xie, Y., Jiang, Y., Mandlekar, A., Xiao, C., Zhu, Y., Fan, L., & Anandkumar, A. (2023). Voyager: An Open-Ended Embodied Agent with Large Language Models. arXiv:2305.16291.

Wei, J., Wang, X., Schuurmans, D., Bosma, M., Ichter, B., Xia, F., Chi, E. H., Le, Q. V., & Zhou, D. (2022). Chain-of-Thought Prompting Elicits Reasoning in Large Language Models. *NeurIPS 2022*.

Zheng, L., Chiang, W.-L., Sheng, Y., Zhuang, S., Wu, Z., Zhuang, Y., Lin, Z., Li, Z., Li, D., Xing, E. P., Zhang, H., Gonzalez, J. E., & Stoica, I. (2023). Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena. *NeurIPS 2023*. arXiv:2306.05685.

---

## Appendix A — Reference Rules

The four reference rules are auto-generated by `dendra.benchmarks.rules.build_reference_rule(seed=100)`. The full source is at `src/dendra/benchmarks/rules.py`. We publish the generated rules per benchmark in `docs/papers/2026-when-should-a-rule-learn/rules/` for replication.

## Appendix B — Reproducibility Checklist

- ✓ Code public (Apache 2.0, GitHub).
- ✓ Seeds documented in benchmark JSONL.
- ✓ Datasets public.
- ✓ Hyperparameters in source.
- ✓ Compute requirements: ~30 minutes per benchmark on a 2024-vintage laptop CPU.
- ✓ All benchmark JSONLs and `paired_mcnemar_summary.json` released.
- ✓ Benchmark harness reproduces the result: `dendra bench {atis,banking77,clinc150,hwu64}`.

## Appendix C — Code listings

Key implementation references:
- `src/dendra/core.py::LearnedSwitch` — six-phase lifecycle.
- `src/dendra/gates.py::McNemarGate` — paired-McNemar gate.
- `src/dendra/research.py::run_benchmark_experiment` — the harness used in §4–§5.
- `src/dendra/verdicts.py::JudgeSource` — LLM-as-judge with same-model bias guardrail.
- `src/dendra/autoresearch.py::CandidateHarness` — autoresearch substrate.

---

_Copyright (c) 2026 B-Tree Ventures, LLC. Apache-2.0 licensed._
