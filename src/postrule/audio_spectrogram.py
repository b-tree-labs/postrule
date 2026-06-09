# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# Render an audio clip to a bounded log-magnitude spectrogram PNG so the
# vision MODEL tier can classify acoustic sub-tasks (#130 slice 2, path "A").
#
# Mirrors audio_features.py's deliberate "scipy alone, no librosa/torch"
# choice so the install stays light and works on Python 3.14. Heavy/optional
# deps (soundfile for non-WAV decode, PIL to write the PNG) are lazy-imported
# and behind the `postrule[audio]` extra.
#
# Honest caveat (per #130): a spectrogram image is NOT native audio — it
# captures appearance/acoustic structure, not fine lexical content. Lexical
# "what was said" tasks want the Whisper→text slice (path B), not this one.

from __future__ import annotations

from typing import Any

# A clip is capped to this many seconds BEFORE the STFT, so an hour-long file
# can't produce an arbitrarily wide (and memory-hungry) spectrogram. This is
# the duration half of the size guard (media.detect_audio caps the byte read).
DEFAULT_MAX_SECONDS = 30.0


def _load_waveform(audio_bytes: bytes, max_seconds: float) -> tuple[Any, int]:
    """Decode container bytes → (mono float32 waveform, sample_rate).

    Prefers soundfile (WAV/FLAC/OGG); falls back to scipy for WAV so the
    common case needs no extra dependency. Caps to ``max_seconds``.
    """
    import io

    import numpy as np

    try:
        import soundfile as sf  # type: ignore[import-untyped]

        y, sr = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=False)
    except ImportError:
        try:
            from scipy.io import wavfile
        except ImportError as e:  # pragma: no cover - scipy is an audio-path dep
            raise ImportError(
                "audio spectrogram rendering needs soundfile (WAV/FLAC/OGG) or "
                "scipy (WAV). Install `pip install postrule[audio]`."
            ) from e
        sr, raw = wavfile.read(io.BytesIO(audio_bytes))
        raw = np.asarray(raw)
        if raw.dtype.kind in ("i", "u"):
            # int PCM → [-1, 1] float
            scale = float(max(abs(np.iinfo(raw.dtype).min), np.iinfo(raw.dtype).max))
            y = raw.astype(np.float32) / scale
        else:
            y = raw.astype(np.float32)

    if getattr(y, "ndim", 1) > 1:
        y = y.mean(axis=1)
    sr = int(sr)
    max_samples = int(max_seconds * sr)
    if y.shape[0] > max_samples:
        y = y[:max_samples]
    return y.astype(np.float32), sr


def spectrogram_png(audio_bytes: bytes, *, max_seconds: float = DEFAULT_MAX_SECONDS) -> bytes:
    """Render ``audio_bytes`` to a grayscale log-magnitude spectrogram PNG.

    Low frequencies at the bottom, time along the x-axis. Raises ``ValueError``
    for clips too short to transform, ``ImportError`` if no decoder/PIL.
    """
    import io

    import numpy as np
    from scipy import signal as sps

    y, sr = _load_waveform(audio_bytes, max_seconds)
    if y.size < 256:
        raise ValueError("audio clip is too short to render a spectrogram")

    _f, _t, Z = sps.stft(y, fs=sr, nperseg=512, noverlap=256)
    S = np.log(np.abs(Z) + 1e-10)
    S = S - S.min()
    peak = S.max()
    if peak > 0:
        S = S / peak
    img = (S * 255.0).astype(np.uint8)
    img = np.flipud(img)  # low freq at the bottom, like a conventional plot

    try:
        from PIL import Image  # type: ignore[import-untyped]
    except ImportError as e:
        raise ImportError(
            "rendering the spectrogram PNG needs Pillow. Install `pip install postrule[audio]`."
        ) from e

    buf = io.BytesIO()
    Image.fromarray(img, mode="L").save(buf, format="PNG")
    return buf.getvalue()
