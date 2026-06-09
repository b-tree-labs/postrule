# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# Best-default vision MODEL-tier recommendation for auto-instrumentation (#130).
#
# When the analyzer auto-instruments a multimodal classifier it must pick a
# vision adapter + model FOR the user. The principle is "detect, don't
# dictate":
#   * infer the PROVIDER from what the codebase already uses (don't make them
#     adopt a new vendor or a second bill); fall back to LOCAL (Ollama) when no
#     cloud provider is in evidence — zero marginal cost, privacy-preserving.
#   * default to the CHEAPEST vision-capable model of that provider, precisely
#     because the MODEL tier is TRANSITIONAL — a switch graduates off it to a
#     local ML head, so you only pay to bootstrap the gate; the cheapest model
#     that can do that is optimal.
#   * emit GUIDANCE, not just a default — the choice + why, the cost-decay
#     story, the one-line override, and the honest spectrogram caveat.

from __future__ import annotations

import re
from dataclasses import dataclass

# Cheapest vision-capable model per provider. (Kept small + explicit; if this
# ever needs to track a price catalog, drive it from llm-prices.json + a
# "vision" capability flag instead of hard-coding.)
_CHEAPEST_VISION: dict[str, tuple[str, str]] = {
    "anthropic": ("AnthropicAdapter", "claude-haiku-4-5"),
    "openai": ("OpenAIAdapter", "gpt-4o-mini"),
    "ollama": ("OllamaAdapter", "llava"),
}

_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_AUDIO_EXT = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")
_VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")

_IMAGE_LIBS = ("pil", "from pil", "import cv2", "cv2.", "torchvision", "skimage", "imageio")
_AUDIO_LIBS = ("soundfile", "librosa", "import wave", "scipy.signal", "torchaudio")
_VIDEO_LIBS = ("cv2.videocapture", "import av\n", "decord", "pyav")

# word-boundary name hints (params / locals)
_IMAGE_NAMES = ("image", "img", "frame", "photo", "picture", "pixels")
_AUDIO_NAMES = ("audio", "clip", "waveform", "sound", "spectrogram", "wav")
_VIDEO_NAMES = ("video", "movie", "frames", "footage")


@dataclass(frozen=True)
class VisionAdapterRecommendation:
    modality: str  # "image" | "audio" | "video"
    provider: str  # "anthropic" | "openai" | "ollama"
    model: str
    adapter_snippet: str  # e.g. 'AnthropicAdapter(model="claude-haiku-4-5")'
    guidance: str  # human-readable comment block


def _any(hay: str, needles) -> bool:
    return any(n in hay for n in needles)


def _name_hit(source: str, names) -> bool:
    return any(re.search(rf"\b{re.escape(n)}\b", source) for n in names)


def detect_modality(source: str) -> str:
    """Best-effort modality of a candidate classifier from its source.

    Returns "image" | "audio" | "video" | "text". File-extension literals and
    imported libraries are stronger signals than parameter names, so they win.
    """
    s = source.lower()
    # 1) extension literals (strongest)
    if _any(s, _VIDEO_EXT):
        return "video"
    if _any(s, _AUDIO_EXT):
        return "audio"
    if _any(s, _IMAGE_EXT):
        return "image"
    # 2) imported libraries
    if _any(s, _VIDEO_LIBS):
        return "video"
    if _any(s, _AUDIO_LIBS):
        return "audio"
    if _any(s, _IMAGE_LIBS):
        return "image"
    # 3) parameter / variable names (weakest)
    if _name_hit(s, _VIDEO_NAMES):
        return "video"
    if _name_hit(s, _AUDIO_NAMES):
        return "audio"
    if _name_hit(s, _IMAGE_NAMES):
        return "image"
    return "text"


def infer_provider(module_source: str) -> str:
    """Infer the LLM provider the codebase already uses (revealed preference).

    Falls back to local Ollama when no cloud provider is in evidence — zero
    marginal cost and privacy-preserving, the best 'try it free' default.
    """
    m = module_source.lower()
    if "anthropic" in m:
        return "anthropic"
    if "openai" in m:
        return "openai"
    if "ollama" in m:
        return "ollama"
    return "ollama"


def recommend_vision_adapter(
    source: str, module_source: str = ""
) -> VisionAdapterRecommendation | None:
    """Recommend a vision MODEL-tier adapter for a candidate classifier, or
    None if it looks text-only.

    ``source`` is the candidate function's source; ``module_source`` is the
    enclosing module (used to infer the provider from existing imports/keys).
    """
    modality = detect_modality(source)
    if modality == "text":
        return None

    provider = infer_provider(module_source or source)
    cls, model = _CHEAPEST_VISION[provider]
    snippet = f'{cls}(model="{model}")'

    local_note = (
        " (local, zero marginal cost — no cloud provider detected in your imports)"
        if provider == "ollama"
        else ""
    )
    audio_caveat = (
        "\n#   Note: audio routes via a spectrogram image — good for ACOUSTIC "
        "sub-tasks,\n#   not lexical 'what was said'. Validate, or use a "
        "native-audio model."
        if modality == "audio"
        else ""
    )
    video_caveat = (
        "\n#   Note: video routes as N sampled frames PLUS its audio track as a "
        "spectrogram\n#   (both together). Needs `pip install postrule[video]` "
        "(PyAV). Rendered-media\n#   vision is weak zero-shot — pass few-shot "
        "examples= to ground it."
        if modality == "video"
        else ""
    )
    guidance = (
        f"# Postrule auto-instrumentation detected a {modality} classifier.\n"
        f"#   model={snippet}{local_note}\n"
        f"#   Why this model: it's the cheapest vision-capable {provider} model. "
        "The MODEL\n"
        "#   tier is TRANSITIONAL — the switch graduates off it to a local ML "
        "head, so\n"
        "#   you pay vision tokens only to bootstrap the gate, then cost decays "
        "to ~0.\n"
        "#   Change it anytime: pass a different model= to the adapter."
        f"{audio_caveat}{video_caveat}"
    )
    return VisionAdapterRecommendation(
        modality=modality,
        provider=provider,
        model=model,
        adapter_snippet=snippet,
        guidance=guidance,
    )
