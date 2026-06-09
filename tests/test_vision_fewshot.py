# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# #130 — few-shot vision exemplars. Zero-shot rendered-media classification can
# be at chance; labeled examples ground the model. These confirm the examples
# are sent (as labeled image blocks) before the query, and that no examples
# leaves the single-image path unchanged.

from __future__ import annotations

from postrule.models import AnthropicAdapter

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


class _Usage:
    input_tokens = 5
    output_tokens = 1


class _Block:
    def __init__(self, t):
        self.text = t


class _Resp:
    def __init__(self, t):
        self.content = [_Block(t)]
        self.usage = _Usage()


class _Messages:
    def __init__(self):
        self.last = {}

    def create(self, **kw):
        self.last = kw
        return _Resp("dog")


class _Client:
    def __init__(self):
        self.messages = _Messages()


def _adapter(examples=None):
    a = AnthropicAdapter.__new__(AnthropicAdapter)
    a._client = _Client()
    a._model = "claude-test"
    a._max_tokens = 16
    a._timeout = 30.0
    a._max_image_bytes = 5 * 1024 * 1024
    a._max_audio_bytes = 25 * 1024 * 1024
    a._examples = examples
    return a


def test_examples_are_sent_as_labeled_blocks_before_query():
    a = _adapter(examples=[(PNG, "cat"), (JPEG, "dog")])
    a.classify(PNG, ["cat", "dog"])
    content = a._client.messages.last["messages"][0]["content"]
    # 2 example images + 2 label texts + intro + "now classify" + query img + instruction
    image_blocks = [c for c in content if c.get("type") == "image"]
    texts = " ".join(c["text"] for c in content if c.get("type") == "text")
    assert len(image_blocks) == 3  # 2 examples + 1 query
    assert "Label: cat" in texts and "Label: dog" in texts
    assert "examples" in texts.lower()


def test_no_examples_is_single_image():
    a = _adapter(examples=None)
    a.classify(PNG, ["cat", "dog"])
    content = a._client.messages.last["messages"][0]["content"]
    image_blocks = [c for c in content if c.get("type") == "image"]
    assert len(image_blocks) == 1  # query only


def test_text_examples_skipped_not_crash():
    # non-vision example inputs are ignored, not sent as images
    a = _adapter(examples=[("a string example", "cat"), (PNG, "dog")])
    a.classify(PNG, ["cat", "dog"])
    content = a._client.messages.last["messages"][0]["content"]
    image_blocks = [c for c in content if c.get("type") == "image"]
    assert len(image_blocks) == 2  # 1 vision example + query (string example skipped)
