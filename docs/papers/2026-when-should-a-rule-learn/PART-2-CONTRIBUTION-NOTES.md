# "When should a rule learn?" — Part II: contribution notes

> Working notes for the Part-II / follow-on paper. Captured 2026-06-09 from the
> graduated-autonomy + multimodal work. **These are framing + evidence notes to
> fold into the draft at the right time — not the draft itself.**
>
> Companion (engineering detail / reproduction pointers): agent memory
> `paper2-graduation-observability` and `aga-audit-and-paper2-outline`.

## Reframe

Part I asked **"when *should* a rule learn?"** Part II asks
**"when *can* a rule be *shown* it should?"** — the observability /
identifiability conditions for graduated autonomy.

## The two contributions (be honest about which is which)

1. **Extend Part I to audio + video — the real new *content*.** The multimodal
   calibration: rendered-media vision (audio→spectrogram, video→frames) **fails
   zero-shot and recovers with cheap few-shot**, quantified per modality. This
   is genuinely useful *measurement*, even if not novel theory.

2. **An applied "pitfalls" contribution.** Graduated-autonomy / cost-tiered
   systems silently inherit **selective-labels bias** in their promotion gate
   and **never graduate**: a shadow tier is scored only on records where the
   incumbent's decision was *correct*, so a challenger that is right exactly
   when the incumbent is wrong (its entire value) is never credited. We give
   the concrete failure, a **cheap sparse fix** (adjudicate the shadow's own
   output on disagreement-when-the-decision-was-wrong), and a **methodological
   correction** (graduation eval must test the *organic gate*, not forced phase
   transitions + tiers scored directly against gold).

## The line that keeps us safe (use verbatim or close)

> "We do not claim a new estimator; we show that a known pathology (selective
> labels) silently governs whether tiered-autonomy systems can graduate,
> demonstrate it on a deployed system, and give a cheap fix and an evaluation
> correction."

## SOBER novelty assessment (read before writing the intro)

**Nothing here is theoretically novel.** Pushed hard; found none. Cite the
prior art **up front** so reviewers see we know it isn't new — that candor is
what makes it publishable instead of embarrassing:

- The bug = **selective labels** (Lakkaraju, Kleinberg, Leskovec, Ludwig,
  Mullainathan, *The Selective Labels Problem*, KDD 2017) / **reject inference**
  (credit scoring) / **off-policy & counterfactual evaluation** / the
  **fundamental problem of causal inference** (only the taken action's outcome
  is observed).
- The fix = **disagreement-based active learning** / query-by-committee.
- Sharpest *explanatory* frame (not a result): decision-conditioned labels
  **asymmetrically censor the McNemar off-diagonal** — cell *b*
  (challenger-right / incumbent-wrong) is never observed without adjudication,
  while *c* is — so the bias is *directional*, not merely "less data." This is
  just MNAR (missing-not-at-random) restated; a good figure, not a theorem.
- Rejected angle (logged so we don't re-pitch it): "cost of identifiability in
  cost-tiered systems" — it's just active-learning cost analysis.

**Do NOT claim novelty beyond applied/empirical.**

## Evidence to cite (numbers measured 2026-06-09)

- **Incumbent bias / never-graduates (the headline demo).** ESC-50 acoustic
  switch, organic gate-driven (NOT forced-phase). Shadow model 96% vs
  spectral-centroid rule 57% ground-truth accuracy → McNemar gate reports
  `current=1.0, target=0.956, "not better"` (it only scores the ~68/120 records
  where the rule was already right). A 39-pt-better model is judged worse and
  never graduates. With the fix it graduates MODEL_SHADOW→MODEL_PRIMARY
  **predictably (~sample 40–50 across 3 seeds)**; a 55%-model control (≈ rule)
  correctly does **not**. Modality-independent (text shares the gate).
- **Multimodal calibration (the new content).** ESC-50 spectrogram-vision via
  Claude haiku: **0.20 zero-shot (= chance for 5 classes)** vs a 0.55 rule;
  **1 exemplar/class → 0.47** (a >2× lift; a few more clear the rule and
  graduate). Honest takeaway: rendered-media vision is unusable zero-shot,
  largely recoverable with cheap few-shot grounding.
- **Methodological correction.** Prior benchmarks/AGA studies *force* phase
  transitions (`advance(target=…)`) and score each tier directly against gold,
  so they validate per-phase accuracy, never the gate's *promotion decision*.
  The organic gate-driven harness (`scripts/validate_multimodal_graduation.py`,
  in the SDK repo) is the first to test promotion; it should become a standing
  eval.

## Engineering provenance (for the reproduction / artifact section)

- Fix shipped in **postrule 1.1.21** (`adjudicate_disagreements`, default on;
  `_source_correct_for` completion). Multimodal adapters (provider-neutral
  vision, few-shot exemplars) in **1.1.22**.
- Harness: `scripts/validate_multimodal_graduation.py` (ESC-50, organic
  graduation across seeds, stub + real-vision modes).
- Datasets: ESC-50 (Piczak 2015) for audio; image path validated on CIFAR-10 in
  prior bench.
