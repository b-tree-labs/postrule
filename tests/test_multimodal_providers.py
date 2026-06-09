# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# #130 — provider-neutral vision. Image/audio detection is shared on
# _BaseAdapter; each provider formats it into its own content shape. These
# tests confirm OpenAIAdapter (image_url data URI) and OllamaAdapter (images
# field) send vision when given an image, and the text path is unchanged.

from __future__ import annotations

from postrule.models import OllamaAdapter, OpenAIAdapter

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


# ---------------------------------------------------------------------------
# OpenAIAdapter
# ---------------------------------------------------------------------------


class _FakeChoice:
    def __init__(self, text):
        self.message = type("M", (), {"content": text})()
        self.logprobs = None  # → _logprob_to_confidence fallback


class _FakeOpenAIResp:
    def __init__(self, text):
        self.choices = [_FakeChoice(text)]
        self.usage = None


class _FakeCompletions:
    def __init__(self):
        self.last_kwargs = {}

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeOpenAIResp("dog")


class _FakeOpenAIClient:
    def __init__(self):
        self.chat = type("C", (), {"completions": _FakeCompletions()})()


def _openai():
    a = OpenAIAdapter.__new__(OpenAIAdapter)
    a._client = _FakeOpenAIClient()
    a._model = "gpt-4o"
    a._temperature = 0.0
    a._timeout = 30.0
    return a


def test_openai_image_sends_image_url_data_uri():
    a = _openai()
    a.classify(PNG, ["cat", "dog"])
    content = a._client.chat.completions.last_kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    img = next(p for p in content if p["type"] == "image_url")
    assert img["image_url"]["url"].startswith("data:image/png;base64,")
    txt = next(p for p in content if p["type"] == "text")
    assert "cat" in txt["text"] and "dog" in txt["text"]


def test_openai_text_unchanged():
    a = _openai()
    a.classify("a billing question", ["billing", "tech"])
    content = a._client.chat.completions.last_kwargs["messages"][0]["content"]
    assert isinstance(content, str)
    assert "a billing question" in content


# ---------------------------------------------------------------------------
# OllamaAdapter
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self):
        pass

    def raise_for_status(self):
        return None

    def json(self):
        return {"response": "dog"}


class _FakeHttpx:
    def __init__(self):
        self.last = {}

    def post(self, url, json, timeout):
        self.last = {"url": url, "json": json, "timeout": timeout}
        return _FakeResp()


def _ollama():
    a = OllamaAdapter.__new__(OllamaAdapter)
    a._httpx = _FakeHttpx()
    a._model = "llava"
    a._host = "http://localhost:11434"
    a._timeout = 30.0
    return a


def test_ollama_image_sends_images_field():
    a = _ollama()
    a.classify(PNG, ["cat", "dog"])
    payload = a._httpx.last["json"]
    assert "images" in payload and len(payload["images"]) == 1
    assert "cat" in payload["prompt"] and "dog" in payload["prompt"]
    assert payload["images"][0]  # non-empty base64 (raw, no data: prefix)
    assert not payload["images"][0].startswith("data:")


def test_ollama_text_unchanged():
    a = _ollama()
    a.classify("a billing question", ["billing", "tech"])
    payload = a._httpx.last["json"]
    assert "images" not in payload
    assert "a billing question" in payload["prompt"]
