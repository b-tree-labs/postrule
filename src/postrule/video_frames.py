# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# Audiovisual decode for the vision MODEL tier (#130 slice 3).
#
# A video carries TWO streams. We sample N frames from the video track AND
# render the audio track to a spectrogram, so a switch can classify on BOTH
# together (degrading to frames-only when there's no audio). Decoding is via
# PyAV (ffmpeg under the hood, bundled in its wheels) for maximum encoding
# flexibility — mp4/mov/webm/mkv/avi and essentially any codec ffmpeg reads.
#
# Both are bounded: frames are capped (count) and audio is capped (duration),
# so one long clip can't balloon memory or the outbound payload.

from __future__ import annotations

import io
from typing import Any

DEFAULT_N_FRAMES = 6
# Hard cap on frames decoded into memory before subsampling. Bounds memory on
# long clips; we sample DEFAULT_N_FRAMES evenly from whatever we decoded.
_MAX_DECODED_FRAMES = 600


def _require_av() -> Any:
    try:
        import av  # type: ignore[import-untyped]
    except ImportError as e:
        raise ImportError(
            "video decoding needs PyAV. Install `pip install postrule[video]`."
        ) from e
    return av


def sample_frame_pngs(video_bytes: bytes, *, n_frames: int = DEFAULT_N_FRAMES) -> list[bytes]:
    """Sample ``n_frames`` evenly-spaced frames from the video track, each
    encoded as PNG. Returns [] if there's no video track."""
    av = _require_av()
    try:
        from PIL import Image  # noqa: F401  (PyAV's to_image needs Pillow)
    except ImportError as e:
        raise ImportError(
            "rendering video frames needs Pillow. Install `pip install postrule[video]`."
        ) from e

    decoded: list[Any] = []
    with av.open(io.BytesIO(video_bytes)) as container:
        if not container.streams.video:
            return []
        for frame in container.decode(video=0):
            decoded.append(frame)
            if len(decoded) >= _MAX_DECODED_FRAMES:
                break
    if not decoded:
        return []

    # Evenly-spaced indices across what we decoded.
    k = min(n_frames, len(decoded))
    idxs = [round(i * (len(decoded) - 1) / max(1, k - 1)) for i in range(k)] if k > 1 else [0]
    out: list[bytes] = []
    for i in idxs:
        buf = io.BytesIO()
        decoded[i].to_image().save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def extract_audio_wav(
    video_bytes: bytes, *, max_seconds: float = 30.0, sample_rate: int = 16000
) -> bytes | None:
    """Decode the audio track to mono 16 kHz WAV bytes (capped to
    ``max_seconds``), or None when the container has no audio."""
    av = _require_av()
    import wave

    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
    chunks: list[Any] = []
    n_samples = 0
    cap = int(max_seconds * sample_rate)
    with av.open(io.BytesIO(video_bytes)) as container:
        if not container.streams.audio:
            return None
        for frame in container.decode(audio=0):
            for rframe in resampler.resample(frame):
                arr = np.asarray(rframe.to_ndarray()).reshape(-1).astype("<i2")
                chunks.append(arr)
                n_samples += arr.shape[0]
            if n_samples >= cap:
                break
    if not chunks:
        return None
    pcm = np.concatenate(chunks)[:cap]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
