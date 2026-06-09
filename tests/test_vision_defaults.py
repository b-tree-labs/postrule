# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# #130 — best-default vision adapter recommendation for auto-instrumentation.

from __future__ import annotations

from postrule.analyzer import recommend_vision_adapter  # re-exported
from postrule.vision_defaults import detect_modality, infer_provider

# ---------------------------------------------------------------------------
# detect_modality
# ---------------------------------------------------------------------------


def test_modality_text_default():
    assert (
        detect_modality("def f(ticket):\n return 'billing' if 'pay' in ticket else 'tech'")
        == "text"
    )


def test_modality_image_by_ext_lib_and_name():
    assert detect_modality('open("frame.png")') == "image"
    assert detect_modality("from PIL import Image\nimg = Image.open(x)") == "image"
    assert detect_modality("def classify(image):\n    return 'cat'") == "image"


def test_modality_audio():
    assert detect_modality('load("clip.wav")') == "audio"
    assert detect_modality("import soundfile as sf\ny=sf.read(p)") == "audio"
    assert detect_modality("def f(waveform):\n    return 'siren'") == "audio"


def test_modality_video():
    assert detect_modality('cv2.VideoCapture("movie.mp4")') == "video"
    assert detect_modality("def f(video):\n    return 'action'") == "video"


def test_extension_beats_name():
    # a .wav literal should win over an "image"-y name
    assert detect_modality("def f(image):\n    return load('a.wav')") == "audio"


# ---------------------------------------------------------------------------
# infer_provider — revealed preference; local fallback
# ---------------------------------------------------------------------------


def test_provider_inference():
    assert infer_provider("import anthropic") == "anthropic"
    assert infer_provider("from openai import OpenAI") == "openai"
    assert infer_provider("import ollama") == "ollama"
    # no cloud provider in evidence → local (zero marginal cost)
    assert infer_provider("import numpy as np") == "ollama"


# ---------------------------------------------------------------------------
# recommend_vision_adapter — the integrated recommendation
# ---------------------------------------------------------------------------


def test_text_gets_no_recommendation():
    assert recommend_vision_adapter("def f(ticket): return 'a'") is None


def test_image_with_openai_picks_cheapest_openai_vision():
    rec = recommend_vision_adapter("def f(image): return 'cat'", "from openai import OpenAI")
    assert rec is not None
    assert rec.modality == "image"
    assert rec.provider == "openai"
    assert rec.model == "gpt-4o-mini"
    assert 'OpenAIAdapter(model="gpt-4o-mini")' in rec.adapter_snippet
    assert "transitional" in rec.guidance.lower()  # cost-decay rationale present


def test_no_provider_defaults_local_with_note():
    rec = recommend_vision_adapter("def f(image): return 'cat'", "import numpy")
    assert rec.provider == "ollama"
    assert "local" in rec.guidance.lower()


def test_audio_recommendation_carries_the_spectrogram_caveat():
    rec = recommend_vision_adapter("def f(clip): return 'siren'", "import anthropic")
    assert rec.modality == "audio"
    assert "spectrogram" in rec.guidance.lower()
    assert rec.provider == "anthropic" and rec.model == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# analyzer wiring smoke — ClassificationSite carries modality fields
# ---------------------------------------------------------------------------


def test_classification_site_has_modality_fields():
    from postrule.analyzer import ClassificationSite

    s = ClassificationSite(
        file_path="f.py", function_name="g", line_start=1, line_end=2, pattern="P1"
    )
    assert s.modality == "text"
    assert s.vision_recommendation is None
