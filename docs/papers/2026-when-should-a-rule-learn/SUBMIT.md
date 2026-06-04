# DMLR submission kit — checklist + reviewer-rebuttal prep

The artifact is review-hardened. This is everything needed to (a) submit with minimal effort and
(b) survive the review cycle. The one remaining action is the operator's: upload to DMLR.

## Pre-submission checklist
- [x] **Anonymized** — tex source has 0 hits for the author name, org, product name, home
  city, university, or API-key prefixes; system name behind `\sysname` ("our framework"),
  author byline anonymized. **DO:** eyeball the rendered `dmlr-submission/main.pdf` before upload.
- [x] **Compiles clean** (tectonic; TMLR-style package — swap official DMLR `.sty` if DMLR requires).
- [x] **No stubs/placeholders** in the draft; real intro; honest limitations + reproducibility.
- [ ] **Operator decisions before upload:** patent-disclosure timing; B-Tree authorship; whether to
  cite the (now-public) OSS repo on camera-ready vs keep "[anonymized repository]" for review.
- [ ] Upload `main.pdf` to OpenReview → DMLR; paste title + abstract; confirm "Under review" header.

## De-anonymize for camera-ready (after acceptance)
Flip in `dmlr-submission/main.tex`: `\sysname` → Postrule, `\pkgname` → postrule; restore real
`\author{…}`, the repo URL, and the patent note. (build_dmlr.py re-applies anonymization on rebuild,
so edit the built tex directly for camera-ready, or add a `--camera-ready` flag.)

## Anticipated reviewer concerns → prepared responses
1. **"Your ML is a weak/strawman baseline; the LLM wins are artifacts."** — Addressed at full
   strength: frozen MiniLM embeddings *and* a fine-tuned DistilBERT both still trail the LLM on
   short-text sentiment (sst2 0.89 vs 0.98; imdb 0.87 vs 0.96). The finding is robust to baseline
   strength. On high-cardinality intent, tuned TF-IDF+logreg remains the strongest classical tier
   (banking77 0.87 > fine-tuned 0.80), consistent with ML winning there. (§6.)
2. **"Wins are within test-set noise."** — The oracle is significance-aware (Wilson-CI tie band);
   sub-noise differences collapse to ties. CIs reported; n=400.
3. **"You only count labeled cost, not inference cost / this is just a cascade."** — Two-cost model
   is explicit (one-time labels vs perpetual inference). Latency is *measured* (rule ~200,000×
   faster than the LLM); under a real-time SLO the LLM is structurally excluded. AGA differs from
   FrugalGPT/L2D by choosing the *terminal* tier per stream and graduating *off* the LLM. (§4, §7.)
4. **"The meta-classifier doesn't work."** — Reported as an honest negative, not hidden: cross-site
   prediction is below the majority baseline at 21 datasets; we conclude per-site measurement is
   what works and a learned predictor needs a larger fleet. (§4.3.)
5. **"Multimodal is thin / proxies are invalid."** — Framed as *preliminary*, not headline. The
   spectrogram-vision audio failure is reported as a negative; image ML is flagged as a weak
   pixel-logreg needing a CNN/ViT baseline. The gate's modality-agnosticism is the claim; the
   MODEL-tier multimodal numbers are explicitly preliminary. (§5, §6.)
6. **"Single LLM / single prompt."** — Stated limitation; the ordering (which tier wins by stream
   characteristic) is the claim, not absolute accuracies. Multi-LLM/prompt-robustness is future work.

## If desk-rejected again (escalation path)
DMLR desk-reject → either appeal (if a misread) or pivot to an applied venue (ML-systems workshop,
MLSys, JMLR-MLOSS for the software). The OSS leaderboard + reproducibility make the applied-venue
story strong. Do NOT resubmit unchanged.
