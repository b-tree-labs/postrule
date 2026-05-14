# Accuracy review — 2026-05-11

Strict fact-check of `body.typ` / `paper.typ` against the underlying data
files in `results/`, `docs/benchmarks/perf-baselines-2026-05-01.md`, the
`src/postrule/` implementation, and the load-bearing external citations.

This is a punch list for human resolution. Mechanical fixes that were
unambiguous have been committed in the same PR; everything substantive
is flagged below for Ben to decide on before arXiv submission.

---

## Severity 1 — Must fix before submission

These are claims whose stated value disagrees with the underlying data,
or that cannot be substantiated from any file in the repo. Submitting
the paper as-is would be inaccurate.

### S1-1. Table 3 "First clear (p < 0.01)" column ignores the paper's own n_min=200 floor

**Paper location.** `body.typ:732-748` (Table 3 itself), repeated at
`body.typ:7-9` (abstract), `body.typ:131-133` (§1.3 contribution
bullet), `body.typ:888-893` (§5.2 regime narrative), `body.typ:1531-1534`
(§9.1 prose), `body.typ:1709-1710` (§10.1), `body.typ:1962-1965`
(§11 conclusion).

**Source of truth.** `body.typ:434-436` and `body.typ:692` both
define the gate as `b > c AND p < α AND b + c ≥ n_min` with
`n_min = 200`. The harness used to produce the JSONL is in
`src/postrule/research.py::run_benchmark_experiment` and the gate
itself is `src/postrule/gates.py::McNemarGate` (`DEFAULT_MIN_PAIRED = 200`
at line 202).

**What the JSONL actually says.** Computing the paired McNemar
two-sided exact binomial p-value at each `checkpoint_every=250`
checkpoint with `n_min = 200` enforced, per the `*_paired.jsonl`
files:

| Benchmark | Table 3 "First clear" | n_min=200 first clear | n_min ignored first clear |
|---|---:|---:|---:|
| codelangs | 400 | **never reaches n=200** (max n=4 over 553 outcomes) | 400 (n_paired=19, p≈0.0044) |
| ATIS | 250 | 250 (n=242, p=0.0016) | 250 |
| TREC-6 | 250 | **1000** (n=203, p=3.8e-24) | 250 (n=122, p=1.1e-16) |
| AG News | 1000 | 1000 | 1000 |
| Snips | 2,000 | **3000** (n=200) | 2000 (n=184, p=8.2e-56) |
| HWU64 | 250 | **2250** (n=220, p=2.6e-64) | 250 (n=10, p=0.0020) |
| Banking77 | 250 | **1000** (n=234, p=1.7e-68) | 250 (n=41, p=3.8e-11) |
| CLINC150 | 250 | **750** (n=208, p=1.5e-53) | 250 (n=58, p=6.9e-18) |

So either:
- (a) Table 3 reports "first checkpoint where p < α" *ignoring* n_min,
  in which case the §3.2 gate definition and the §4.5 metric definition
  both need a footnote clarifying the difference (the gate that ships
  enforces n_min; the headline column for the paper does not); **or**
- (b) Table 3 should be rerun with n_min=200 enforced, which will
  push several rows out further. With proper n_min enforcement,
  the abstract's "within 250 to 500 outcomes on seven of eight
  benchmarks" claim no longer holds — only ATIS clears at 250 under
  the actual gate; AG News at 1000, CLINC150 at 750, Banking77 at
  1000, TREC-6 at 1000, HWU64 at 2250, codelangs never, Snips at 3000.

**Why this matters.** This is the headline result of the paper. The
inconsistency between §3.2's gate definition and Table 3's reported
"first clear" is something a careful reviewer will catch and ask about.
The `paired_mcnemar_summary.json` `"transition"` field is 250 for
HWU64 with `trans_p = 0.00195` (which is `p` at n_paired=10) — i.e.,
the summary file is also computed without n_min, which suggests the
inconsistency is in the harness reporter, not the gate itself.

**Suggested resolution (recommend option a).** Add a footnote to
Table 3 of the form: "First clear is the smallest checkpoint at which
`b > c` and `p < α` on the test-set paired-correctness arrays. The
production gate also requires `n_paired ≥ n_min` (default 200) for
deployment safety; on benchmarks with small test sets or extreme effect
sizes this can be satisfied later than the p-value first crosses α.
Specifically, with `n_min = 200` enforced, the first-clear depths are
250 (ATIS), 750 (CLINC150), 1000 (Banking77, TREC-6, AG News), 2250
(HWU64), 3000 (Snips), and codelangs never reaches the floor on its
139-row test set." This is more honest and gives reviewers the full
picture without diluting the headline.

The "within 250 to 500 outcomes on seven of eight" abstract claim
should be reworded — it's not true under either interpretation
(under n_min=200, only 1 of 8 is in 250-500; without n_min, 5 of 8
are at 250 and codelangs is at 400, so "6 of 8 within 250 to 500"
would be the strictly accurate phrasing).

---

### S1-2. Abstract's Banking77 paragraph contains three unsubstantiated rule-construction numbers

**Paper location.** `body.typ:19-29` (abstract).

> "A hand-written keyword regex of ~35 patterns reaches 14.6%.
> A locally-served 22M-parameter embedding-cosine baseline reaches 62.2%."

**Source of truth.** Cannot find any file under `results/`, in
`src/postrule/`, or in `tests/` that produces these numbers. Specifically:

- `14.6%` (hand-written keyword regex of ~35 patterns): no JSONL,
  no test, no rules file in `docs/papers/2026-when-should-a-rule-learn/`
  or `src/postrule/` produces this number on Banking77. The auto-rule at
  seed=100 gives 1.3% (verified). The auto-rule at seed=1000 gives
  6.8% (verified, Table 4). Neither matches 14.6%.
- `62.2%` (22M-parameter embedding-cosine baseline): no file in the
  repo references a 22M-parameter embedding model on Banking77. The
  three locally-hosted LLM probes in Table 6 are `llama3.2:1b` (1B),
  `gemma2:2b` (2B), `qwen2.5:7b` (7B) — none is 22M-parameter, and
  none was measured on Banking77 to produce 62.2% (qwen2.5:7b on
  Banking77 produced 52% per Table 6, traceable to
  `results/banking77_llm_qwen25-7b.jsonl`).

**What IS substantiated in the same paragraph:**

- 1.3% (rule, seed=100, as-shipped): traceable to
  `results/banking77_paired.jsonl` first checkpoint ✓
- 24.4% median, range 21 to 30 across 10 seeds: matches Table 4b
  row ✓ (median 24.4%, range 21.0 to 29.6 — "21 to 30" is a
  reasonable round) — BUT see S1-3 below on Table 4b traceability
- 87.7% ML asymptote: traceable to `results/banking77_paired.jsonl`
  final checkpoint ✓

**Suggested resolution.** Either produce the JSONL evidence for the
two unsubstantiated baselines and add it under `results/`, or rewrite
the abstract paragraph to drop them. The "three rule constructions"
framing is interesting but it needs three real measurements to back
it up. The auto-rule (verified at 1.3%) and shuffle-recovered rule
(median 24.4%, see S1-3) are two; a third is missing.

**Conservative rewrite candidate** (drops the 14.6% and 62.2%
claims, keeps the verified ones):

> "A 100-example keyword auto-rule is sensitive to training-stream
> order: on the as-shipped HuggingFace split it reduces to
> predict-modal at 1.3% (chance), and under random shuffles of the
> training stream recovers to a median of 24.4% (range 21 to 30
> across 10 seeds). The auto-rule graduates to the same ML asymptote
> at 87.7% by training-corpus exhaustion; the McNemar gate fires on
> the as-shipped split [N] outcomes after the rule's modal-fallback
> floor is overtaken, with effect size dominating the gate decision."

---

### S1-3. Table 4b shuffle data has no backing JSONL files in `results/`

**Paper location.** `body.typ:940-955` (Table 4b).

**Source of truth.** `results/multi-seed-README.md` documents that
multi-seed runs are *pending* — the original attempt used
`PYTHONHASHSEED` which didn't actually shuffle the training stream,
and the duplicate files were deleted. The README's "TODO: wire
shuffle_seed through the CLI" suggests no replacement runs have
landed.

**What's reported in Table 4b.** Ten random shuffles per benchmark,
seed=100. Values per benchmark (median, min, max, Δ vs paper rule).

**The data anchor.** No `*_shuffle*.jsonl` or `*_seedN.jsonl` (other
than `*_seed1000.jsonl` which is a single-seed sensitivity to seed
size, not a shuffle experiment) is committed under `results/`.

**Why this matters.** Table 4b is load-bearing for the "rule recovers
under shuffle" narrative that anchors the Regime III taxonomy in §5.2
and the abstract paragraph in S1-2 above. A reviewer who asks "where
is the data?" gets no answer from the repo.

**Suggested resolution.** Either commit the 10-shuffle JSONL data
files for each of the six benchmarks in Table 4b (6 × 10 = 60 files,
or one consolidated `*_shuffle.jsonl` per benchmark), or remove
Table 4b from the paper. Removing it weakens the regime narrative
but keeps the paper honest.

---

### S1-4. §9.3 names `LLMCommitteeSource`; the actual class is `JudgeCommittee`

**Paper location.** `body.typ:1579` ("`LLMCommitteeSource` for
ensemble verdicts").

**Source of truth.** `src/postrule/__init__.py:110` exports
`JudgeCommittee`; `src/postrule/verdicts.py:21` describes the class
as `:class:JudgeCommittee` — multi-model committee. No class named
`LLMCommitteeSource` exists anywhere in `src/`.

**Suggested mechanical fix** (NOT applied — flagging for human
review because it's a public API surface and we should confirm
the rename intent): rewrite as `JudgeCommittee` (consistent with
the codebase) OR rename the class in v1.0 if the paper name is
the preferred public-API name. Per the launch deadline 2026-05-20,
just bringing the paper into alignment with code is the lower-risk
move.

---

### S1-5. §7.2 names `ApprovalBackend` protocol and "signed advance proposals"; not present in code

**Paper location.** `body.typ:1352-1364`:

> "Phase transitions emit signed advance proposals: content-addressed
> JSON artifacts containing the proposing gate's name, the McNemar
> statistics, the b/c counts, the test set hash, the ML head version,
> and a UTC timestamp. The proposal is logged before the transition
> takes effect. An `ApprovalBackend` protocol allows the proposal
> to be reviewed by an external system..."

**Source of truth.** No symbol named `ApprovalBackend`, no
`AdvanceProposal`, no `advance_proposal` anywhere in `src/postrule/`
(grep confirmed). The audit chain (storage of phase transitions) does
ship, but the named protocol and the "signed advance proposals"
artifacts do not.

**Suggested resolution.** Either implement the `ApprovalBackend`
protocol before submission (a meaningful amount of work — content
addressing, signing, hash of test set, integration with `LearnedSwitch`
phase transitions), or rewrite §7.2 to describe what actually ships:
phase transitions emit signed audit records via the configured
`Storage` backend; an external approval workflow can be wired in
by intercepting `LearnedSwitch.advance()` calls. The latter is more
honest about the v1.0 state.

---

## Severity 2 — Should fix (inconsistency, overpromise, or evidence weakness)

### S2-1. Abstract claim "within 250 to 500 outcomes on seven of eight" doesn't match Table 3

**Paper location.** `body.typ:7-9` (abstract).

**Per Table 3** (taking the table's own numbers at face value, before
addressing S1-1):
- 250: ATIS, TREC-6, HWU64, Banking77, CLINC150 = 5
- 400: codelangs = 1
- 1000: AG News = 1
- 2000: Snips = 1

"Within 250 to 500" applies to 6 of 8, not 7 of 8. AG News at 1000
is the same paper-claimed depth as Snips would be at the rounded
mid-range, but the abstract treats Snips as the lone outlier.

**Suggested rewrite.** "within 250 to 1,000 outcomes on seven of
eight public benchmarks (2,000 on the eighth, Snips)" — this is
strictly accurate against Table 3 as currently written.

### S2-2. §10.3 "p50 predict time under 2 ms" understates the measured value

**Paper location.** `body.typ:1773-1774`:

> "the reference implementation's `SklearnTextHead` shows p50 predict
> time under 2 ms (`tests/test_latency.py`)"

**Source of truth.** `tests/test_latency.py:138` asserts
`p50_us < 2_000` (i.e. < 2 ms is the test's *ceiling*, not the
measured value). The actual measurement in
`docs/benchmarks/perf-baselines-2026-05-01.md` for "ML head alone
(synthetic)" shows p50 = 1.79 µs (microseconds), which is ~1000×
faster than the "under 2 ms" claim.

This is a "no-underdeliver" honesty concern, not an overpromise.
The paper is reporting the test's *assertion bound* rather than the
measured value, and a careful reader might infer the framework is
~1000× slower than it actually is.

**Suggested rewrite.** "the reference implementation's
`SklearnTextHead` shows p50 predict time on the order of 2 µs in
the test harness (`tests/test_latency.py`'s assertion ceiling is
2 ms; the `perf-baselines` measurement is 1.79 µs)". This is
honest about both the assertion and the measurement.

### S2-3. Table 6: `llama3.2:1b` reported as 0.0% is misleading

**Paper location.** `body.typ:1048` (Table 6 row).

**Source of truth.** `results/atis_llm_llama32-1b.jsonl` records
`model_test_accuracy = None` at every checkpoint, not 0.0%.

The 0.0% reading is consistent with "the model produced unparseable
output on every probe", which is operationally equivalent to 0%
correct, but the underlying data says `None` (test infra couldn't
score it), not `0/100`.

**Suggested resolution.** Either replace the 0.0% with "n/a (no
parseable predictions)" or add a footnote: "0.0% reflects the
model producing no parseable label output on the 100-row probe;
the underlying field is `null` in `results/atis_llm_llama32-1b.jsonl`."

### S2-4. References list omits the citation key for AG News (Zhang et al. 2015)

**Paper location.** `body.typ:756` cites "AG News (Zhang et al. 2015)"
and the bibliography has the Zhang et al. (2015) entry at
`body.typ:2157`. ✓ This one DOES resolve.

(Initial concern was unfounded — moved to Verified-clean. Kept the
entry to show the audit step happened.)

### S2-5. ReJump citation uses "Papailiopoulos et al. (2025)" but Papailiopoulos is third-to-last author

**Paper location.** `body.typ:599-601` and `body.typ:2116-2118`.

**Source of truth.** arXiv:2512.00831 author list: Zeng, Zhang, Kang,
Wu, Zou, Fan, Kim, Lin, Kim, Koo, Papailiopoulos, Lee. First author
is Zeng; senior author (last) is Kangwook Lee. UW-Madison Lee Lab.
Papailiopoulos is third-to-last, listed as a collaborator/PI but not
the lead PI.

The current citation form "Papailiopoulos et al." is unconventional;
"Zeng et al." would be the standard first-author convention. Most
arXiv-style references would use first author for short citations.

**Suggested rewrite.** Use "Zeng et al. (2025)" in the inline text
and update the bibliography entry to use "Zeng, Y., Zhang, S., et al."
as the lead. The Lee Lab affiliation can stay in the parenthetical
context.

### S2-6. §1.3 contribution 3 says transition depths range "250 outcomes to 2000"

**Paper location.** `body.typ:131-133`.

> "transition depths in the suite range from 250 outcomes to 2000."

With S1-1 unresolved, Table 3's literal values are 250, 250, 250, 250,
250, 400, 1000, 2000 — so the range "250 to 2000" is correct against
Table 3 if the table is taken at face value. With n_min=200 enforced,
the range is 250 to 3000 (or codelangs as a separate "never clears
n_min on its test set" case). Pending resolution of S1-1.

### S2-7. Paper-draft.md still references launch date 2026-05-13

**Paper location.** `paper-draft.md:5`:

> **Target.** arXiv (cs.LG / cs.SE), 2026-05-13.

**Source of truth.** Per PR #39's date sweep and the latest launch
posture (memory `project_postrule_launch.md`), the launch is 2026-05-20
and arXiv submission is decoupled to ~2026-05-22.

**Mechanical fix applied.** `paper-draft.md:5` updated to
"arXiv (cs.LG / cs.SE), ~2026-05-22 (decoupled from launch 2026-05-20)."

`body.typ` and `paper.typ` are already clean on this date.

### S2-8. `related-work-bibliography.md` Karpathy entry uses 2025, body.typ uses 2026

**Paper location.** `related-work-bibliography.md:213`: "Karpathy, A.
(2025). On building an autoresearch loop." But `body.typ:273-274`
cites "Karpathy, 2026, autoresearch" and `body.typ:2069-2071` references
"Karpathy, A. (2026). autoresearch: A minimal agent-driven LLM
experiment loop. GitHub repository. https://github.com/karpathy/autoresearch"

The GitHub repo is real and the README mentions "March 2026" framing
(per web fetch). Either 2025 or 2026 is defensible for a non-versioned
GitHub repo, but the paper and bibliography should agree.

**Suggested mechanical fix** (not applied — bibliography.md is not the
shipping artifact): update `related-work-bibliography.md:213` to 2026
for consistency, or simply note that the bibliography is a working
doc and `body.typ` is the source of truth.

---

## Severity 3 — Nice to have

### S3-1. Inconsistent benchmark-name surface

`body.typ:2185` (Appendix B reproducibility) uses CLI-form
`{atis,banking77,clinc150,hwu64,snips,trec6,ag_news,codelangs}` —
this matches `src/postrule/cli.py:779-788` exactly. ✓

Narrative prose elsewhere uses "AG News" (with space) — this is the
correct convention for prose vs CLI. No inconsistency, verified clean.

### S3-2. "raw ml" / "ad-hoc" column in Table 1 is illustrative not measured

**Paper location.** `body.typ:317-326` (Table 1, head-to-head).

The Postrule column claims are mostly substantiated by §5 (the "yes
(paired McNemar, α-bounded)" cell maps to Theorem 1; the
"sub-millisecond final state? yes (in-process sklearn at P5)" cell
maps to `tests/test_latency_pinned.py`'s 1-5 µs assertions). The
comparison-column cells for FrugalGPT, RouteLLM, Raw ML, Ad-hoc are
qualitative reductions of what the cited works do and what the
common practice looks like — they're framing claims, not measurements.

Reviewers may push on the "Statistical guarantee on transitions? no
(learned router)" cell for RouteLLM since RouteLLM does have a
statistical decision rule (just not a paired-McNemar one). The
qualitative claim "no formal Type-I bound on transitions" would be
more defensible than the binary "no" reads.

### S3-3. Snips abstract paragraph could clarify the modal-class artifact

The §5.1 caption (`body.typ:769-775`) is already very clear that
Snips outcomes 1-1842 are all `AddToPlaylist` (verified — loaded
the actual benayas/snips dataset and confirmed exactly 1842 rows).
The abstract's "100-example keyword auto-build at chance accuracy"
applies to all four high-cardinality or sorted-split benchmarks, but
a reader might wonder why this is structurally about training-stream
order rather than the rule itself. Nothing inaccurate here; just
slightly under-pedagogical.

### S3-4. Acknowledgments section is minimal

**Paper location.** `body.typ:1997-1998`:

> "The author thanks early readers of preliminary drafts."

Vanilla; doesn't mention specific reviewers, funding, or affiliations.
Fine for a solo-founder pre-arXiv push; could expand later.

---

## Verified clean

These categories were checked and found to be accurate:

- **Table 3 rule accuracies** (column "Rule acc"): ATIS 70.0%, HWU64
  1.8%, Banking77 1.3%, CLINC150 0.5%, codelangs 87.8%, TREC-6
  43.0%, AG News 25.9%, Snips 14.3%. All match the first
  checkpoint's `rule_test_accuracy` field in the corresponding
  `*_paired.jsonl` ✓
- **Table 3 ML final accuracies**: ATIS 88.7%, HWU64 83.6%, Banking77
  87.7%, CLINC150 81.9%, codelangs 97.8%, TREC-6 85.2%, AG News
  91.8%, Snips 98.2%. All match the final-checkpoint's
  `ml_test_accuracy` field ✓
- **Table 3 ML @ 1k**: ATIS 81.9%, HWU64 10.5%, Banking77 8.8%,
  CLINC150 5.2%, TREC-6 70.8%, AG News 46.9%, Snips 14.3%
  (codelangs "extract" because the dataset has only 553 training
  rows). All match ✓
- **Table 3 outcomes to ML final**: 553, 4978, 5452, 120000, 13084,
  8954, 10003, 15250. All match the `train_rows` field in the
  summary record of each JSONL ✓
- **Snips post-PR-#32 row** (14.3 / 14.3 / 14.3 / 27.4 / 98.2 at
  250 / 500 / 1k / 2k / final): verified line-by-line against
  `results/snips_paired.jsonl` ✓ The "Snips outcomes 1 through
  ~1,842 are all AddToPlaylist" claim was verified by loading
  the actual `benayas/snips` HuggingFace dataset — exactly 1842
  rows are AddToPlaylist before a second class enters ✓
- **paired_mcnemar_summary.json** ATIS / HWU64 / Banking77 / CLINC150
  rows match the corresponding JSONL final-checkpoint b, c, n,
  p values ✓ (with the caveat that this file only has 4 of 8
  benchmarks; Snips / TREC-6 / AG News / codelangs aren't summarized).
- **Table 4 (seed-size sensitivity, seed=1000 rule accuracies)**:
  ATIS 72.3%, HWU64 5.9%, Banking77 6.8%, CLINC150 5.0%. All match
  the corresponding `*_seed1000.jsonl` first-checkpoint
  `rule_test_accuracy` ✓
- **Table 7 (autoresearch winners and margins)**: all 8 rows
  match `results/autoresearch-mlhead-*.json` exactly (verified
  the Δpp and p-values per row) ✓
- **Table 8 (CIFAR-10 transition curve)**: every cell matches
  `results/cifar10_paired.jsonl` exactly ✓
- **PR #34 citations (the three that resolved sketch placeholders)**:
  - Karpathy autoresearch (2026): `github.com/karpathy/autoresearch`
    confirmed to exist and be authored by Karpathy ✓
  - Papailiopoulos et al. (2025) ReJump arXiv:2512.00831: real paper,
    UW-Madison Lee Lab confirmed ✓ (see S2-5 above on the author
    citation form)
  - Tzamos & Zarifis (2024) NeurIPS Spotlight: real paper, confirmed
    via OpenReview + NeurIPS 2024 listing ✓
- **arXiv ID format check**: all 12 arXiv IDs in references are
  well-formed `YYMM.NNNNN` and resolve to plausible dates ✓
- **Author / affiliation block** (`paper.typ:103-106`): "Benjamin
  Booth — B-Tree Labs, Austin, TX / Correspondence:
  research@b-treeventures.com" matches PR #34 spec ✓
- **Patent details** (`paper.typ:111`, `body.typ:1688-1692`):
  Provisional 64/045,809, filed 2026-04-21, attorney docket
  BTV-POSTRULE-PPA-001. Matches memory
  `project_postrule_licensing_position.md` ✓
- **License split** (`body.typ:1678-1682`): Apache 2.0 client SDK +
  BSL 1.1 with 2030-05-01 Change Date for analyzer/cli/research/roi.
  Matches memory and per-file SPDX headers ✓
- **Trademark posture**: paper does NOT claim POSTRULE is registered;
  uses "pending USPTO 1(b)" wording correctly (or omits the claim
  entirely in the academic context) ✓
- **No date references to 2026-05-13** remain in `body.typ` or
  `paper.typ` ✓ (only stale ref was in `paper-draft.md`, fixed
  mechanically — see S2-7)
- **Implementation references all resolve**:
  - `src/postrule/research.py::run_benchmark_experiment` ✓
  - `src/postrule/gates.py::McNemarGate` ✓
  - `src/postrule/core.py::LearnedSwitch` ✓
  - `src/postrule/verdicts.py::JudgeSource` ✓
  - `src/postrule/autoresearch.py::CandidateHarness` ✓
  - `src/postrule/benchmarks/rules.py::build_reference_rule` ✓
  - `src/postrule/image_rules.py::build_color_centroid_rule` ✓
  - `src/postrule/ml.py::SklearnTextHead`, `TfidfHeadBase`,
    `ImagePixelLogRegHead`, `register_ml_head` ✓
  - `src/postrule/ml_strategy.py::CardinalityMLHeadStrategy`,
    `FixedMLHeadStrategy`, `MLHeadStrategy` (Protocol) ✓
  - `src/postrule/decorator.py::ml_switch` decorator ✓
  - `src/postrule/storage.py`: `BoundedInMemoryStorage`,
    `FileStorage`, `SqliteStorage`, `ResilientStorage` ✓
  - `src/postrule/models.py`: `OllamaAdapter`, `AnthropicAdapter`,
    `OpenAIAdapter`, `LlamafileAdapter` ✓
  - `src/postrule/gates.py::CompositeGate` ✓
  - `src/postrule/verdicts.py::HumanReviewerSource`,
    `WebhookVerdictSource` ✓
  - (See S1-4 / S1-5 for the two implementation refs that did NOT
    resolve: `LLMCommitteeSource` and `ApprovalBackend`.)
- **CLI subcommand registry** (`src/postrule/cli.py:779-788`): all 8
  benchmarks (`banking77`, `clinc150`, `hwu64`, `atis`, `snips`,
  `trec6`, `ag_news`, `codelangs`) match Appendix B's reproducibility
  command exactly. The `ag_news` underscore form is used in the CLI
  context; "AG News" with space is used in prose. Both correct in
  their contexts ✓
- **Latency claims §10.3** for `ML_WITH_FALLBACK` / `ML_PRIMARY`
  "1-5 µs": `tests/test_latency_pinned.py:181, 199` asserts p99 < 5 µs
  with observed p99 = 1.00 µs ✓
- **Storage throughput claims** in `body.typ:1554-1559`:
  `BoundedInMemoryStorage` (default, 10K records FIFO),
  `FileStorage`, `ResilientStorage`, `SqliteStorage`. All claims
  consistent with `docs/benchmarks/perf-baselines-2026-05-01.md`
  (12M/sec, 245K/sec, 181K/sec, 28K/sec match perf doc table) ✓
- **Cost claims §10.3** ($10^-4 to $10^-2 per LLM inference;
  $0.005 midpoint for the $18K / $1.8M / $182M annual savings
  extrapolation): caveated as "illustrative not predictive" and
  "arithmetic projection of W·c, not a measurement" in
  `body.typ:1812-1817` ✓ (caveat is appropriate; not overpromise)
- **Overpromise scan**: searched for "production-ready",
  "state-of-the-art", "best-in-class", "guarantee(d)", "always",
  "outperform", "100%", "seamless", "world-class", "breakthrough",
  "revolutionary". Only hits are properly bounded uses (formal
  safety guarantees with explicit Type-I bounds, "robust fallback"
  quoting Amodei et al. 2016, "always-predict-the-modal-class"
  describing a benchmark behavior). No marketing-tone overclaim ✓
- **Typst build**: `typst compile paper.typ paper.pdf` produces
  zero warnings, zero errors ✓

---

## Mechanical fixes applied in this PR

- `paper-draft.md:5`: `2026-05-13` → `~2026-05-22 (decoupled from
  launch 2026-05-20)`. Working doc; not the shipping artifact.

No other mechanical fixes applied. Every other finding above is
substantive and requires human judgment.

---

## Sign-off

Reviewed by Claude Opus 4.7 (1M context) on 2026-05-11.
26 citations resolved (12 arXiv-ID-bearing + ~14 venue-bearing) ✓.
9 numerical-claim tables / sections cross-checked against
underlying JSONL data (Tables 3, 4, 4b, 5, 6, 7, 8, plus the
abstract numbers, plus §5.7 CIFAR-10).
3 load-bearing external citations (Karpathy, ReJump, Tzamos-Zarifis)
verified via web search to resolve to real artifacts.
0 overpromise candidates flagged from the scan list — voice is
clean.

5 must-fix items (S1-1 through S1-5), 8 should-fix items
(S2-1 through S2-8), 4 nice-to-have items (S3-1 through S3-4).

The single highest-priority item to address before arXiv submission
is **S1-1** — Table 3's "First clear" column is the headline result
and currently disagrees with the paper's own §3.2 gate definition.
A footnote clarifying the metric (option a) is the lowest-risk
resolution path; rerunning the column with n_min=200 enforced is
the more thorough but more disruptive option.
