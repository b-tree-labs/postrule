# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# #130 PR1 — image input detection + Claude-vision MODEL-tier routing.
#
# The rule + classical-ML tiers are already modality-agnostic; only the LLM
# adapter's input path needed work. These tests cover (1) the conservative
# detection rules in postrule.media and (2) that AnthropicAdapter sends a
# vision content block for images while leaving the text path untouched.

from __future__ import annotations

from pathlib import Path

import pytest

from postrule.media import (
    DEFAULT_MAX_IMAGE_BYTES,
    ImageTooLargeError,
    detect_image,
)
from postrule.models import AnthropicAdapter

# Minimal valid-enough magic-byte payloads (detection reads only the prefix).
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8


# ---------------------------------------------------------------------------
# detect_image — the disambiguation contract
# ---------------------------------------------------------------------------


def test_detects_image_bytes_by_magic_number():
    assert detect_image(PNG) == (PNG, "image/png")
    assert detect_image(JPEG) == (JPEG, "image/jpeg")
    assert detect_image(GIF) == (GIF, "image/gif")
    assert detect_image(WEBP) == (WEBP, "image/webp")


def test_str_is_always_text():
    # A string is the canonical text input — never sniffed as a path, even
    # if it looks like a filename.
    assert detect_image("a normal support ticket") is None
    assert detect_image("photo.png") is None


def test_non_image_bytes_fall_through_to_text():
    assert detect_image(b"\x00\x01\x02 not an image") is None
    assert detect_image(b"plain ascii bytes") is None


def test_path_with_image_extension(tmp_path: Path):
    p = tmp_path / "frame.png"
    p.write_bytes(PNG)
    assert detect_image(p) == (PNG, "image/png")


def test_path_trusts_magic_over_extension(tmp_path: Path):
    # File named .png but actually JPEG bytes → media type follows the bytes.
    p = tmp_path / "mislabeled.png"
    p.write_bytes(JPEG)
    data, media_type = detect_image(p)
    assert media_type == "image/jpeg"


def test_path_non_image_extension_is_text(tmp_path: Path):
    p = tmp_path / "notes.txt"
    p.write_text("hello")
    assert detect_image(p) is None


def test_missing_file_falls_through(tmp_path: Path):
    assert detect_image(tmp_path / "does-not-exist.png") is None


def test_pil_image_encoded_to_png():
    PIL = pytest.importorskip("PIL.Image")
    img = PIL.new("RGB", (4, 4), (255, 0, 0))
    result = detect_image(img)
    assert result is not None
    data, media_type = result
    assert media_type == "image/png"
    assert data.startswith(b"\x89PNG\r\n\x1a\n")


# ---------------------------------------------------------------------------
# Size ceiling — fail loud on oversized media (record-keeping sanity)
# ---------------------------------------------------------------------------


def test_oversized_bytes_raise():
    big = PNG + b"\x00" * (3 * 1024 * 1024)
    with pytest.raises(ImageTooLargeError):
        detect_image(big, max_bytes=1024)


def test_custom_cap_and_disable():
    payload = PNG + b"\x00" * 1000
    # under a generous cap → fine
    assert detect_image(payload, max_bytes=DEFAULT_MAX_IMAGE_BYTES) is not None
    # cap of None disables the ceiling entirely
    assert detect_image(payload, max_bytes=None) is not None
    # a tiny cap rejects it
    with pytest.raises(ImageTooLargeError):
        detect_image(payload, max_bytes=16)


def test_oversized_path_rejected_via_stat(tmp_path: Path):
    p = tmp_path / "big.png"
    p.write_bytes(PNG + b"\x00" * (2 * 1024 * 1024))
    with pytest.raises(ImageTooLargeError):
        detect_image(p, max_bytes=1024)


def test_non_image_bytes_never_size_checked():
    # A huge non-image blob is just text — no spurious ImageTooLargeError.
    assert detect_image(b"\x00\x01" + b"x" * (2 * 1024 * 1024), max_bytes=16) is None


# ---------------------------------------------------------------------------
# AnthropicAdapter — vision routing vs unchanged text path
# ---------------------------------------------------------------------------


class _FakeUsage:
    input_tokens = 11
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
        return _FakeResponse("dog")


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


def _adapter_with_fake() -> tuple[AnthropicAdapter, _FakeClient]:
    # Bypass __init__ so the test needs neither the anthropic SDK nor a network.
    adapter = AnthropicAdapter.__new__(AnthropicAdapter)
    fake = _FakeClient()
    adapter._client = fake
    adapter._model = "claude-test"
    adapter._max_tokens = 32
    adapter._timeout = 30.0
    return adapter, fake


def test_image_input_sends_vision_block():
    adapter, fake = _adapter_with_fake()
    pred = adapter.classify(PNG, ["cat", "dog"])

    content = fake.messages.last_kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    image_block = content[0]
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/png"
    assert image_block["source"]["data"]  # non-empty base64
    # the label instruction rides in a separate text block
    assert content[1]["type"] == "text"
    assert "cat" in content[1]["text"] and "dog" in content[1]["text"]
    # response parsing is shared with the text path
    assert pred.label == "dog"


def test_adapter_enforces_its_image_cap():
    adapter, _ = _adapter_with_fake()
    adapter._max_image_bytes = 1024
    with pytest.raises(ImageTooLargeError):
        adapter.classify(PNG + b"\x00" * (2 * 1024 * 1024), ["cat", "dog"])


def test_text_input_unchanged_string_content():
    adapter, fake = _adapter_with_fake()
    adapter.classify("a support ticket about billing", ["billing", "technical"])

    content = fake.messages.last_kwargs["messages"][0]["content"]
    # text path still sends a plain string prompt — no vision block
    assert isinstance(content, str)
    assert "a support ticket about billing" in content
