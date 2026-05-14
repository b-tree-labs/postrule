# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""language model classifier protocol and optional provider adapters.

Phase 1 (MODEL_SHADOW) and Phase 2 (MODEL_PRIMARY) need a language model to produce a
classification. Postrule never hard-depends on a specific provider — users
supply any object that satisfies the :class:`ModelClassifier` protocol, or
they pull in one of the optional adapters below (all behind lazy
imports so ``pip install postrule`` stays dep-free).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPrediction:
    """What a language model classifier returns for one input.

    Optional usage / cost fields are populated by adapters when the
    underlying provider returns it (OpenAI / Anthropic surface
    ``usage.prompt_tokens`` and ``usage.completion_tokens``; Ollama
    returns ``prompt_eval_count`` and ``eval_count``). Adapters that
    don't track usage leave them as ``None``; the close-the-loop
    benchmark harness (``--measure-real-cost``) sums ``cost_usd``
    across calls and falls back to the rate-card estimate when
    ``cost_usd`` is None on every prediction.
    """

    label: str
    confidence: float
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None


@runtime_checkable
class ModelClassifier(Protocol):
    """Any object with a ``classify(input, labels) -> ModelPrediction``.

    ``input`` is the raw object passed to the switch (not stringified
    by Postrule — the adapter owns serialization).

    ``labels`` is the exhaustive label list declared on the switch.
    Adapters may use it to constrain the output, scaffold a prompt, or
    ignore it for zero-shot behavior.

    Both args are positional-only so implementations are free to
    name them anything (``ticket``, ``x``, ``request``, …) without
    tripping Protocol name-matching in strict type-checkers.
    """

    def classify(self, input: Any, labels: Iterable[str], /) -> ModelPrediction: ...


# ---------------------------------------------------------------------------
# Adapters (optional; lazy-imported)
# ---------------------------------------------------------------------------


class _BaseAdapter:
    """Shared helpers for shipped provider adapters."""

    @staticmethod
    def _render_prompt(input: Any, labels: Iterable[str]) -> str:
        label_list = ", ".join(labels)
        return (
            "Classify the following input into exactly one of these labels: "
            f"[{label_list}].\n"
            f"Input: {input!r}\n"
            "Return only the label, no extra text."
        )

    @staticmethod
    def _normalize_label(text: str, labels: Iterable[str]) -> tuple[str, bool]:
        """Best-effort match of a model output to one of ``labels``.

        Returns ``(label, matched)``. ``matched=True`` when the
        provider's text mapped to a label via exact match or one of
        the substring heuristics; ``matched=False`` when nothing
        matched and we fell back to ``labels[0]``. Adapters use
        ``matched`` to clamp confidence on no-match so the switch's
        ``confidence_threshold`` drops the prediction rather than
        silently routing every unparseable model response to the first
        label (v1-readiness.md §2 finding #12).

        Strategy: pull the first non-empty line, lowercase + strip
        punctuation, then (1) exact match, (2) whole-string-in-label
        match, (3) longest substring match.
        """
        label_list = list(labels)
        if not label_list:
            return text.strip(), False

        first_line = ""
        for line in text.splitlines():
            line = line.strip()
            if line:
                first_line = line
                break
        cleaned = first_line.strip(".?!\"'`:- ").lower()
        lower_labels = [lbl.lower() for lbl in label_list]
        if cleaned in lower_labels:
            return label_list[lower_labels.index(cleaned)], True
        # Labels whose text matches the cleaned string in either direction.
        containing_cleaned = [lbl for lbl in label_list if cleaned and cleaned in lbl.lower()]
        if containing_cleaned:
            return min(containing_cleaned, key=len), True
        hits = [lbl for lbl in label_list if lbl.lower() in cleaned]
        if hits:
            return max(hits, key=len), True
        return label_list[0], False


class OpenAIAdapter(_BaseAdapter):
    """OpenAI-compatible chat-completions classifier.

    Works against any endpoint that speaks the OpenAI Chat API (OpenAI
    itself, Together, Groq, local vLLM, LiteLLM proxy, etc.) — pass a
    custom ``base_url`` to target alternates.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
        timeout: float = 30.0,
    ) -> None:
        try:
            from openai import OpenAI  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "OpenAIAdapter requires the openai SDK. "
                "Install with `pip install postrule[openai]` or `pip install openai`."
            ) from e
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model
        self._temperature = temperature
        self._timeout = timeout

    def classify(self, input: Any, labels: Iterable[str]) -> ModelPrediction:
        labels = list(labels)
        prompt = self._render_prompt(input, labels)
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
            logprobs=True,
            top_logprobs=1,
        )
        choice = resp.choices[0]
        raw = (choice.message.content or "").strip()
        label, matched = self._normalize_label(raw, labels)
        # Unmatched output → confidence=0.0 so the switch drops it
        # via confidence_threshold. Don't silently route every
        # unparseable response to labels[0].
        confidence = _logprob_to_confidence(choice) if matched else 0.0
        tokens_in, tokens_out = _openai_usage(resp)
        return ModelPrediction(
            label=label,
            confidence=confidence,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


class AnthropicAdapter(_BaseAdapter):
    """Anthropic Messages API adapter."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        max_tokens: int = 32,
        timeout: float = 30.0,
    ) -> None:
        try:
            from anthropic import Anthropic  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "AnthropicAdapter requires the anthropic SDK. "
                "Install with `pip install postrule[anthropic]` or `pip install anthropic`."
            ) from e
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._client = Anthropic(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout

    def classify(self, input: Any, labels: Iterable[str]) -> ModelPrediction:
        labels = list(labels)
        prompt = self._render_prompt(input, labels)
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(block, "text", "") for block in resp.content).strip()
        # Anthropic doesn't expose token logprobs; approximate confidence as
        # 1.0 if the returned text exactly matches one of the allowed labels,
        # else a lower bound reflecting the uncertainty.
        exact_hit = text in labels
        label, matched = self._normalize_label(text, labels)
        confidence = 0.0 if not matched else (0.9 if exact_hit else 0.5)
        tokens_in, tokens_out = _anthropic_usage(resp)
        return ModelPrediction(
            label=label,
            confidence=confidence,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


class OllamaAdapter(_BaseAdapter):
    """Ollama local-language-model adapter (http://localhost:11434 by default)."""

    def __init__(
        self,
        *,
        model: str,
        host: str = "http://localhost:11434",
        timeout: float = 30.0,
    ) -> None:
        try:
            import httpx  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "OllamaAdapter requires httpx. "
                "Install with `pip install postrule[ollama]` or `pip install httpx`."
            ) from e
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._httpx = httpx
        self._model = model
        self._host = host.rstrip("/")
        self._timeout = timeout

    def classify(self, input: Any, labels: Iterable[str]) -> ModelPrediction:
        labels = list(labels)
        prompt = self._render_prompt(input, labels)
        r = self._httpx.post(
            f"{self._host}/api/generate",
            json={"model": self._model, "prompt": prompt, "stream": False},
            timeout=self._timeout,
        )
        r.raise_for_status()
        body = r.json()
        text = (body.get("response") or "").strip()
        exact_hit = text in labels
        label, matched = self._normalize_label(text, labels)
        confidence = 0.0 if not matched else (0.85 if exact_hit else 0.5)
        tokens_in, tokens_out = _ollama_usage(body)
        return ModelPrediction(
            label=label,
            confidence=confidence,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


class LlamafileAdapter(OpenAIAdapter):
    """Llamafile adapter — thin wrapper over OpenAIAdapter.

    Llamafile exposes an OpenAI-compatible endpoint on localhost; point
    :class:`OpenAIAdapter` at it. This subclass fixes the base_url so
    callers don't have to remember the port.
    """

    def __init__(
        self,
        *,
        model: str = "LLaMA_CPP",
        base_url: str = "http://localhost:8080/v1",
        api_key: str = "sk-no-key-required",
        temperature: float = 0.0,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
        )


# ---------------------------------------------------------------------------
# Async adapter siblings
# ---------------------------------------------------------------------------
#
# Every sync adapter ships an ``...AsyncAdapter`` peer that uses the
# provider's native async client (``AsyncOpenAI``, ``AsyncAnthropic``,
# ``httpx.AsyncClient``). The ``classify(...)`` method is replaced by
# ``aclassify(...)`` — a coroutine. Pass these to async-aware call
# sites (``LearnedSwitch.abulk_record_verdicts_from_source`` when the
# source is also async; :class:`LLMJudgeAsyncSource` for an async
# model judge; direct usage from FastAPI / LangGraph / LlamaIndex).


class OpenAIAsyncAdapter(_BaseAdapter):
    """Async peer of :class:`OpenAIAdapter`, using ``openai.AsyncOpenAI``."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
        timeout: float = 30.0,
    ) -> None:
        try:
            from openai import AsyncOpenAI  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "OpenAIAsyncAdapter requires the openai SDK. "
                "Install with `pip install postrule[openai]` or `pip install openai`."
            ) from e
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self._model = model
        self._temperature = temperature
        self._timeout = timeout

    async def aclassify(self, input: Any, labels: Iterable[str]) -> ModelPrediction:
        labels = list(labels)
        prompt = self._render_prompt(input, labels)
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self._temperature,
            logprobs=True,
            top_logprobs=1,
        )
        choice = resp.choices[0]
        raw = (choice.message.content or "").strip()
        label, matched = self._normalize_label(raw, labels)
        confidence = _logprob_to_confidence(choice) if matched else 0.0
        tokens_in, tokens_out = _openai_usage(resp)
        return ModelPrediction(
            label=label,
            confidence=confidence,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


class AnthropicAsyncAdapter(_BaseAdapter):
    """Async peer of :class:`AnthropicAdapter`, using ``anthropic.AsyncAnthropic``."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        max_tokens: int = 32,
        timeout: float = 30.0,
    ) -> None:
        try:
            from anthropic import AsyncAnthropic  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "AnthropicAsyncAdapter requires the anthropic SDK. "
                "Install with `pip install postrule[anthropic]` or `pip install anthropic`."
            ) from e
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout

    async def aclassify(self, input: Any, labels: Iterable[str]) -> ModelPrediction:
        labels = list(labels)
        prompt = self._render_prompt(input, labels)
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(block, "text", "") for block in resp.content).strip()
        exact_hit = text in labels
        label, matched = self._normalize_label(text, labels)
        confidence = 0.0 if not matched else (0.9 if exact_hit else 0.5)
        tokens_in, tokens_out = _anthropic_usage(resp)
        return ModelPrediction(
            label=label,
            confidence=confidence,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


class OllamaAsyncAdapter(_BaseAdapter):
    """Async peer of :class:`OllamaAdapter`, using ``httpx.AsyncClient``."""

    def __init__(
        self,
        *,
        model: str,
        host: str = "http://localhost:11434",
        timeout: float = 30.0,
    ) -> None:
        try:
            import httpx  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "OllamaAsyncAdapter requires httpx. "
                "Install with `pip install postrule[ollama]` or `pip install httpx`."
            ) from e
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._httpx = httpx
        self._model = model
        self._host = host.rstrip("/")
        self._timeout = timeout

    async def aclassify(self, input: Any, labels: Iterable[str]) -> ModelPrediction:
        labels = list(labels)
        prompt = self._render_prompt(input, labels)
        async with self._httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                f"{self._host}/api/generate",
                json={"model": self._model, "prompt": prompt, "stream": False},
            )
        r.raise_for_status()
        body = r.json()
        text = (body.get("response") or "").strip()
        exact_hit = text in labels
        label, matched = self._normalize_label(text, labels)
        confidence = 0.0 if not matched else (0.85 if exact_hit else 0.5)
        tokens_in, tokens_out = _ollama_usage(body)
        return ModelPrediction(
            label=label,
            confidence=confidence,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


class LlamafileAsyncAdapter(OpenAIAsyncAdapter):
    """Llamafile's OpenAI-compatible endpoint, async variant.

    Same shape as :class:`LlamafileAdapter` — inherits the local
    defaults and wires the async OpenAI client underneath.
    """

    def __init__(
        self,
        *,
        model: str = "LLaMA_CPP",
        base_url: str = "http://localhost:8080/v1",
        api_key: str = "sk-no-key-required",
        temperature: float = 0.0,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            timeout=timeout,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _logprob_to_confidence(choice: Any) -> float:
    """Best-effort probability for OpenAI-style completions.

    Uses the top token logprob when available; falls back to 0.8 if the
    provider didn't return logprobs.
    """
    try:
        import math

        content = choice.logprobs.content  # type: ignore[attr-defined]
        if not content:
            return 0.8
        # Highest-confidence first token is a reasonable proxy.
        return float(math.exp(content[0].logprob))
    except (AttributeError, IndexError, TypeError, ValueError):
        return 0.8


def _openai_usage(resp: Any) -> tuple[int | None, int | None]:
    """Extract ``(prompt_tokens, completion_tokens)`` from an OpenAI
    chat-completion response. Returns ``(None, None)`` when the
    provider didn't include a ``usage`` block.
    """
    try:
        usage = resp.usage  # type: ignore[attr-defined]
        if usage is None:
            return None, None
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        return (
            int(prompt) if prompt is not None else None,
            int(completion) if completion is not None else None,
        )
    except (AttributeError, TypeError, ValueError):
        return None, None


def _anthropic_usage(resp: Any) -> tuple[int | None, int | None]:
    """Extract ``(input_tokens, output_tokens)`` from an Anthropic
    Messages response. Returns ``(None, None)`` when ``usage`` is
    missing.
    """
    try:
        usage = resp.usage  # type: ignore[attr-defined]
        if usage is None:
            return None, None
        prompt = getattr(usage, "input_tokens", None)
        completion = getattr(usage, "output_tokens", None)
        return (
            int(prompt) if prompt is not None else None,
            int(completion) if completion is not None else None,
        )
    except (AttributeError, TypeError, ValueError):
        return None, None


def _ollama_usage(body: dict) -> tuple[int | None, int | None]:
    """Extract ``(prompt_eval_count, eval_count)`` from an Ollama
    ``/api/generate`` response body. Older Ollama builds omit these
    fields; we return ``(None, None)`` in that case.
    """
    try:
        prompt = body.get("prompt_eval_count")
        completion = body.get("eval_count")
        return (
            int(prompt) if prompt is not None else None,
            int(completion) if completion is not None else None,
        )
    except (AttributeError, TypeError, ValueError):
        return None, None


__all__ = [
    "AnthropicAdapter",
    "AnthropicAsyncAdapter",
    "LlamafileAdapter",
    "LlamafileAsyncAdapter",
    "ModelClassifier",
    "ModelPrediction",
    "OllamaAdapter",
    "OllamaAsyncAdapter",
    "OpenAIAdapter",
    "OpenAIAsyncAdapter",
]
