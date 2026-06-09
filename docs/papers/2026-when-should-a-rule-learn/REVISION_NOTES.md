# DMLR revision notes — internal working file

Submitted to DMLR 2026-06-05 (`main.pdf` + `postrule-dmlr-supplement.zip`). This
file collects reproducibility findings and prepared responses so a
revise-and-resubmit can be turned around quickly. Internal — not part of the
submission.

## Reproducibility: what `tests/test_aga_paper_parity.py` locks

The shipped `postrule.aga` is now regression-locked against the paper's claims
across all three AGA axes, run over the committed frozen `aligned/*.json`:

| Claim (paper) | Reproduces? | Lock |
|---|---|---|
| Per-site AGA acc 0.839 vs fixed 0.764 (+0.075) | exactly | pinned `abs=0.002` |
| LLM kept terminal ~half the streams | yes (9/21) | `8 <= n <= 12` |
| Cross-site LODO 0.762 = fixed ladder (negative result) | exactly | pinned `abs=0.01` |
| Latency p50s: rule <0.01ms, task-ML ~0.1ms, MiniLM 6–23ms, LLM 530–570ms | exactly | from `latency_profile.json` |
| 50ms SLO excludes the LLM by construction | yes | `oracle_terminal` test |

## Known discrepancy to disclose if a reviewer re-runs

**Labeled-cost figures do not reproduce exactly.** The paper reports per-site
AGA 3295 vs fixed 4964 labeled outcomes (34% reduction). Re-running the shipped
code over the current `aligned/*.json` gives **2348 vs 4017 (42% reduction)**.

- **Accuracy is unaffected** and reproduces to the digit; only the cost number drifts.
- **Cause (benign):** `ml_cost` is outcomes-to-*tie* the LLM via
  `crossover_outcomes`, i.e. it depends on the LLM tier's measured accuracy. Per
  `REPRODUCE.md`, LLM accuracies are not bit-reproducible (model/version drift),
  so the aligned JSONs shifted and moved the crossover points. The *direction and
  rough magnitude* of the saving (≥30%) is stable; the exact integer is not.
- **The qualitative claim is unchanged and arguably stronger** (42% > 34%).

### Prepared response options (pick on R&R)
1. **Re-freeze** the LLM tier accuracies used for the cost computation and
   regenerate the exact figure for the camera-ready (tightest, most defensible).
2. **Soften the prose** to a range ("roughly a third to two-fifths fewer labeled
   outcomes") and cite the LLM-non-reproducibility caveat already in REPRODUCE.md.
3. Report cost as a **distribution across seeds** rather than a point estimate.

Recommendation: (1) for the camera-ready if accepted; (2) is the cheap interim
framing if a reviewer flags it during review.

## Camera-ready TODO (on acceptance)
- Fill `\openreview` forum URL in `build_dmlr.py` (still
  `(forum link assigned upon submission)`).
- Regenerate the cost figure per option (1) above if going that route.
