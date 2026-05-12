# Accuracy review v2 — 2026-05-11

Second adversarial scrutiny pass over `body.typ` / `paper.typ`,
complementary to `ACCURACY_REVIEW-2026-05-11.md` (V1). Mindset:
re-read every paragraph asking "could a reviewer prove this wrong
in 60 seconds?" V1 found two phantom symbols and two fabricated
abstract numbers; this pass independently re-checks load-bearing
V1 "verified clean" items and surfaces what V1 missed.

The two mechanical fixes that were unambiguous are applied in the
same PR; everything substantive is flagged below for human
resolution.

---

## V1 findings still open

All five V1 must-fix items are still in the paper as of this pass:

- **S1-1.** Table 3 "First clear (p < 0.01)" column ignores n_min=200
  (paper §3.2 defines the gate to require `n_paired ≥ n_min`; Table 3's
  values use `n_min` ignored). Still open. See also the related new
  finding **V2-S1-3** below — Table 3's first-clear values are computed
  with a **two-sided** p-value, not the **one-sided** p the production
  gate actually computes; the two interpretations disagree on codelangs
  (300 vs 400 outcomes).
- **S1-2.** Abstract's "hand-written keyword regex of ~35 patterns
  reaches 14.6%" and "22M-parameter embedding-cosine baseline reaches
  62.2%". Confirmed: no JSONL / test / rules file produces either number.
- **S1-3.** Table 4b shuffle data has no backing JSONL files in
  `results/`. Confirmed: `results/multi-seed-README.md` still describes
  the experiment as "pending" (and the README is itself stale; see
  **V2-S2-15** below).
- **S1-4.** §9.3 line 1578 still names `LLMCommitteeSource`; the
  actual class is `JudgeCommittee`.
- **S1-5.** §7.2 line 1356 still names `ApprovalBackend` protocol and
  "signed advance proposals". Confirmed: no symbol named
  `ApprovalBackend`, `AdvanceProposal`, or `advance_proposal` anywhere
  in `src/dendra/`.

---

## Additional must-fix findings

### V2-S1-1. §5.7 CIFAR-10 row counts disagree with the data file

**Paper location.** `body.typ:1186-1187`:

> "The bench (1000 train rows + 200 test rows, deterministic seed)
> reports:"

**Source of truth.** `results/cifar10_paired.jsonl`'s summary record:

```json
{"kind": "summary", "benchmark": "cifar10", "labels": 10,
 "train_rows": 4000, "test_rows": 500, "seed_size": 100, ...}
```

So the actual run was **4,000 train rows + 500 test rows**, not 1,000 +
200 as the prose claims. The Table 8 curve extends to 4,000 outcomes,
which is impossible at 1,000 train rows — so the table itself
independently confirms the prose number is wrong. (At test_rows=500,
the Table 8 ML accuracy of 28.2% corresponds to 141 correct out of 500;
at test_rows=200, 28.2% would be 56.4 — non-integer — so test_rows
cannot be 200.)

**Why this matters.** A reviewer who notices the table going to 4,000
outcomes on a 1,000-row training set will (correctly) flag this as a
mathematical impossibility. The cleanest resolution is to fix the prose
to "4,000 train rows + 500 test rows, deterministic seed" — but it's a
numerical claim change, so flagging for human resolution per the V2
mechanical-fix rules.

---

### V2-S1-2. §9.1 example uses a `record_verdict` API that doesn't exist

**Paper location.** `body.typ:1491`:

> "`classify_content.switch.record_verdict(record_id, Verdict.CORRECT)`
> registers an outcome."

**Source of truth.** `src/dendra/core.py:1583` defines
`record_verdict` with this signature:

```python
def record_verdict(
    self,
    *,
    input: Any,
    label: Any,
    outcome: str,
    source: str = "rule",
    confidence: float = 1.0,
    _result_ctx: ClassificationResult | None = None,
) -> None:
```

All arguments are **keyword-only** (note the `*,`). The signature takes
`input`, `label`, `outcome` — **not** `record_id`. There is no
`record_id` parameter anywhere in `record_verdict`. The example as
written would raise `TypeError: record_verdict() takes 1 positional
argument but 3 were given`.

**Why this matters.** §9.1 is the headline code example showing
practitioners how to wire Dendra into their codebase. A reader who
copy-pastes the cited call gets an immediate exception. The first
external reader to try the example will file an issue.

**Suggested resolution.** Rewrite the inline call to match the actual
API, e.g., `classify_content.switch.record_verdict(input=post,
label=decision, outcome="correct")`. Or use the result-aware path
that's actually idiomatic:
`result = classify_content(post); result.mark_correct()`.

---

### V2-S1-3. §3.2 algorithm spec uses two-sided p; production gate uses one-sided

**Paper location.** `body.typ:442-477` (Algorithm 1, the paired-McNemar
gate definition).

> "The paired McNemar test rejects 'A and B have equal accuracy' at
> significance level α when the two-sided exact binomial p-value of
> min(b, c) under the null Binomial(b + c, 0.5) is below α."
>
> ```
> k = min(b, c)
> p = 2 * BinomialCDF(k; n_paired, 0.5)        # two-sided exact binomial
> if b > c and p < alpha:
>     return (advance, p, b, c)
> ```

**Source of truth.** `src/dendra/viz.py:112-141` implements `mcnemar_p`
as a **one-sided** exact binomial / normal-approximation:

```python
def mcnemar_p(rule_correct: list[bool], ml_correct: list[bool]) -> float | None:
    """One-sided McNemar's paired-test p-value (H1: ML beats rule).
    ...
    """
    b = sum(...)  # ml right, rule wrong
    c = sum(...)  # rule right, ml wrong
    n = b + c
    if n <= 50:
        # Exact one-sided binomial: P(X >= b | X ~ Bin(n, 0.5)).
        from math import comb
        tail = sum(comb(n, k) for k in range(b, n + 1))
        return tail / (2**n)
    # Normal approximation (continuity-corrected).
    z = (b - c - (1 if b > c else -1)) / math.sqrt(n)
    return 0.5 * math.erfc(z / math.sqrt(2))
```

And `src/dendra/gates.py:245-301` (`McNemarGate.evaluate`) compares this
one-sided p directly against `self._alpha` (default 0.01) with no
factor-of-2 conversion.

**The discrepancy.** Two-sided p = 2 × one-sided p (when `b > c`). So:
- Paper algorithm: rejects when `2 × tail < α`, i.e., `tail < α/2`.
- Code: rejects when `tail < α`.

The code is **strictly more permissive** (rejects ~2× more often)
than what the §3.2 algorithm description says. The deployed gate at
α=0.01 (one-sided) is operationally equivalent to a two-sided test at
α=0.02 (the value the paper's algorithm describes as "advance iff
two-sided p < α", with α=0.02 not the documented 0.01).

**Implication for Theorem 1.** Both interpretations satisfy the
Type-I bound ≤ α stated in the safety theorem — the one-sided
binomial test at level α has marginal size α (uniform-under-null);
the paper's two-sided + directional-b > c test has marginal size ≤ α/2.
So the safety guarantee holds for the deployed gate. But **the paper's
stated algorithm is not the algorithm the code runs**, and a reviewer
reading §3.2 and then reading `viz.py::mcnemar_p` will catch the
mismatch.

**Where this surfaces empirically.** Table 3's "First clear" values
appear to be computed with two-sided p (matching the paper algorithm),
based on:
- codelangs first clear: two-sided p first crosses α at outcomes=400
  (p=0.0044). One-sided p first crosses α at outcomes=300 (p=0.0059).
  Table 3 reports **400**, matching two-sided.
- `paired_mcnemar_summary.json` stores `trans_p = 0.001953` for HWU64
  at outcomes=250, which is exactly `2 × 0.000977` (one-sided) — i.e.,
  two-sided.
- Table 8 (CIFAR-10) p-values match two-sided computation exactly
  (e.g., 250 outcomes: paper p=7.0e-4, two-sided p=6.98e-4,
  one-sided p=3.49e-4).

**Why this matters.** This is a load-bearing algorithm description in
§3 — the very gate the safety theorem proves bounds on. A reader
implementing the §3.2 algorithm from the paper alone will write a
strictly different gate than the one Dendra ships.

**Suggested resolution.** Either:
- (a) update `viz.py::mcnemar_p` and `gates.py::McNemarGate` to use
  two-sided p (with the explicit b > c check), matching the paper —
  cleanest, but a production-default change;
- (b) update §3.2's algorithm description to match the deployed gate
  (one-sided exact binomial; the directional condition is implicit
  in p < 0.5);
- (c) keep both, but add a footnote in §3.2 explicitly noting that the
  reference implementation uses the equivalent one-sided form. This is
  the minimal-disruption option.

The Theorem 1 proof should also be re-stated under whichever convention
is chosen (the "min(b,c) = b in expectation" phrasing on line 536 is
loose under either convention; see also **V2-S2-8**).

---

### V2-S1-4. Tzamos & Zarifis citation has the wrong author list

**Paper location.** `body.typ:599-602` (§3.4), `body.typ:1916-1918`
(§10.6), bibliography entry at `body.typ:2139` (pre-fix; now
`body.typ:2073` post-fix).

**V1 claimed this verified clean.** V1 line 455-457 of
`ACCURACY_REVIEW-2026-05-11.md`:

> "Tzamos & Zarifis (2024) NeurIPS Spotlight: real paper, confirmed
> via OpenReview + NeurIPS 2024 listing ✓"

**Source of truth.** OpenReview record for the paper "Active
Classification with Few Queries under Misspecification"
(`openreview.net/forum?id=Ma0993KZlq`) lists the authors as:

> **Vasilis Kontonis, Mingchen Ma, Christos Tzamos**

Zarifis is **not** an author. The paper exists and the venue is correct
(NeurIPS 2024 Spotlight), but the author list as cited in three places
in the paper is wrong.

**Mechanical fix applied.** All three prose mentions
("Tzamos and Zarifis's") and the bibliography entry have been updated
to "Kontonis, Ma, and Tzamos" / "Kontonis, V., Ma, M., & Tzamos, C.
(2024)". This is a citation-accuracy correction (analogous to V1's
`LLMCommitteeSource` → `JudgeCommittee` suggested fix), not a
substantive content change.

The bibliography entry has been moved to the alphabetically correct
position (after Karpathy, before Kuleshov).

**Why this matters.** A reviewer (especially anyone in the active-
learning subfield who knows Christos Tzamos's collaborators) will
immediately flag the wrong author list, and the citation form
"Tzamos and Zarifis" (two-author short cite) doesn't even match the
"X et al." or "first-author et al." convention the rest of the
bibliography uses. The error is doubly visible.

**This is a re-verification miss in V1.** V1 confirmed the paper
existed but did not check the author list. Flagging here so the
methodology of "verified clean" is tightened for V3 or later passes:
always check **first author and last author** at minimum on every
citation.

---

### V2-S1-5. Appendix B reproducibility command does NOT reproduce Table 3 by default

**Paper location.** `body.typ:2184-2185`:

> ✓ Benchmark harness reproduces the result:
> `dendra bench {atis,banking77,clinc150,hwu64,snips,trec6,ag_news,codelangs}`.

**Source of truth.** `src/dendra/cli.py:1735-1746` (the `--no-shuffle`
flag definition) and `src/dendra/benchmarks/rules.py:77-115`
(`build_reference_rule`'s `shuffle` parameter).

The CLI flag's own help text says it all:

```
--no-shuffle    Disable the deterministic shuffle of the training stream
                before the seed window is taken. The default shuffles with
                seed 0 so label-sorted upstream splits (Banking77, HWU64,
                CLINC150, Snips on HuggingFace) cannot collapse the rule
                to a single label. Pass --no-shuffle to reproduce the v0.x
                paper-as-shipped behavior.
```

And `build_reference_rule`'s docstring (rules.py:99-106):

> "When True (default), the training stream is shuffled with a
> deterministic random.Random(shuffle_seed) before slicing the seed
> window. ... Pass shuffle=False to reproduce the v0.x paper-as-
> shipped behavior (the auto-rule then degenerates to predict-the-
> modal-class on Banking77, HWU64, CLINC150, and Snips)."

**The discrepancy.** Table 3 reports the **as-shipped HuggingFace
split** behavior — Banking77 rule at 1.3% (modal class), HWU64 at
1.8%, etc. To reproduce these, you must pass `--no-shuffle`. The
documented Appendix B command produces **different** rules (the
shuffled-seed-window versions, which on the high-cardinality
benchmarks are much higher accuracy per Table 4b).

So a reviewer who runs

```bash
dendra bench banking77
```

will see Rule acc ≈ 24% (the shuffle-recovered version), **not** the
1.3% the paper reports in Table 3. They will conclude either (a) the
paper's numbers are wrong, or (b) the reproducibility instructions are
incomplete. Both undermine trust.

**Suggested resolution.** Update Appendix B to specify the flag:

```
✓ Benchmark harness reproduces Table 3 (as-shipped HuggingFace splits):
  `dendra bench --no-shuffle {atis,banking77,clinc150,hwu64,snips,
  trec6,ag_news,codelangs}`.
```

Or, equivalently, change the CLI default to `shuffle=False` (then add
documentation in Appendix B for how to reproduce Table 4b with
`--shuffle-seed=N` instead). The v0.x default was no-shuffle per the
help string; v1.0 changed the default; the paper's Appendix B was not
updated to match.

**Why this matters.** This is the headline reproducibility command;
it's the answer to "how do I run your experiment myself?". Getting
this wrong is the kind of thing that gets paper retractions on
arXiv-style platforms where reviewers run the code.

---

### V2-S1-6. Two tables both labeled "Table 1"

**Paper location.** `body.typ:330` and `body.typ:387`.

- Line 330: "Table 1. Head-to-head feature comparison on a
  Banking77-shape site ..."
- Line 387: "Table 1. The six-phase graduated-autonomy lifecycle."

Two consecutive tables, both numbered "Table 1". Section 3.1 line 354
("the routing logic at each phase is shown in Table 1 below") refers
to the lifecycle table; Section 2's head-to-head table also says
"Table 1". Reader ambiguity is total.

Subsequent tables are numbered 2 (Benchmarks), 3 (Headline), 4 (Seed
sensitivity), 4b (Shuffle), 5 (Paired vs unpaired), 6 (LLM probe),
7 (Autoresearch), 8 (CIFAR-10). So renumbering "Table 1 (head-to-
head)" to "Table 1" and "Table 1 (lifecycle)" to "Table 2" would
require shifting every downstream table number by 1 — a
non-mechanical change that touches every numbered cross-reference.

**Suggested resolution (recommend option 1).**

1. **Demote one of them to a figure** (e.g., "Figure 0. The six-phase
   graduated-autonomy lifecycle" or leave it unnumbered). This is the
   lowest-disruption fix: the lifecycle table is small and could
   reasonably be presented as a labeled inline block without a
   formal table number.
2. **Renumber every downstream Table N → N+1**: Head-to-head → Table 1,
   Lifecycle → Table 2, Benchmarks → Table 3, ..., CIFAR-10 → Table 9.
   This is the conventional fix but touches every cross-reference
   throughout the paper (§5.1, §6, §11, etc.).
3. **Re-letter the second one as Table 1b** (parallel to Table 4b
   convention). Lowest-disruption but unusual.

I am not applying a mechanical fix here because the choice
substantively affects how readers cross-reference the paper. Flagging
for human resolution.

**Why this matters.** A reviewer who can't tell which "Table 1" the
prose is referring to will rightly question editorial oversight.

---

### V2-S1-7. Abstract overclaims rule baseline as universally "at chance accuracy"

**Paper location.** `body.typ:8-9`:

> "even when the day-zero rule is a 100-example keyword auto-build at
> chance accuracy"

**Source of truth.** Table 3 rule accuracies, in descending order:
- codelangs: 87.8% (chance = 8.3%) — **decisively above chance**
- ATIS: 70.0% (chance = 3.8%) — **decisively above chance**
- TREC-6: 43.0% (chance = 16.7%) — **above chance**
- AG News: 25.9% (chance = 25.0%) — at chance
- Snips: 14.3% (chance = 14.3%) — at chance
- HWU64: 1.8% (chance = 1.56%) — near chance
- Banking77: 1.3% (chance = 1.30%) — at chance
- CLINC150: 0.5% (chance = 0.66%) — near chance

The abstract's "even when the day-zero rule is ... at chance accuracy"
applies to **4 of 8** benchmarks (HWU64, Banking77, CLINC150, Snips),
maybe 5 of 8 if AG News counts. It does **not** apply to codelangs,
ATIS, or TREC-6.

**Why this matters.** The abstract's headline framing implies the
McNemar gate fires within 250 to 500 outcomes **on every benchmark
including the chance-baseline ones**. But on 3 of the 8 (codelangs,
ATIS, TREC-6), the rule starts well above chance, and the gate fires
specifically *because* the rule was a usable but not optimal floor.
The "even when... at chance accuracy" phrasing collapses both regimes
into one narrative, which a careful reviewer reading §5.2 (where
Regimes I, II, III are explicitly distinguished) will catch as
oversimplification.

**Suggested rewrite.** "even when the day-zero rule on the
high-cardinality benchmarks (Banking77, HWU64, CLINC150) is a
100-example keyword auto-build that reduces to chance-accuracy
modal-fallback on the as-shipped split". Longer, but accurate.

---

## Additional should-fix findings

### V2-S2-1. §10.3 `SklearnTextHead` p50 claim cites a test that doesn't measure SklearnTextHead

**Paper location.** `body.typ:1773-1774`:

> "the reference implementation's `SklearnTextHead` shows p50 predict
> time under 2 ms (`tests/test_latency.py`)"

**V1's S2-2** flagged that "under 2 ms" is the test's *assertion
ceiling*, not the measured value. **This pass surfaces a second issue
on top**: the cited test, `tests/test_latency.py::test_ml_head_
submillisecond` (lines 125-138), uses `_FakeFastMLHead`, **not**
`SklearnTextHead`. From the test source:

```python
class _FakeFastMLHead:
    """Synthetic ML head that takes ~200 µs per predict (realistic for
    TF-IDF + LogReg at this vocab size). Deterministic for timing."""

    def predict(self, input, labels):
        _ = sum(ord(c) for c in str(input)[:200])  # synthetic delay
        return MLPrediction(label="flight", confidence=0.92)
```

So `tests/test_latency.py` doesn't actually time `SklearnTextHead`. It
times a hand-rolled stand-in. The paper's cite is inaccurate on top of
V1's "assertion-vs-measurement" finding.

**Why this matters.** A reviewer who opens the cited test to verify
finds (a) the assertion bound is 2 ms not the measurement, AND (b) the
test isn't even on the named class. Two strikes from one citation.

**Suggested resolution.** Either:
- Cite `docs/benchmarks/perf-baselines-2026-05-01.md` instead (which
  has the "ML head alone (synthetic) | 1.79 µs" cell), with a footnote
  that the measurement uses a synthetic stand-in for TF-IDF + LR; **or**
- Add a real `tests/test_latency.py::test_sklearn_text_head_latency`
  that times the actual `SklearnTextHead` with realistic input and
  cite that.

---

### V2-S2-2. §10.3 claims 1-5 µs for both ML_WITH_FALLBACK and ML_PRIMARY; test only covers ML_WITH_FALLBACK

**Paper location.** `body.typ:1774-1776`:

> "switch overhead at `ML_WITH_FALLBACK` and `ML_PRIMARY` is on the
> order of 1--5 µs (`tests/test_latency_pinned.py`)."

**Source of truth.** `tests/test_latency_pinned.py` lines 191-199:

```python
def test_phase_ml_with_fallback_overhead_under_5us(self):
    sw = LearnedSwitch(
        ...
        starting_phase=Phase.ML_WITH_FALLBACK,
        ...
    )
    ...
    _assert_p99_below(stats, 5_000, cell="classify.ML_WITH_FALLBACK.auto_record=False")
```

The pinned-latency test covers **only** `ML_WITH_FALLBACK`, not
`ML_PRIMARY`. There is no `test_phase_ml_primary_overhead_*`. The paper
claim "1–5 µs for both" extrapolates from the one measured phase to the
other unmeasured phase.

**Why this matters.** Reviewers who want to verify the claim by reading
the cited test file won't find ML_PRIMARY there. Either add the test or
narrow the prose to what's actually measured.

**Suggested rewrite.** "switch overhead at `ML_WITH_FALLBACK` is on
the order of 1–5 µs (`tests/test_latency_pinned.py`); `ML_PRIMARY`
removes the predecessor-cascade fallback and is structurally faster."
(Or add the missing test.)

---

### V2-S2-3. RouteLLM cost claim doesn't match the published number

**Paper location.** `body.typ:170-172`:

> "RouteLLM (Ong et al., 2024) extended cascading to learned routing
> from preference data, recovering 95% of GPT-4 quality at 15% of the
> cost on MT-Bench."

**Source of truth.** The lmsys.org RouteLLM blog post (the canonical
companion to the arXiv paper) reports:

> "With LLM judge augmentation, the matrix factorization router
> achieved 95% of GPT-4 performance using **14% of total calls**."

So the actual published number is **14%**, not 15%. Off by one
percentage point.

**Why this matters.** A reviewer who knows the RouteLLM result will
flag this as either careless or willfully softening (the "15%" is
slightly less impressive than "14%", which makes Dendra's
post-graduation cost story relatively stronger). Either way, it's a
fixable inaccuracy.

**Suggested rewrite.** "recovering 95% of GPT-4 quality at 14% of the
cost on MT-Bench" (with the standard caveats about which augmentation
condition).

---

### V2-S2-4. §1.3 contribution 3 omits AG News from the third regime; §5.2 and §6 include it

**Paper location.** `body.typ:126-130`.

§1.3's contribution-3 names the three regimes:
- "rule near-optimum (codelangs)" — 1
- "rule usable (ATIS, TREC-6)" — 2
- "rule modal-fallback under sorted splits (Banking77, HWU64,
  CLINC150, Snips)" — 4

That's 1 + 2 + 4 = 7 of 8. AG News is unaccounted for.

But §5.2 line 832-834 places AG News in "Regime III ... when the
as-shipped split is sorted by label". And §6's taxonomy matrix
(`body.typ:1273-1279`) places AG News in the "Weak (≈ chance)"
column. So AG News belongs in the third regime per §5.2 and §6, but
§1.3 doesn't include it.

**Suggested rewrite.** Add AG News to the third-regime parenthetical
in §1.3: "rule modal-fallback under sorted splits (Banking77, HWU64,
CLINC150, Snips, AG News)".

---

### V2-S2-5. §6 "Five attribute dimensions" listed seven (FIXED mechanically)

**Paper location.** `body.typ:1239`:

> "Five attribute dimensions are available without training:"

But the bulleted list that follows has **seven** items:
1. Label cardinality
2. Rule keyword affinity
3. Distribution stability
4. Verdict latency
5. Verdict quality
6. Feature dimensionality
7. Input length

**Mechanical fix applied.** Changed "Five" → "Seven" — a pure typo,
no semantic content.

---

### V2-S2-6. §10.1 "fires automatically every 250 outcomes" conflates benchmark cadence with production default

**Paper location.** `body.typ:1708-1710`:

> "After this work, it is a paired-McNemar gate evaluation that fires
> automatically every 250 outcomes once n_min = 200 paired outcomes
> accumulate."

**Source of truth.** The production `LearnedSwitch` default in
`src/dendra/core.py:392`:

```python
auto_advance_interval: int = 500
```

The default for `auto_advance_interval` is **500**, not 250. The 250
figure is `research.py::run_benchmark_experiment`'s
`checkpoint_every=250` default, which is the experimental harness's
cadence — not the production switch's.

**Why this matters.** A practitioner reading §10.1 expects the
out-of-the-box default to fire every 250 outcomes. It actually fires
every 500. Off by 2×.

**Suggested rewrite.** "fires automatically at a configurable cadence
(default every 500 outcomes; the experimental harness in §5 uses 250)
once n_min = 200 paired outcomes accumulate."

---

### V2-S2-7. §10.4 "MODEL_PRIMARY structurally identical to FrugalGPT cascade" reverses the cascade direction

**Paper location.** `body.typ:1843-1847`:

> "a `MODEL_PRIMARY` phase is structurally identical to a FrugalGPT
> cascade with two stages (rule + LLM), and the P_4 → P_5 transition
> is the moment when the cascade's escalation tier becomes
> unnecessary."

**Source of truth.** Two structural facts that disagree:

- **FrugalGPT cascade** (Chen et al., 2024): "weakest-model-first,
  escalate-on-low-confidence". Small/cheap model runs first; the
  expensive model is the escalation tier.
- **Dendra MODEL_PRIMARY** (paper Table 1 at `body.typ:374`):
  "M(x) if conf_M ≥ θ; else R(x)". The **LLM (M)** runs first; the
  **rule (R)** is the fallback on low LLM confidence.

These are reverse cascades:
- FrugalGPT: cheap → expensive on uncertainty.
- Dendra at MODEL_PRIMARY: expensive (LLM) → cheap (rule) on
  uncertainty.

The "structurally identical" claim collapses this difference.

**Why this matters.** A reviewer in the cascade-routing literature
will catch the inversion. The substantive claim about P_4 → P_5
(graduating off the LLM tier) is independent and still valid.

**Suggested rewrite.** "a `MODEL_PRIMARY` phase is structurally
analogous to a two-stage cascade in the FrugalGPT lineage (LLM
classifier with rule fallback; the direction is reversed from
FrugalGPT's cheap-first cascade, but both share the
escalation-on-low-confidence pattern), and the P_4 → P_5 transition
is the moment when the LLM tier becomes unnecessary."

---

### V2-S2-8. §3.3 proof sketch has muddled phrasing around "min(b,c) = b in expectation"

**Paper location.** `body.typ:531-541`:

> "If B is no better than A in true accuracy on X, the distribution of
> discordant pairs satisfies E[b] ≤ E[c], so min(b, c) = b in
> expectation."

**The mathematical issue.** Under the null (B's true accuracy = A's),
E[b] = E[c] exactly; min(b,c) is variable but symmetric. Under
"B strictly worse than A", E[b] < E[c], so b ≤ c in expectation —
but the rejection rule fires when b > c (the directional condition),
which is exactly when the in-expectation inequality is **violated**.

The proof's "min(b, c) = b in expectation" is informally trying to
say "in the typical sample, b is the smaller one when B is no better"
— which is true but doesn't directly support the rejection-probability
bound that follows. The actual logic is:
- Conditional on b > c (the directional event), the marginal
  probability under null is 1/2 (symmetric).
- Conditional on b > c AND b ≥ some threshold k, the marginal
  probability is bounded by the one-sided binomial CDF.
- The rejection rule sets k such that this conditional probability is
  ≤ α.

This is more standard one-sided binomial test reasoning. The proof
sketch's phrasing is unconventional and could confuse a careful
reader.

**Why this matters.** Theorem 1 is one of the paper's three named
formal contributions. The proof sketch as written is *correct in
conclusion* but the intermediate step ("min(b,c) = b in expectation
... so the rejection rule rejects when ... falls below α") is hard to
follow and a reviewer may push back.

**Suggested resolution.** Replace with a more standard size-of-test
statement: "Under the null hypothesis E[b]=E[c], the test statistic
min(b,c) is symmetric in b and c, so the directional condition b > c
holds with probability ≤ 1/2. Conditional on b > c, the one-sided
exact binomial tail P(X ≥ b | n_paired, 0.5) is uniformly distributed
on its rejection region, so P(reject | null) ≤ α/2 × 1 + α/2 × 0 = α."

(Caveat: this rewrite assumes the deployed gate's one-sided
formulation; under the paper's stated two-sided algorithm, the
α/2 × 2 = α arithmetic works the same way.)

---

### V2-S2-9. §5.7 Table 8 uses two-sided p; §3.2 algorithm matches; production code differs

**Paper location.** `body.typ:1207-1213` (Table 8 + caption).

**Source of truth.** Re-deriving p-values from
`results/cifar10_paired.jsonl`:

| outcomes | b | c | p_one_sided | p_two_sided | Table 8 |
|---:|---:|---:|---:|---:|---:|
| 50 | 63 | 47 | 0.0762 | 0.152 | 0.15 ✓ (two-sided) |
| 100 | 72 | 48 | 0.0177 | 0.0353 | 0.035 ✓ (two-sided) |
| 250 | 95 | 53 | 0.000349 | 0.000698 | 7.0e-4 ✓ (two-sided) |
| 500 | 90 | 56 | 0.00306 | 0.00612 | 6.1e-3 ✓ (two-sided) |
| 1000 | 106 | 55 | 3.57e-5 | 7.15e-5 | 7.1e-5 ✓ (two-sided) |
| 2000 | 99 | 55 | 0.000245 | 0.00049 | 4.9e-4 ✓ (two-sided) |
| 4000 | 113 | 53 | 1.85e-6 | 3.7e-6 | 3.7e-6 ✓ (two-sided) |

So Table 8 is internally consistent with the §3.2 algorithm
description (two-sided). But the production gate (`viz.py::mcnemar_p`)
uses one-sided. See **V2-S1-3** for the deeper algorithm-vs-code issue.

**Subordinate to V2-S1-3.** If V2-S1-3 is resolved by aligning code to
paper (option a), this becomes moot. If resolved by aligning paper to
code (option b), Table 8's p-values need to be re-rendered as
one-sided (everything halves). If resolved by a footnote (option c),
Table 8 needs a note clarifying which convention is used.

---

### V2-S2-10. Yang et al. (2023) cited inline without author names

**Paper location.** `body.typ:197-198`:

> "Recent extensions (e.g., Block-regularized 5×2 cross-validated
> McNemar, 2023) further refine the methodology for cross-validated
> evaluation."

**Source of truth.** Bibliography line 2153 (post-fix; numbers shift
after the Kontonis move):

> "Yang, J., Wang, R., Song, Y., & Li, J. (2023). Block-regularized
> 5×2 Cross-validated McNemar's Test for Comparing Two Classification
> Algorithms. arXiv:2304.03990."

The inline citation gives only the work's title, not author names. A
reader cannot match the inline form to the bibliography by author —
only by remembering "Block-regularized 5×2" as a search string.

**Suggested rewrite.** "Yang et al. (2023, Block-regularized 5×2
cross-validated McNemar)" — matches the author-year convention used
elsewhere in §2.

---

### V2-S2-11. Table 3 footnote "equals chance (1/k)" is approximate, not exact

**Paper location.** `body.typ:761-762`:

> "Their `Rule acc` column equals chance (1/k)."

For Banking77 (1/77 = 1.30%) and Snips (1/7 = 14.29%), this is
exact. For HWU64 (1/64 = 1.56%) the reported rule is 1.8%; for
CLINC150 (1/151 = 0.66%) the reported rule is 0.5%. Neither equals
1/k exactly — they reflect the modal-class prevalence on the
specific test split, which is close to but not exactly uniform.

**Why this matters.** The claim is a rounded simplification. A
reviewer with calculator in hand notices the rounding. Two of the
four rows don't match the formula the footnote states.

**Suggested rewrite.** "Their `Rule acc` column reflects the
modal-class prevalence on the test split (≈1/k for uniform-class
benchmarks; slightly higher when the modal class is over-represented
in the test set)."

---

### V2-S2-12. JudgeSource bias guardrail only fires when `require_distinct_from=` is explicitly passed

**Paper location.** `body.typ:1572-1574`:

> "The same-LLM-as-classifier-and-judge bias is enforced at
> construction time: the `JudgeSource` constructor refuses a judge
> model that resolves to the same identity as the classifier"

**Source of truth.** `src/dendra/verdicts.py:182-211`:

```python
def __init__(
    self,
    judge_model: ModelClassifier,
    *,
    require_distinct_from: ModelClassifier | None = None,
    guard_against_same_llm: bool = True,
    prompt_template: str | None = None,
) -> None:
    ...
    if (
        guard_against_same_llm
        and require_distinct_from is not None
        and _same_model(judge_model, require_distinct_from)
    ):
        raise ValueError(...)
```

The guardrail only fires when the caller **explicitly passes**
`require_distinct_from=`. Plain `JudgeSource(judge_model=m)` (without
specifying what to be distinct from) does not refuse anything.

**Why this matters.** Paper reads as "automatic guardrail"; code is
"opt-in guardrail with explicit ground-truth comparator". An auditor
who wires `JudgeSource(judge_model=GPT4Adapter())` and the classifier
is also GPT-4 would expect refusal per the paper — and not get it.

**Suggested rewrite.** "the `JudgeSource` constructor refuses a judge
model that resolves to the same identity as a caller-supplied
`require_distinct_from=` reference classifier; the `default_verifier()`
factory wires this comparator automatically." (Verify the second
clause against `default_verifier()` before publishing.)

---

### V2-S2-13. §5.3 "Five of six benchmarks" — TREC-6 wasn't at chance in the first place

**Paper location.** `body.typ:957-960`:

> "Ten random shuffles per benchmark, seed=100. Five of six benchmarks
> where the as-shipped rule was at chance recover meaningfully under
> shuffling; Snips recovers from 14.3% to 75.3%."

Table 4b has six rows: HWU64, Banking77, CLINC150, Snips, AG News,
TREC-6. But TREC-6's as-shipped rule was 43.0% (vs chance 16.7% =
1/6) — **not** at chance. The "five of six benchmarks where the
as-shipped rule was at chance" framing implicitly says six benchmarks
were at chance, when only five (or four, by stricter interpretation)
were.

**Suggested rewrite.** "Ten random shuffles per benchmark, seed=100.
Of the five at-chance baselines (HWU64, Banking77, CLINC150, Snips,
AG News), five recover meaningfully under shuffling; Snips recovers
from 14.3% to 75.3%. TREC-6 (rule at 43.0%, above chance) is included
as a control."

---

### V2-S2-14. Dekoninck et al. citation date inconsistency

**Paper location.** `body.typ:172-174` (§2) cites "Dekoninck et al.,
2025"; bibliography line 2040 lists it as 2025 (ICML 2025). But arXiv
submission was October 2024 (arXiv:2410.10347). Both years are
defensible (submission vs publication), but the paper uses 2025
consistently. **Verified clean** in V1's audit — flagging only as a
note that "2024" is also defensible if a future revision wants to
match the arXiv year.

No change suggested; this is a categorization note, not a finding.

---

### V2-S2-15. `multi-seed-README.md` is stale on CLI capability

**Paper location.** Not in the paper directly; the README is
referenced as the multi-seed-pending status doc.

**Source of truth.**
`docs/papers/2026-when-should-a-rule-learn/results/multi-seed-README.md`
lines 20-21:

> "TODO: wire shuffle_seed through the CLI; currently requires
> Python-level invocation of run_benchmark_experiment."

But `src/dendra/cli.py:1747-1756` does ship a `--shuffle-seed`
flag (plus `--no-shuffle`). The README's TODO was completed; the
README was not updated.

**Why this matters.** Anyone trying to regenerate Table 4b reads the
README, follows the Python-snippet workflow, and never discovers the
simpler CLI path. The actual reproducibility surface is better than
the README documents.

**Suggested fix.** Update the README to:

```bash
for SEED in 1 2 3 4 5; do
  for BENCH in atis hwu64 banking77 clinc150; do
    dendra bench $BENCH --shuffle-seed $SEED > ${BENCH}_seed${SEED}.jsonl
  done
done
```

(Not applied here because the README is internal documentation, not
the shipping paper artifact — flagging for human cleanup.)

---

## V1 "verified clean" items I re-checked

| V1 claim | V2 verdict |
|---|---|
| Tzamos & Zarifis (2024) NeurIPS Spotlight: real paper, confirmed | **Now flagged (V2-S1-4)**. Paper is real, but the author list is wrong: actual authors are Kontonis, Ma, Tzamos. Zarifis is not an author. Mechanical fix applied to all 3 prose sites + bibliography. |
| Karpathy autoresearch (2026): confirmed | Still clean. Re-verified via github.com/karpathy/autoresearch. |
| Papailiopoulos et al. (2025) ReJump arXiv:2512.00831: real paper | Did not re-check (V1's separate finding S2-5 already flags the author-citation form). |
| Table 8 (CIFAR-10) cells: every cell matches `cifar10_paired.jsonl` | Re-derived p-values from rule_correct/ml_correct arrays at every checkpoint. Two-sided p-values match Table 8 to within rounding. **But** §5.7 prose says "1000 train rows + 200 test rows" when the data file's summary says 4000 train + 500 test (**V2-S1-1, new finding**). |
| Snips outcomes 1 through ~1,842 are all AddToPlaylist | Re-verified by `from dendra.benchmarks import load_snips; ds = load_snips()`. First class change at index 1842 exactly. ✓ |
| paired_mcnemar_summary.json: ATIS / HWU64 / Banking77 / CLINC150 match | Re-verified. **But**: `trans_p` field is two-sided p (e.g., HWU64 trans_p=0.001953 = 2 × one-sided 0.000977), so this artifact uses the paper's algorithm convention, not the deployed gate's. See **V2-S1-3**. |
| Table 4 seed=1000 rule accuracies | Re-verified. ✓ |
| Table 7 autoresearch winners and margins | Re-verified all 8 rows from `results/autoresearch-mlhead-*.json`. All match. ✓ |
| Latency claims §10.3 "1–5 µs" for ML_WITH_FALLBACK / ML_PRIMARY | Partial. Test `tests/test_latency_pinned.py` covers ML_WITH_FALLBACK only; ML_PRIMARY has no corresponding test (**V2-S2-2, new finding**). |
| `tests/test_latency.py`'s `SklearnTextHead` p50 claim | **Now flagged twice**. V1's S2-2 caught the assertion-vs-measurement. V2 additionally finds the test uses `_FakeFastMLHead`, not `SklearnTextHead` (**V2-S2-1**). |
| All implementation references in §9 resolve | Re-verified. ✓ All resolve. |
| arXiv ID format check | Re-verified two arXiv IDs by direct WebFetch (`2410.10347` Dekoninck → real; `2304.03990` Yang → real). |
| 0 overpromise candidates flagged from scan list | Re-ran a wider scan with terms not on V1's list ("first to", "the only", "decisively", "structurally identical", "substantially"). Found mostly bounded uses, but two soft overclaims: §10.4's "structurally identical to FrugalGPT cascade" (**V2-S2-7**), and the abstract's universal "at chance accuracy" framing (**V2-S1-7**). |
| Typst build warning-free | Re-confirmed after mechanical fixes. `typst compile paper.typ paper.pdf` produces zero warnings. ✓ |

---

## Mechanical fixes applied in this PR

1. `body.typ:1239`: "Five attribute dimensions" → "Seven attribute
   dimensions" (pure typo; the list immediately following has seven
   bullets). See **V2-S2-5**.
2. `body.typ:599-602, 1916-1918`: "Tzamos and Zarifis" → "Kontonis,
   Ma, and Tzamos" (citation-accuracy fix; correct authors per
   OpenReview record). See **V2-S1-4**.
3. `body.typ:2073` (was 2139): bibliography entry updated to
   "Kontonis, V., Ma, M., & Tzamos, C. (2024)" and moved to the
   alphabetically correct position (after Karpathy, before
   Kuleshov).

No other mechanical fixes applied. Every other finding above is
substantive and requires human judgment, or is a numerical-claim
change which the V2 fix rules prohibit applying automatically.

---

## Sign-off

Reviewed by Claude Opus 4.7 (1M context) on 2026-05-11 as a second
adversarial pass complementing the V1 review of the same day.

**Methodology footprint.** Roughly 60 numerical claims chased to data
files (every Table 3 / 4 / 4b / 5 / 6 / 7 / 8 cell plus every abstract
and §1.3 / §5 / §10 number). 38 backtick-quoted symbols searched
against `src/dendra/` (all resolve except the two V1-flagged phantoms).
Algorithm-vs-code mismatch on the McNemar gate's p-value convention
re-derived from `viz.py::mcnemar_p` and verified against
`cifar10_paired.jsonl` and `paired_mcnemar_summary.json`. Five
external citations spot-checked via WebFetch (Tzamos/Zarifis →
**caught**; RouteLLM 15% → **caught**; Dekoninck → clean; Yang → clean;
Karpathy → clean).

**Totals.** 7 new must-fix items (V2-S1-1 through V2-S1-7), 15 new
should-fix items (V2-S2-1 through V2-S2-15), 1 V1 "verified clean"
item now flagged (Tzamos & Zarifis author list — wrong, mechanical
fix applied).

**The highest-priority new items**, ranked:

1. **V2-S1-2** (§9.1 code example doesn't match the actual
   `record_verdict` signature) — the headline example breaks on
   copy-paste; any external reader who tries the example reports an
   issue immediately.
2. **V2-S1-5** (Appendix B reproducibility command doesn't reproduce
   Table 3 by default; missing `--no-shuffle` flag) — a reviewer who
   runs the cited command will see *different* numbers from Table 3.
3. **V2-S1-1** (§5.7 CIFAR-10 row counts — 1000+200 in prose vs
   4000+500 in data) — Table 8's curve extends to 4,000 outcomes,
   mathematically incompatible with the prose's 1,000-row training
   claim.
4. **V2-S1-3** (§3.2 algorithm two-sided vs code one-sided) — the
   paper's described gate differs from the deployed gate. Safety
   theorem still holds, but the algorithm reader-can-reimplement is
   a different test than the one Dendra ships.
5. **V2-S1-4** (Tzamos & Zarifis author list wrong — already fixed
   mechanically). V1 mis-verified this; flagging the
   verification-methodology gap.

The single highest-priority **process** finding: V1's "verified clean"
list should be tightened to always sanity-check first author **and**
last author against the canonical record (OpenReview / arXiv / DOI),
not just the title and venue. The Tzamos/Zarifis miss demonstrates
that title + venue confirmation is insufficient.

— Claude Opus 4.7, 2026-05-11
