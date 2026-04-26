# Related-work bibliography

Annotated reference list for the paper. Tiered by must-cite /
should-cite / nice-to-have, with a one-line rationale on each
entry.

**Tier conventions:**

- **MUST** — direct lineage; the paper's contribution can't be
  positioned without it. Reviewer will flag if missing.
- **SHOULD** — important context; strengthens the framing.
  Reviewer might note absence but not reject.
- **NICE** — historical context, adjacent fields, or alternate
  framings. Cut if tight on space.

When we move to LaTeX, every MUST entry needs a working BibTeX
record; SHOULD and NICE entries can land in a single bibtex file
and we cite as needed.

---

## A. Direct lineage — LLM cascade routing

These are the papers we're explicitly building on. Our
"transition curves" and "graduated-autonomy lifecycle" are the
production-deployment generalization of what these papers do at
inference-time routing.

### MUST

- **Chen, L., Zaharia, M., & Zou, J. (2023/2024). FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance.** *Transactions on Machine Learning Research (TMLR), 2024.* arXiv:2305.05176.
  *The foundational LLM-cascade paper. Introduces the "weakest-model-first, escalate-on-low-confidence" pattern. Our work generalizes the cost-quality tradeoff into a multi-phase deployment lifecycle and adds the rule floor. Cite in §2 (related work) and §3 (the rule→LLM phases echo Chen's cascade structure).*

- **Ong, I., Almahairi, A., Wu, V., Chiang, W.-L., Wu, T., Gonzalez, J. E., Kadous, M. W., & Stoica, I. (2024). RouteLLM: Learning to Route LLMs with Preference Data.** arXiv:2406.18665.
  *Extends FrugalGPT to learned routing from preference data. Reduces cost by 85% on MT-Bench while keeping 95% of GPT-4 quality. Cite in §2 and §6 (our McNemar gate is a statistical analog to RouteLLM's preference-trained router — both decide "use the cheaper option when it's good enough.").*

- **Dekoninck, J., et al. (2024). A Unified Approach to Routing and Cascading for LLMs.** arXiv:2410.10347.
  *Theoretical unification of routing and cascading. Important for our §6 differentiation — our "phase transition" framing is closer to cascading than routing. Cite as the formal-foundation reference.*

### SHOULD

- **GATEKEEPER: Improving Model Cascades Through Confidence Tuning.** OpenReview pdf id=qYI4fw3g4v.
  *Calibration-aware cascading. Useful when discussing why our `confidence_threshold` parameter exists.*

- **Luo et al. (2026). RouteLMT.** [if available — recent in-model router work]
  *Recent (2026) advance: in-model router via lightweight LoRA adaptation. Supports our claim that LLM-routing remains an active research front.*

### NICE

- **Wang et al. (2026). ICL-Router.** [if available]
  *Compact in-context vector routers. Adjacent.*

- **Jaideep Ray (2024). LLM Routing.** Medium post, *Better ML*.
  *Practitioner survey of routing patterns. Useful for citing real-world deployment contexts.*

---

## B. Statistical methodology — paired tests

Our McNemar gate is the load-bearing piece of the safety
guarantee. These citations establish that the methodology is
classical and well-grounded.

### MUST

- **McNemar, Q. (1947). Note on the sampling error of the difference between correlated proportions or percentages.** *Psychometrika, 12(2), 153–157.*
  *The original McNemar paper. Cite once when introducing the test (§3.3 or wherever the gate is formalized).*

- **Dietterich, T. G. (1998). Approximate Statistical Tests for Comparing Supervised Classification Learning Algorithms.** *Neural Computation, 10(7), 1895–1923.*
  *Canonical reference for paired-McNemar in ML eval. Dietterich's recommendation: use McNemar's test when classifiers can be evaluated only once on a single test set (which is exactly our online-decision case). Cite as the methodological justification in §3.3.*

### SHOULD

- **Demšar, J. (2006). Statistical Comparisons of Classifiers over Multiple Data Sets.** *Journal of Machine Learning Research, 7, 1–30.*
  *Multi-dataset extension. Cite if we want to defend against "but you only ran four benchmarks."*

- **Bouckaert, R. R. (2003). Choosing Between Two Learning Algorithms Based on Calibrated Tests.** *ICML 2003.*
  *Calibration concerns in pairwise classifier tests. Adjacent to our `confidence_threshold` discussion.*

### NICE

- **Block-regularized 5×2 Cross-validated McNemar's Test.** arXiv:2304.03990.
  *Recent (2023) extension. Useful for showing the methodology is still actively developed.*

---

## C. Production ML safety / MLOps

These citations frame the *why this matters* — production ML
systems have a documented history of silent failure modes that
hand-coded rules don't share.

### MUST

- **Sculley, D., Holt, G., Golovin, D., Davydov, E., Phillips, T., Ebner, D., Chaudhary, V., Young, M., Crespo, J.-F., & Dennison, D. (2015). Hidden Technical Debt in Machine Learning Systems.** *NeurIPS 2015.*
  *Foundational. Documents how ML systems incur invisible technical debt (boundary erosion, hidden feedback loops, configuration drift). Our §1 motivation cites this directly: replacing a legible rule with a learned classifier is a debt-incurring operation, and Sculley's framework helps justify why teams resist the migration.*

- **Breck, E., Cai, S., Nielsen, E., Salib, M., & Sculley, D. (2017). The ML Test Score: A Rubric for ML Production Readiness and Technical Debt Reduction.** *IEEE Big Data 2017.*
  *Concrete checklist for production-readiness. Our circuit-breaker, audit chain, and statistical-gate-before-promotion match several rubric items. Cite in §7 (safety properties).*

### SHOULD

- **Polyzotis, N., Roy, S., Whang, S. E., & Zinkevich, M. (2018). Data Lifecycle Challenges in Production Machine Learning: A Survey.** *SIGMOD Record, 47(2).*
  *Data-lifecycle perspective; useful when discussing the outcome log as the substrate for everything else.*

- **Paleyes, A., Urma, R.-G., & Lawrence, N. D. (2022). Challenges in Deploying Machine Learning: A Survey of Case Studies.** *ACM Computing Surveys 55(6).*
  *Case-study survey of real ML deployment failures. Strong evidence base for the "rules calcify" / "ML-from-day-one fails" framings in §1.*

- **Amodei, D., Olah, C., Steinhardt, J., Christiano, P., Schulman, J., & Mané, D. (2016). Concrete Problems in AI Safety.** arXiv:1606.06565.
  *AI-safety foundational paper. Cite for the safety-critical framing and the rule-floor architectural guarantee.*

### NICE

- **Kästner, C. (2024). Machine Learning in Production: From Models to Products.** Online textbook. mlip-cmu.github.io/book/
  *CMU's MLIP course material. Useful for citing pedagogy on production ML safety. Less load-bearing than Sculley/Breck.*

- **McGregor, S. (2021). Preventing Repeated Real World AI Failures by Cataloging Incidents: The AI Incident Database.** *AAAI 2021.*
  *Empirical AI-failure cataloging. Adjacent.*

---

## D. LLM-as-judge / evaluation

Cited specifically in the verdict-source / committee-bias
discussion. The self-judgment-bias guardrail in
`JudgeSource` rests on this literature.

### MUST

- **Liu, Y., Iter, D., Xu, Y., Wang, S., Xu, R., & Zhu, C. (2023). G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment.** *EMNLP 2023.* arXiv:2303.16634.
  *LLM-as-judge methodology + the bias caveats. Direct citation for our `JudgeSource` self-judgment guardrail.*

- **Zheng, L., Chiang, W.-L., Sheng, Y., Zhuang, S., Wu, Z., Zhuang, Y., Lin, Z., Li, Z., Li, D., Xing, E. P., Zhang, H., Gonzalez, J. E., & Stoica, I. (2023). Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena.** *NeurIPS 2023.* arXiv:2306.05685.
  *Establishes when LLM-as-judge is reliable and where the bias is. Cite in §verdict-sources for the same-LLM-as-judge-and-classifier anti-pattern.*

### SHOULD

- **Chiang, W.-L., Zheng, L., Sheng, Y., Angelopoulos, A. N., Li, T., Li, D., Zhang, H., Zhu, B., Jordan, M., Gonzalez, J. E., & Stoica, I. (2024). Chatbot Arena: An Open Platform for Evaluating LLMs by Human Preference.** *ICML 2024.*
  *Crowdsourced preference comparison. Useful for triangulating verdict sources in the §verdict-sources discussion.*

### NICE

- **Zheng et al. (2024). LLM-as-a-Judge Survey.**
  *Catch-up survey. Cite if we need a single broad reference.*

---

## E. AutoML — for differentiation

Cited specifically to *differentiate* from. AutoML is offline
model selection; we're online model promotion. The contrast is
load-bearing for our positioning.

### SHOULD

- **Hutter, F., Kotthoff, L., & Vanschoren, J. (Eds.) (2019). Automated Machine Learning: Methods, Systems, Challenges.** Springer.
  *AutoML book. Cite for the comprehensive view of "AutoML as offline model search."*

- **Feurer, M., Klein, A., Eggensperger, K., Springenberg, J., Blum, M., & Hutter, F. (2015). Efficient and Robust Automated Machine Learning.** *NeurIPS 2015.*
  *Auto-sklearn paper. The most-cited AutoML system. Used as the canonical "what AutoML actually does" reference.*

### NICE

- **Olson, R. S., & Moore, J. H. (2016). TPOT: A Tree-based Pipeline Optimization Tool for Automating Machine Learning.** *ICML AutoML Workshop 2016.*
  *Genetic-programming AutoML. Adjacent.*

---

## F. Cascade learning — historical lineage

The cascade pattern predates LLMs by 20+ years. Citing the
classical-CV ancestors strengthens the lineage argument.

### SHOULD

- **Viola, P., & Jones, M. (2001). Rapid Object Detection using a Boosted Cascade of Simple Features.** *CVPR 2001.*
  *The foundational cascade paper. Cited in nearly every modern cascade-routing paper. Establishes that "use cheap classifiers first, escalate on uncertainty" has a 25-year lineage.*

- **Trapeznikov, K., & Saligrama, V. (2013). Supervised Sequential Classification Under Budget Constraints.** *AISTATS 2013.*
  *Sequential classification with cost. Closer to our framing than Viola-Jones; useful as a bridge cite.*

---

## G. Online learning / online model selection

Adjacent area. Important to cite for differentiation —
online-learning literature is about updating model parameters
continuously, not about graduated rule-to-ML migration.

### SHOULD

- **Bottou, L. (1998). Online Learning and Stochastic Approximations.** *In* Saad, D. (Ed.), *Online Learning in Neural Networks*, Cambridge University Press.
  *Canonical reference for online learning. Cite in §2 to differentiate from continuous-update approaches.*

- **Langford, J., et al. (2007). Vowpal Wabbit Online Learning System.** Online ML system.
  *The most-cited production online-learning system. Reviewers will ask "how is this different from VW?" — answer in §2 paragraph: VW updates a model continuously without a rule floor; we graduate phases discretely with a rule floor.*

### NICE

- **French, R. M. (1999). Catastrophic forgetting in connectionist networks.** *Trends in Cognitive Sciences.*
  *Why naive online learning is hard. Adjacent.*

---

## H. Agent / autoresearch — for the production-substrate framing

Recent / zeitgeist citations. Connects our `CandidateHarness`
positioning to the agentic-loop pattern that's currently in
public discourse.

### SHOULD

- **Karpathy, A. (2025). On building an autoresearch loop.** [Karpathy's recent talks / blog posts on the autoresearch pattern — find the canonical reference.]
  *The most visible recent advocacy for LLM-driven research loops. Cite in §positioning when introducing the autoresearch hook in §autoresearch.md companion doc.*

- **Wang, G., Xie, Y., Jiang, Y., Mandlekar, A., Xiao, C., Zhu, Y., Fan, L., & Anandkumar, A. (2023). Voyager: An Open-Ended Embodied Agent with Large Language Models.** arXiv:2305.16291.
  *LLM-driven curriculum learning loop. Cite as exemplar of the autoresearch-style loops our `CandidateHarness` would deploy.*

- **Shinn, N., Cassano, F., Gopinath, A., Narasimhan, K., & Yao, S. (2023). Reflexion: Language Agents with Verbal Reinforcement Learning.** *NeurIPS 2023.* arXiv:2303.11366.
  *LLM self-reflection / iteration loop. Same lineage.*

### NICE

- **Wei, J., Wang, X., Schuurmans, D., Bosma, M., Ichter, B., Xia, F., Chi, E. H., Le, Q. V., & Zhou, D. (2022). Chain-of-Thought Prompting Elicits Reasoning in Large Language Models.** *NeurIPS 2022.*
  *Foundational reasoning-in-prompts paper. Adjacent.*

- **AutoGPT, BabyAGI lineage.** *2023 OSS projects.*
  *Cite informally as the popular-press exemplars of agentic loops.*

---

## I. Calibration / uncertainty estimation

Useful when defending the `confidence_threshold` design.

### NICE

- **Guo, C., Pleiss, G., Sun, Y., & Weinberger, K. Q. (2017). On Calibration of Modern Neural Networks.** *ICML 2017.*
  *The "modern networks are miscalibrated" paper. Cite if we get into a calibration discussion.*

- **Kuleshov, V., Fenner, N., & Ermon, S. (2018). Accurate Uncertainties for Deep Learning Using Calibrated Regression.** *ICML 2018.*
  *Calibrated uncertainty for regression. Adjacent.*

---

## J. Drift detection / continuous evaluation

Adjacent — distinguishing what we do from drift detection.

### NICE

- **Lu, J., Liu, A., Dong, F., Gu, F., Gama, J., & Zhang, G. (2018). Learning under Concept Drift: A Review.** *IEEE TKDE.*
  *Concept-drift survey. Cite in §2 to differentiate.*

- **Gama, J., Žliobaitė, I., Bifet, A., Pechenizkiy, M., & Bouchachia, A. (2014). A Survey on Concept Drift Adaptation.** *ACM Computing Surveys.*
  *Older but more cited than Lu et al.*

---

## How to use this list

**For the paper's "Related Work" section** (target ~1.5 pages):
- Cite **all MUST entries** from sections A, B, C, D.
- Cite **most SHOULD entries** from those sections.
- Cite **at least one** entry from sections E, F, G, H for
  contrast / lineage.
- Skip section I and J unless we get into those discussions.

**Total citations to plan for:** ~25-30 references in the final
paper. Working from this list, we'll have ~40 candidates; cut
to fit page budget.

**For BibTeX:** when we move to the LaTeX build, every MUST
entry needs a clean `@article` / `@inproceedings` record. I can
auto-generate from arXiv IDs — drop me the arXiv ID and I'll
emit BibTeX.

**Open question:** is there other recent work (2025-2026) on
*production ML deployment with statistical gates* specifically
that we should track? The cascade-routing field is well-mapped;
the production-deployment side is sparser. If you've seen
something in your reading, surface it.

## Sources used to build this list

- [FrugalGPT (arXiv 2305.05176)](https://arxiv.org/abs/2305.05176)
- [RouteLLM (arXiv 2406.18665)](https://arxiv.org/abs/2406.18665)
- [Dekoninck et al. unified routing/cascading (arXiv 2410.10347)](https://arxiv.org/abs/2410.10347)
- [Hidden Technical Debt in ML Systems (NeurIPS 2015)](https://papers.nips.cc/paper/5656-hidden-technical-debt-in-machine-learning-systems)
- [Dietterich 1998 — McNemar reference (Sebastian Raschka's exposition)](https://sebastianraschka.com/blog/2018/model-evaluation-selection-part4.html)
- [Demšar 2006 (JMLR)](https://www.jmlr.org/papers/volume7/demsar06a/demsar06a.pdf)
