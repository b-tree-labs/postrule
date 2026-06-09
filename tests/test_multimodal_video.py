# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# #130 slice 3 — audiovisual: a video → sampled frames + its audio spectrogram,
# sent to the vision MODEL tier TOGETHER. Decode is via PyAV (ffmpeg); detection
# tests need no decoder, the decode/adapter tests importorskip("av").

from __future__ import annotations

from pathlib import Path

import pytest

from postrule.media import VideoTooLargeError, detect_audio, detect_video

# ftyp boxes: a video major brand (isom) vs an audio brand (M4A ).
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16
M4A = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 16
WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 16
AVI = b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 8


# ---------------------------------------------------------------------------
# detect_video — incl. the mp4-vs-m4a ftyp brand disambiguation
# ---------------------------------------------------------------------------


def test_detects_video_containers_by_magic():
    assert detect_video(MP4) == MP4
    assert detect_video(WEBM) == WEBM
    assert detect_video(AVI) == AVI


def test_ftyp_brand_splits_video_from_audio():
    # mp4 (video brand) → video, not audio; m4a (audio brand) → audio, not video
    assert detect_video(MP4) == MP4
    assert detect_audio(MP4) is None
    assert detect_video(M4A) is None
    assert detect_audio(M4A) == M4A


def test_str_is_text_not_video():
    assert detect_video("a movie review") is None
    assert detect_video("clip.mp4") is None


def test_path_video_extension(tmp_path: Path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(MP4)
    assert detect_video(p) == MP4
    assert detect_video(tmp_path / "notes.txt") is None


def test_oversized_video_raises():
    with pytest.raises(VideoTooLargeError):
        detect_video(MP4 + b"\x00" * 4096, max_bytes=1024)


# ---------------------------------------------------------------------------
# decode + audiovisual adapter content (need PyAV + Pillow)
# ---------------------------------------------------------------------------


def _make_av_clip(n_frames: int = 20, with_audio: bool = True) -> bytes:
    import os
    import tempfile

    import av  # type: ignore[import-untyped]
    import numpy as np
    from PIL import Image

    path = tempfile.mktemp(suffix=".mp4")
    c = av.open(path, "w")
    vs = c.add_stream("libx264", rate=10)
    vs.width, vs.height, vs.pix_fmt = 64, 64, "yuv420p"
    aus = c.add_stream("aac", rate=16000) if with_audio else None
    for i in range(n_frames):
        img = Image.new("RGB", (64, 64), ((i * 9) % 255, 30, 200))
        for pkt in vs.encode(av.VideoFrame.from_image(img)):
            c.mux(pkt)
    if aus is not None:
        sr = 16000
        t = np.arange(sr, dtype=np.float32) / sr
        af = av.AudioFrame.from_ndarray(
            (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2").reshape(1, -1),
            format="s16",
            layout="mono",
        )
        af.rate = sr
        for pkt in aus.encode(af):
            c.mux(pkt)
        for pkt in aus.encode():
            c.mux(pkt)
    for pkt in vs.encode():
        c.mux(pkt)
    c.close()
    with open(path, "rb") as fh:
        data = fh.read()
    os.unlink(path)
    return data


def test_sample_frames_and_extract_audio():
    pytest.importorskip("av")
    pytest.importorskip("PIL")
    from postrule.video_frames import extract_audio_wav, sample_frame_pngs

    vb = _make_av_clip()
    frames = sample_frame_pngs(vb, n_frames=5)
    assert len(frames) == 5
    assert all(f.startswith(b"\x89PNG\r\n\x1a\n") for f in frames)
    wav = extract_audio_wav(vb)
    assert wav is not None and wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"


def test_no_audio_track_returns_none():
    pytest.importorskip("av")
    pytest.importorskip("PIL")
    from postrule.video_frames import extract_audio_wav

    assert extract_audio_wav(_make_av_clip(with_audio=False)) is None


class _Resp:
    def __init__(self):
        self.content = [type("B", (), {"text": "dog"})()]
        self.usage = type("U", (), {"input_tokens": 9, "output_tokens": 1})()


class _Msgs:
    def __init__(self):
        self.last = {}

    def create(self, **kw):
        self.last = kw
        return _Resp()


def test_anthropic_video_sends_frames_plus_audio_blocks():
    pytest.importorskip("av")
    pytest.importorskip("PIL")
    pytest.importorskip("scipy")
    from postrule.models import AnthropicAdapter

    a = AnthropicAdapter.__new__(AnthropicAdapter)
    a._client = type("C", (), {"messages": _Msgs()})()
    a._model, a._max_tokens, a._timeout = "x", 16, 30.0
    a._max_image_bytes = a._max_audio_bytes = a._max_video_bytes = 100 * 1024 * 1024
    a._video_frames, a._examples = 4, None

    a.classify(_make_av_clip(), ["cat", "dog"])
    content = a._client.messages.last["messages"][0]["content"]
    images = [c for c in content if c.get("type") == "image"]
    texts = " ".join(c["text"] for c in content if c.get("type") == "text")
    assert len(images) == 5  # 4 frames + 1 audio spectrogram
    assert "frame 1" in texts and "audio track" in texts
    assert "spectrogram" in texts  # subject names both streams
