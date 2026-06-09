# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# Multimodal input detection for the MODEL (LLM) tier (#130).
#
# The object a switch is called with is shared across ALL tiers — the rule
# function and the classical-ML head see the same thing the LLM adapter does.
# Those tiers are already modality-agnostic (they consume raw arrays), so we
# must NOT wrap or transform the input globally. Instead the LLM adapter, which
# "owns serialization" per the ModelClassifier contract, asks this module
# whether a given input is an image it should send as a vision block.
#
# Disambiguation is deliberately conservative — no guessing:
#   * `str`            → ALWAYS text. str is the canonical text input; a string
#                        that happens to look like a path is still text.
#   * `bytes`/bytearray→ image ONLY if it starts with a known image magic number
#                        (PNG/JPEG/GIF/WebP). Non-image bytes → text path.
#   * `pathlib.Path`   → image if it has an image extension and the file reads.
#   * PIL.Image.Image  → encoded to PNG (lazy; PIL is an optional extra).
#   * anything else     → text (numpy arrays are intentionally NOT treated as
#                        images here — an array is ambiguous, vs a feature
#                        vector; convert to PIL/PNG to send one to the LLM).

from __future__ import annotations

from pathlib import Path
from typing import Any

# (magic-number prefix, media_type). Order matters only in that all prefixes
# are mutually exclusive for these formats.
_IMAGE_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]

_EXT_MEDIA_TYPE: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# The media types a vision-capable provider accepts today.
SUPPORTED_IMAGE_MEDIA_TYPES = frozenset(_EXT_MEDIA_TYPE.values())

# Default ceiling on a single image we'll encode + send to the MODEL tier.
# 5 MiB matches the per-image limit of common vision providers (e.g.
# Anthropic). The point isn't just the provider limit — it's record-keeping
# sanity: an unbounded read would let one oversized frame balloon memory and
# the outbound payload. Oversized media must be downsampled by the caller
# (frame-sampling for video, spectrogram for audio) BEFORE it reaches here.
DEFAULT_MAX_IMAGE_BYTES = 5 * 1024 * 1024


class ImageTooLargeError(ValueError):
    """Raised when an image input exceeds the MODEL-tier size ceiling.

    Fail-loud on purpose: silently falling back to the text path would
    ``repr()`` megabytes of bytes into a prompt, and silently sending would
    be rejected downstream by the provider. The caller should downsample.
    """


def _check_size(num_bytes: int, max_bytes: int | None) -> None:
    if max_bytes is not None and num_bytes > max_bytes:
        raise ImageTooLargeError(
            f"image input is {num_bytes} bytes, over the {max_bytes}-byte limit. "
            "Downsample it (e.g. resize, or sample fewer/smaller frames) before "
            "passing it to the switch."
        )


def _sniff_media_type(data: bytes) -> str | None:
    """Return the image media type implied by ``data``'s magic bytes, or None."""
    for magic, media_type in _IMAGE_MAGIC:
        if data.startswith(magic):
            return media_type
    # WebP: "RIFF" <4-byte size> "WEBP"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _encode_pil(obj: Any) -> tuple[bytes, str] | None:
    """If ``obj`` is a PIL image, encode it to PNG bytes. Lazy; no hard dep."""
    module = type(obj).__module__ or ""
    if not module.startswith("PIL.") or not hasattr(obj, "save"):
        return None
    import io

    buf = io.BytesIO()
    obj.save(buf, format="PNG")
    return buf.getvalue(), "image/png"


def detect_image(
    obj: Any, *, max_bytes: int | None = DEFAULT_MAX_IMAGE_BYTES
) -> tuple[bytes, str] | None:
    """Detect whether ``obj`` is an image input for the vision MODEL tier.

    Returns ``(data, media_type)`` with the raw image bytes and its MIME type
    if ``obj`` is unambiguously an image, else ``None`` (caller uses the text
    path). See the module docstring for the disambiguation rules.

    Raises :class:`ImageTooLargeError` if the image exceeds ``max_bytes``
    (pass ``max_bytes=None`` to disable the ceiling).
    """
    # str is always text — never sniff a string as a path.
    if isinstance(obj, str):
        return None

    if isinstance(obj, (bytes, bytearray)):
        data = bytes(obj)
        media_type = _sniff_media_type(data)
        if media_type is None:
            return None
        _check_size(len(data), max_bytes)
        return data, media_type

    if isinstance(obj, Path):
        media_type = _EXT_MEDIA_TYPE.get(obj.suffix.lower())
        if media_type is None:
            return None
        # Size-check via stat() BEFORE reading, so a huge file is rejected
        # without first loading it into memory.
        try:
            size = obj.stat().st_size
        except OSError:
            return None
        _check_size(size, max_bytes)
        try:
            data = obj.read_bytes()
        except OSError:
            return None
        # Trust the magic number over the extension when both are present.
        return data, (_sniff_media_type(data) or media_type)

    encoded = _encode_pil(obj)
    if encoded is None:
        return None
    data, media_type = encoded
    _check_size(len(data), max_bytes)
    return data, media_type
