# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Audio benchmark loaders (DMLR modality expansion).

HuggingFace ``datasets`` 4.x decodes audio via ``torchcodec`` (a full
torch dependency, with no Python 3.14 wheels yet), so these loaders
bypass it entirely: they fetch a raw-WAV corpus once, decode with
``soundfile``, and resample to 16 kHz mono with scipy. Inputs returned
match the modality-agnostic ``BenchmarkDataset`` shape — ``(waveform,
label)`` pairs — so the same harness/bench pattern as CIFAR applies.

ESC-50 (Piczak 2015): 2000 clips, 50 environmental-sound classes, a
prebuilt 5-fold split (folds 1–4 train, fold 5 test). CC BY-NC 3.0.
"""

from __future__ import annotations

import csv
import io
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from postrule.audio_features import SAMPLE_RATE
from postrule.benchmarks.loaders import CACHE_DIR, BenchmarkDataset

_ESC50_URL = "https://github.com/karolpiczak/ESC-50/archive/refs/heads/master.zip"
_ESC50_DIR = CACHE_DIR / "esc50" / "ESC-50-master"


def _ensure_esc50() -> Path:
    if (_ESC50_DIR / "meta" / "esc50.csv").exists():
        return _ESC50_DIR
    dest = CACHE_DIR / "esc50"
    dest.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(_ESC50_URL) as resp:  # noqa: S310 - pinned GitHub URL
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest)
    if not (_ESC50_DIR / "meta" / "esc50.csv").exists():
        raise RuntimeError(f"ESC-50 extracted but layout unexpected under {dest}")
    return _ESC50_DIR


def _resample_mono(y: Any, sr_native: int):
    import numpy as np
    from scipy import signal as sps

    y = np.asarray(y, dtype=np.float32)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if sr_native == SAMPLE_RATE:
        return y
    from math import gcd

    g = gcd(SAMPLE_RATE, sr_native)
    return sps.resample_poly(y, SAMPLE_RATE // g, sr_native // g).astype(np.float32)


def load_esc50(*, max_per_split: int | None = None) -> BenchmarkDataset:
    """ESC-50 environmental-sound classification (50 classes).

    Folds 1–4 → train, fold 5 → test (the canonical evaluation split).
    Each input is a 16 kHz mono float32 waveform.
    """
    import soundfile as sf

    root = _ensure_esc50()
    audio_dir = root / "audio"
    rows = list(csv.DictReader((root / "meta" / "esc50.csv").open()))

    train: list[tuple[Any, str]] = []
    test: list[tuple[Any, str]] = []
    labels: set[str] = set()
    n_train = n_test = 0
    for r in rows:
        fold = int(r["fold"])
        is_test = fold == 5
        if max_per_split is not None:
            if is_test and n_test >= max_per_split:
                continue
            if not is_test and n_train >= max_per_split:
                continue
        y, sr_native = sf.read(str(audio_dir / r["filename"]), dtype="float32")
        wav = _resample_mono(y, sr_native)
        label = r["category"]
        labels.add(label)
        if is_test:
            test.append((wav, label))
            n_test += 1
        else:
            train.append((wav, label))
            n_train += 1

    return BenchmarkDataset(
        name="esc50",
        train=train,
        test=test,
        labels=sorted(labels),
        citation="Piczak 2015, 'ESC: Dataset for Environmental Sound Classification'",
    )


__all__ = ["load_esc50"]
