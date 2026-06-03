# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Audio feature path for the DMLR modality expansion.

Mirrors the image path (``image_rules`` + ``ImagePixelLogRegHead``) for a
third modality, but deliberately avoids librosa/torch: features are
extracted with scipy alone so the install stays light and works on
Python 3.14 (where numba/torch wheels may be absent).

Inputs are 1-D float32 waveforms sampled at :data:`SAMPLE_RATE` (the
loaders resample to this rate), so the rule and head can assume it.

  * :func:`extract_features` — a fixed 21-dim vector (spectral centroid
    mean/std, 85% rolloff, zero-crossing rate, RMS energy, + 16 log
    power-spectrum bins). This is the "traditional ML" feature path.
  * :func:`build_spectral_centroid_rule` — the cheap *day-zero* heuristic:
    nearest-class-centroid on three cheap scalar features. The audio
    analog of the keyword regex / color-centroid rule.
  * :class:`AudioFeatureLogRegHead` — sklearn logistic regression on the
    21-dim feature vector. Same protocol surface as the text/image heads.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from postrule.ml import MLPrediction

SAMPLE_RATE = 16000
_N_LOGBINS = 16
_FEATURE_DIM = 5 + _N_LOGBINS  # 21


def _to_mono(y: Any):
    import numpy as np

    y = np.asarray(y, dtype=np.float32)
    if y.ndim > 1:
        y = y.mean(axis=1)
    return y


def extract_features(y: Any, sr: int = SAMPLE_RATE):
    """Return a finite 21-dim float32 feature vector for a waveform."""
    import numpy as np
    from scipy import signal as sps

    y = _to_mono(y)
    if y.size < 256:
        return np.zeros(_FEATURE_DIM, dtype=np.float32)

    f, _t, Z = sps.stft(y, fs=sr, nperseg=512, noverlap=256)
    S = np.abs(Z) + 1e-10  # (freq, time)
    freqs = f[:, None]
    centroid = (freqs * S).sum(0) / S.sum(0)
    cumS = np.cumsum(S, axis=0) / S.sum(0, keepdims=True)
    rolloff = f[np.argmax(cumS >= 0.85, axis=0)]
    zcr = np.mean(np.abs(np.diff(np.sign(y)))) / 2.0
    rms = float(np.sqrt(np.mean(y**2)))
    edges = np.linspace(0, len(f), _N_LOGBINS + 1, dtype=int)[:-1]
    logbins = np.log(np.add.reduceat(S.mean(1), edges) + 1e-10)
    vec = np.concatenate(
        [[float(centroid.mean()), float(centroid.std()), float(rolloff.mean()), zcr, rms], logbins]
    ).astype(np.float32)
    vec[~np.isfinite(vec)] = 0.0
    return vec


def _cheap_features(y: Any, sr: int = SAMPLE_RATE):
    """Three cheap scalars for the day-zero rule: centroid, ZCR, RMS."""
    import numpy as np
    from scipy import signal as sps

    y = _to_mono(y)
    if y.size < 256:
        return np.zeros(3, dtype=np.float32)
    f, _t, Z = sps.stft(y, fs=sr, nperseg=512, noverlap=256)
    S = np.abs(Z) + 1e-10
    centroid = float(((f[:, None] * S).sum(0) / S.sum(0)).mean())
    zcr = float(np.mean(np.abs(np.diff(np.sign(y)))) / 2.0)
    rms = float(np.sqrt(np.mean(y**2)))
    v = np.array([centroid, zcr, rms], dtype=np.float32)
    v[~np.isfinite(v)] = 0.0
    return v


def build_spectral_centroid_rule(
    seed_pairs: Iterable[tuple[Any, str]], sr: int = SAMPLE_RATE
) -> Callable[[Any], str]:
    """Day-zero audio rule: nearest class centroid in cheap-feature space.

    Computes per-label mean of three cheap scalars over the seed window,
    z-normalizes, and classifies by nearest centroid. Deliberately weak —
    the audio analog of a 100-example keyword auto-rule.
    """
    import numpy as np

    pairs = list(seed_pairs)
    feats = np.stack([_cheap_features(y, sr) for y, _ in pairs])
    labels = [lbl for _, lbl in pairs]
    mu = feats.mean(0)
    sd = feats.std(0) + 1e-8
    fz = (feats - mu) / sd

    by_label: dict[str, list[int]] = {}
    for i, lbl in enumerate(labels):
        by_label.setdefault(lbl, []).append(i)
    centroids = {lbl: fz[idx].mean(0) for lbl, idx in by_label.items()}
    fallback = max(by_label, key=lambda k: len(by_label[k]))
    clabels = list(centroids)
    cmat = np.stack([centroids[k] for k in clabels])

    def _rule(y: Any) -> str:
        if not clabels:
            return fallback
        x = (_cheap_features(y, sr) - mu) / sd
        d = np.linalg.norm(cmat - x, axis=1)
        return clabels[int(d.argmin())]

    return _rule


class AudioFeatureLogRegHead:
    """Logistic-regression head on scipy audio features (21-dim).

    Same protocol surface as ``ImagePixelLogRegHead`` / the text heads:
    ``fit(records)`` then ``predict(input, labels) -> MLPrediction``.
    """

    def __init__(self, *, min_outcomes: int = 50) -> None:
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
        except ImportError as e:
            raise ImportError(
                "AudioFeatureLogRegHead requires scikit-learn. "
                "Install with `pip install postrule[train]`."
            ) from e
        self._LR = LogisticRegression
        self._Scaler = StandardScaler
        self._pipe: Any | None = None
        self._classes: list[str] = []
        self._min_outcomes = min_outcomes
        self._version = "AudioFeatureLogRegHead-untrained"

    def fit(self, records: Iterable[Any]) -> None:
        import numpy as np

        records = list(records)
        if len(records) < self._min_outcomes:
            return
        X, y = [], []
        for r in records:
            if getattr(r, "outcome", None) != "correct":
                continue
            X.append(extract_features(r.input))
            y.append(r.label)
        if len({*y}) < 2:
            return
        Xa = np.stack(X)
        scaler = self._Scaler().fit(Xa)
        clf = self._LR(max_iter=2000)
        clf.fit(scaler.transform(Xa), np.asarray(y))
        self._pipe = (scaler, clf)
        self._classes = list(clf.classes_)
        self._version = f"AudioFeatureLogRegHead-{len(records)}"

    def predict(self, input: Any, labels: Iterable[str]) -> MLPrediction:
        import numpy as np

        if self._pipe is None:
            return MLPrediction(label="", confidence=0.0)
        scaler, clf = self._pipe
        x = scaler.transform(extract_features(input).reshape(1, -1))
        probs = clf.predict_proba(x)[0]
        idx = int(np.asarray(probs).argmax())
        return MLPrediction(label=self._classes[idx], confidence=float(probs[idx]))

    def model_version(self) -> str:
        return self._version


__all__ = [
    "SAMPLE_RATE",
    "AudioFeatureLogRegHead",
    "build_spectral_centroid_rule",
    "extract_features",
]
