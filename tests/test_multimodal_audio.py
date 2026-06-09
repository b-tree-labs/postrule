# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# #130 slice 2 (path A) — audio → bounded spectrogram → vision MODEL tier.

from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path

import pytest

from postrule.media import AudioTooLargeError, detect_audio
from postrule.models import AnthropicAdapter

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def make_wav(seconds: float = 0.5, sr: int = 16000, freq: float = 440.0) -> bytes:
    """A valid mono 16-bit PCM WAV (RIFF/WAVE) — readable by scipy, no deps."""
    buf = io.BytesIO()
    n = int(seconds * sr)
    frames = bytearray()
    for i in range(n):
        frames += struct.pack("<h", int(32767 * 0.5 * math.sin(2 * math.pi * freq * i / sr)))
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)
    return buf.getvalue()


WAV = make_wav()


# ---------------------------------------------------------------------------
# detect_audio — same conservative contract as detect_image
# ---------------------------------------------------------------------------


def test_detects_wav_bytes_by_magic():
    assert detect_audio(WAV) == WAV


def test_str_is_text_not_audio():
    assert detect_audio("a transcript of a phone call") is None
    assert detect_audio("recording.wav") is None  # a string is never a path


def test_non_audio_bytes_fall_through():
    assert detect_audio(b"\x00\x01\x02 not audio") is None


def test_path_with_audio_extension(tmp_path: Path):
    p = tmp_path / "clip.wav"
    p.write_bytes(WAV)
    assert detect_audio(p) == WAV


def test_path_non_audio_extension_is_text(tmp_path: Path):
    p = tmp_path / "notes.txt"
    p.write_text("hello")
    assert detect_audio(p) is None


def test_oversized_audio_raises():
    big = WAV + b"\x00" * 4096
    with pytest.raises(AudioTooLargeError):
        detect_audio(big, max_bytes=1024)


def test_oversized_audio_path_via_stat(tmp_path: Path):
    p = tmp_path / "big.wav"
    p.write_bytes(WAV + b"\x00" * 8192)
    with pytest.raises(AudioTooLargeError):
        detect_audio(p, max_bytes=1024)


# ---------------------------------------------------------------------------
# spectrogram_png — renders a bounded PNG; duration cap bounds the width
# ---------------------------------------------------------------------------


def test_spectrogram_png_renders():
    pytest.importorskip("scipy")
    pytest.importorskip("PIL")
    from postrule.audio_spectrogram import spectrogram_png

    png = spectrogram_png(WAV)
    assert png.startswith(PNG_MAGIC)


def test_duration_cap_bounds_image_width():
    PILImage = pytest.importorskip("PIL.Image")
    pytest.importorskip("scipy")
    from postrule.audio_spectrogram import spectrogram_png

    long_clip = make_wav(seconds=1.0)
    narrow = PILImage.open(io.BytesIO(spectrogram_png(long_clip, max_seconds=0.2)))
    wide = PILImage.open(io.BytesIO(spectrogram_png(long_clip, max_seconds=0.8)))
    # capping to fewer seconds yields fewer STFT time columns → a narrower image
    assert narrow.width < wide.width


# ---------------------------------------------------------------------------
# AnthropicAdapter — audio routes through the vision block as a spectrogram
# ---------------------------------------------------------------------------


class _FakeUsage:
    input_tokens = 9
    output_tokens = 1


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage()


class _FakeMessages:
    def __init__(self) -> None:
        self.last_kwargs: dict = {}

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeResponse("siren")


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


def _adapter_with_fake():
    adapter = AnthropicAdapter.__new__(AnthropicAdapter)
    fake = _FakeClient()
    adapter._client = fake
    adapter._model = "claude-test"
    adapter._max_tokens = 32
    adapter._timeout = 30.0
    adapter._max_image_bytes = 5 * 1024 * 1024
    adapter._max_audio_bytes = 25 * 1024 * 1024
    return adapter, fake


def test_audio_input_sends_spectrogram_vision_block():
    pytest.importorskip("scipy")
    pytest.importorskip("PIL")
    adapter, fake = _adapter_with_fake()
    pred = adapter.classify(WAV, ["siren", "speech", "music"])

    content = fake.messages.last_kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    image_block = content[0]
    assert image_block["type"] == "image"
    # audio is rendered to a PNG spectrogram before sending
    assert image_block["source"]["media_type"] == "image/png"
    assert image_block["source"]["data"]
    assert "spectrogram" in content[1]["text"]
    assert pred.label == "siren"
