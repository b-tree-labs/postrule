# Reproducing the DMLR study

One command reproduces every number in the paper. MODEL (LLM) stages are skipped
automatically without an API key; rule + classical-ML results reproduce offline.

## 1. Install

```bash
pip install -e '.[bench,train,viz]'      # datasets, scikit-learn, scipy, matplotlib
pip install soundfile pillow anthropic    # audio decode, image encode, Claude MODEL tier
# PDF stage only: pandoc + tectonic (e.g. `brew install pandoc tectonic`)
```

Python 3.11+ (validated on 3.14). The audio path uses **scipy + soundfile only** (no
librosa/torch) so it installs on bleeding-edge Pythons. ESC-50 (~600 MB) and the HF
text datasets download to `~/.cache/postrule/datasets` on first run.

## 2. Run

```bash
# Full study (rule + classical ML + MODEL/LLM tiers + AGA + meta-classifier + PDF):
ANTHROPIC_API_KEY=sk-ant-... bash scripts/reproduce_dmlr.sh

# Offline (rule + classical ML + characteristics analysis only):
bash scripts/reproduce_dmlr.sh
```

The MODEL tier defaults to `claude-haiku-4-5`. Cost for the full MODEL sweep is a few
USD (test subsets are bounded: text `--test-n 400`, image 150, audio 120).

## 3. Stages & outputs (all under `results/dmlr-text-expansion/`)

| stage | script | output |
|---|---|---|
| 1 text benches | `run_dmlr_text_expansion.sh` | `<dataset>.jsonl` (rule+ML transition curves) |
| 2 image/audio | `bench_cifar10_image.py`, `bench_audio.py` | `cifar10_paired.jsonl`, `esc50.jsonl` |
| 3 characteristics | `analyze_dmlr_characteristics.py` | `characteristics_analysis.json` (cardinality ρ) |
| 4 aligned sweep | `aligned_tier_eval.py`, `multimodal_model_eval.py` | `aligned/<dataset>.json`, `llm_tiers.json` |
| 5 AGA + meta | `evaluate_aga.py`, `build_meta_classifier.py` | console tables, `meta_classifier_rows.json` |
| 6 paper | `build_dmlr.py` + tectonic | `dmlr-submission/main.pdf` |

## 4. Determinism & caveats

- Seeds are fixed (`PYTHONHASHSEED=0`; deterministic shuffles in loaders/harness).
- LLM tiers are **not** bit-reproducible (model/version drift); accuracies reproduce
  within sampling noise. The aligned sweep records `common_test_n` so CIs are computable.
- Classical-ML baselines are deliberately the *cheap day-N head* (TF-IDF / pixel /
  MFCC + logistic regression), not fine-tuned transformers — see the paper's limitations.
- MODEL tier for audio uses a spectrogram→vision proxy; image/video use frames. Native
  audio/video models are noted as future work.
