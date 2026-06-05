# Supplementary code — "When Should a Rule Learn?"

This bundle reproduces every number, table, and figure in the paper. It is
self-contained: the `postrule` package source, the analysis scripts, and all
committed benchmark outputs are included. Two reproduction paths are provided —
a **fast offline path** (seconds, no API key, no large downloads) that rebuilds
the figures and statistics from the committed result files, and a **full path**
that re-runs the benchmark sweeps from scratch.

License: Apache-2.0 (see `LICENSE-APACHE`). Python 3.11+ (validated on 3.14).

---

## Layout

```
src/postrule/                     # the package under study (importable; Apache-2.0)
scripts/                          # benchmark + analysis + figure scripts
docs/papers/.../dmlr-draft.md     # the paper source (Markdown)
docs/papers/.../results/          # committed benchmark outputs (JSONL/JSON) + figures
docs/papers/.../REPRODUCE.md      # detailed per-stage reproduction notes
docs/papers/.../build_dmlr.py     # Markdown -> LaTeX -> PDF build
pyproject.toml                    # install metadata
```

The key algorithm and statistics live in:
- `src/postrule/aga.py` — Adaptive Graduated Autonomy: tier scoring, the
  significance-aware hindsight-optimal terminal tier (`oracle_terminal`), Wilson
  half-width, and the multi-objective meta-policy.
- `src/postrule/gates.py` — the statistical graduation gates, incl. the
  `CostToleranceGate` non-inferiority test used to graduate to a cheaper tier.

---

## Quick start (fast offline path — recommended first)

Rebuilds the three paper figures and the headline statistics directly from the
committed result files. No API key, no dataset downloads.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install numpy scipy matplotlib
export PYTHONPATH=src

python3 scripts/make_figures.py        # writes fig1/fig2/fig3 + prints CIs and p-values
python3 scripts/evaluate_aga.py        # AGA vs fixed-ladder accuracy/cost from committed results
python3 scripts/analyze_dmlr_characteristics.py   # cardinality -> graduation-depth table
```

Expected headline numbers (from `make_figures.py`):
- AGA accuracy **0.839** vs fixed-ladder **0.764** (Δ **+0.075**, 95% CI [+0.024, +0.127])
- **34%** fewer labeled outcomes; paired Wilcoxon for lower cost **p = 0.002**
- LLM remains the terminal tier on ~half the streams

---

## Full reproduction (re-run the sweeps)

One stage-gated command. LLM (Claude) stages are **skipped automatically** when
`ANTHROPIC_API_KEY` is unset; rule + classical-ML + characteristics stages run
fully offline. Text/image/audio datasets download to `~/.cache/postrule/datasets`
on first run (ESC-50 audio is ~600 MB).

```bash
pip install -e '.[bench,train,viz]'        # datasets, scikit-learn, scipy, matplotlib
pip install soundfile pillow               # audio decode, image encode
pip install anthropic                       # only needed for the LLM tier

# Offline (rule + classical ML + analysis only):
bash scripts/reproduce_dmlr.sh

# Full (adds the LLM tiers + AGA meta-classifier + PDF):
ANTHROPIC_API_KEY=sk-ant-... bash scripts/reproduce_dmlr.sh
```

To build the PDF, also install `pandoc` and `tectonic` (e.g. `brew install pandoc tectonic`).

A free, no-cost LLM robustness check (local `qwen2.5:7b` via Ollama, no API spend)
is provided by `scripts/robustness_qwen.py` — it confirms the tier ordering
survives a weaker, cheaper model.

See `docs/papers/2026-when-should-a-rule-learn/REPRODUCE.md` for per-stage detail,
dataset provenance, and the exact subsets used to bound LLM cost.

---

## Notes for reviewers

- **Determinism:** scripts set `PYTHONHASHSEED=0` and use fixed seeds; dataset
  loaders shuffle deterministically so class-sorted HF splits do not bias the
  classical-ML training curve.
- **Cost guard:** any script that calls a paid API checks `POSTRULE_ALLOW_PAID_LLM=1`
  before spending, so an accidental run cannot incur charges.
- **Negative results are included and reproducible:** the cross-site
  meta-classifier (`scripts/build_meta_classifier.py`) does *not* generalize at
  this dataset count — this is reported in the paper, and the leave-one-dataset-out
  instability is visible in its output.
