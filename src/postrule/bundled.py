# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Bundled local SLMs — lazy-downloaded GGUF models for ``default_verifier()``
and ``default_classifier()``.

The shipped defaults (post-bench, see
``docs/benchmarks/slm-verifier-results.md``):

- **judge** → ``qwen2.5-7b-instruct-q4_k_m.gguf`` (~4.7 GB) —
  85% accuracy on the verdict task, 481 ms p50.
- **classifier** → ``gemma-2-2b-instruct-q4_k_m.gguf`` (~1.6 GB) —
  71% accuracy on the verdict task (used as a generalisation
  proxy for classifier-task quality; a proper classifier-task
  benchmark is post-launch).

Different model families (Qwen + Gemma) so the self-judgment
guardrail (``require_distinct_from``) is satisfied without
configuration.

Cache location is the community-standard
``~/.cache/llama.cpp/models/`` so any other ``llama-cpp-python``
app on the same machine finds and reuses the same weights —
"install once, useful everywhere" rather than another
project-private cache to fill the disk.

Inference engine is ``llama-cpp-python``, an optional extra
(``pip install postrule[bundled]``). Pure-pip on macOS / Linux /
Windows with prebuilt wheels for common platforms — no
``ollama`` install required.

Usage::

    from postrule.bundled import default_verifier, default_classifier

    sw = LearnedSwitch(
        rule=my_rule,
        model=default_classifier(),     # gemma-2-2b
        verifier=default_verifier(),    # qwen2.5-7b
    )

First call to either factory triggers a one-time download of the
GGUF weights. Subsequent calls (and other tools sharing the
``~/.cache/llama.cpp/models/`` directory) hit the cache directly.

Environment overrides:

- ``POSTRULE_BUNDLED_CDN_BASE`` — replace the default CDN base URL
  with a local/private mirror (testing, air-gapped sites,
  enterprise proxy).
- ``POSTRULE_BUNDLED_CACHE_DIR`` — override the cache location.
- ``POSTRULE_BUNDLED_OFFLINE=1`` — skip the network entirely;
  raise if the requested model isn't already cached.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal

from postrule.verdicts import JudgeSource

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


# CDN base — Cloudflare R2 public bucket (set via env override pre-launch
# until production CDN is up). The default is intentionally a placeholder
# that resolves to a clear "models not hosted yet" error so anyone running
# the bundled path before launch sees a useful failure.
_DEFAULT_CDN_BASE = "https://models.postrule.ai"


def cdn_base() -> str:
    """Resolve the active CDN base URL (env override → default)."""
    return os.environ.get("POSTRULE_BUNDLED_CDN_BASE", _DEFAULT_CDN_BASE)


def cache_dir() -> Path:
    """Resolve the active cache directory (env override → community default)."""
    override = os.environ.get("POSTRULE_BUNDLED_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "llama.cpp" / "models"


# (role, info) registry. ``size_bytes=0`` and ``sha256=None`` mean
# "not yet published to the CDN" — ``is_cached`` accepts any
# non-empty file under the canonical filename. Both fields are
# tightened when a real GGUF is uploaded so verified-integrity
# downloads become possible. Filenames are the canonical
# Hugging-Face Q4_K_M-quant naming so the cache is portable across
# tools.
_REGISTRY: dict[str, dict[str, Any]] = {
    "judge": {
        "filename": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "size_bytes": 4683074240,
        "sha256": "65b8fcd92af6b4fefa935c625d1ac27ea29dcb6ee14589c55a8f115ceaaa1423",  # noqa: E501  # pragma: allowlist secret
        "ollama_fallback": "qwen2.5:7b",
        "description": "Qwen2.5-7B-Instruct (Q4_K_M) — judge default",
    },
    "classifier": {
        "filename": "gemma-2-2b-instruct-q4_k_m.gguf",
        "size_bytes": 1708582752,
        "sha256": "e0aee85060f168f0f2d8473d7ea41ce2f3230c1bc1374847505ea599288a7787",  # noqa: E501  # pragma: allowlist secret
        "ollama_fallback": "gemma2:2b",
        "description": "Gemma-2-2B-Instruct (Q4_K_M) — classifier default",
    },
}


Role = Literal["judge", "classifier"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BundledModelUnavailableError(RuntimeError):
    """Raised when a bundled model can't be downloaded or loaded.

    The error message always includes recovery options the user can
    act on: install Ollama and pull the equivalent Ollama model, set
    a cloud API key, or set ``POSTRULE_BUNDLED_CDN_BASE`` to a
    reachable mirror.
    """


# ---------------------------------------------------------------------------
# Cache + download
# ---------------------------------------------------------------------------


def cache_path(role: Role) -> Path:
    """Return the on-disk path where the role's GGUF would live."""
    info = _REGISTRY[role]
    return cache_dir() / info["filename"]


def is_cached(role: Role) -> bool:
    """``True`` if the role's GGUF is already on disk at the expected size."""
    p = cache_path(role)
    if not p.exists():
        return False
    expected = _REGISTRY[role]["size_bytes"]
    # If we don't have a published size yet (placeholder), accept
    # any non-empty file so users can drop in their own GGUF.
    return p.stat().st_size > 0 and (expected == 0 or p.stat().st_size == expected)


def ensure_model(role: Role, *, progress: bool = True) -> Path:
    """Lazy-download the role's GGUF; return its cache path.

    Idempotent: a cached file is returned without touching the
    network.

    Honours ``POSTRULE_BUNDLED_OFFLINE=1`` — when set, raises
    :class:`BundledModelUnavailableError` if the file isn't
    already cached.
    """
    if is_cached(role):
        return cache_path(role)

    if os.environ.get("POSTRULE_BUNDLED_OFFLINE", "").strip() == "1":
        raise BundledModelUnavailableError(_offline_message(role))

    info = _REGISTRY[role]
    target = cache_path(role)
    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"{cdn_base()}/{info['filename']}"

    if progress:
        size_gb = (info["size_bytes"] / 1e9) if info["size_bytes"] else None
        if size_gb:
            print(
                f"postrule: downloading {info['description']} "
                f"({size_gb:.1f} GB) from {url}\n"
                f"        → {target}\n"
                f"        (one-time; cached at "
                f"~/.cache/llama.cpp/models/ for reuse by other tools)"
            )
        else:
            print(f"postrule: downloading {info['description']} from {url}")

    try:
        urllib.request.urlretrieve(url, target)
    except (urllib.error.URLError, OSError) as e:
        # Clean up partial download
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass
        raise BundledModelUnavailableError(_download_failed_message(role, url, str(e))) from e

    return target


# ---------------------------------------------------------------------------
# Adapter construction (llama-cpp-python integration)
# ---------------------------------------------------------------------------


def _llama_cpp_classifier(model_path: Path):
    """Wrap a ``llama-cpp-python`` ``Llama`` instance as a
    ``ModelClassifier``-conforming object.

    Imports ``llama_cpp`` lazily; raises ``ImportError`` with a
    pip-install hint if missing.
    """
    try:
        from llama_cpp import Llama  # type: ignore[import-untyped]
    except ImportError as e:
        raise ImportError(
            "Bundled models require llama-cpp-python. Install with "
            "`pip install postrule[bundled]` (or `pip install "
            "llama-cpp-python` directly). "
            "Alternative: skip bundled models and use Ollama via "
            '`default_verifier(prefer="local")` instead.'
        ) from e

    from postrule.models import ModelPrediction

    llama = Llama(
        model_path=str(model_path),
        verbose=False,
    )

    class _LlamaCppClassifier:
        # Identifier the same-LLM guardrail in core.py reads.
        _model = model_path.name

        def classify(self, input: Any, labels: Any) -> ModelPrediction:
            # Render a verdict-style prompt; the JudgeSource layer
            # actually uses its own prompt template, so this path is
            # for ``model=`` use as a classifier rather than judge.
            prompt = f"Input: {input}\nLabel: "
            out = llama(prompt, max_tokens=16, temperature=0.0)
            text = out["choices"][0]["text"].strip() if out and out.get("choices") else ""
            label = text.split()[0] if text else ""
            return ModelPrediction(label=label, confidence=0.5)

    return _LlamaCppClassifier()


# ---------------------------------------------------------------------------
# Public factories
# ---------------------------------------------------------------------------


def default_verifier_bundled() -> JudgeSource:
    """Return a :class:`JudgeSource` backed by the bundled judge GGUF.

    First call downloads the GGUF (~4.7 GB) to
    ``~/.cache/llama.cpp/models/`` and constructs a
    ``llama-cpp-python``-backed adapter.

    Raises :class:`BundledModelUnavailableError` if the download
    fails or ``POSTRULE_BUNDLED_OFFLINE=1`` and no cached copy
    exists. Raises ``ImportError`` if ``llama-cpp-python`` is not
    installed.
    """
    path = ensure_model("judge")
    return JudgeSource(_llama_cpp_classifier(path))


def default_classifier():
    """Return a ``ModelClassifier``-conforming object backed by
    the bundled classifier GGUF (Gemma-2-2B by default).

    Used as the ``model=`` argument to ``LearnedSwitch`` for
    MODEL_SHADOW / MODEL_PRIMARY phases. Distinct family from
    :func:`default_verifier_bundled`'s judge model so the
    same-LLM guardrail is satisfied without per-call wiring.

    First call downloads the GGUF (~1.6 GB) to
    ``~/.cache/llama.cpp/models/``.

    Notes
    -----
    The classifier model was selected on its **verdict-task**
    accuracy (71%) as a generalisation proxy. A proper
    classifier-task benchmark — measuring "predict the right
    label given the input" rather than "judge whether a label
    matches an input" — is a v1.1 deliverable. The choice may
    change after that benchmark.
    """
    path = ensure_model("classifier")
    return _llama_cpp_classifier(path)


# ---------------------------------------------------------------------------
# Error-message templates (kept here so they're easy to edit + test)
# ---------------------------------------------------------------------------


def _offline_message(role: Role) -> str:
    info = _REGISTRY[role]
    return (
        f"Bundled {role} model {info['filename']!r} is not cached at "
        f"{cache_path(role)}, and POSTRULE_BUNDLED_OFFLINE=1 is set. "
        f"Recovery options:\n"
        f"  1. Unset POSTRULE_BUNDLED_OFFLINE and let postrule download.\n"
        f"  2. Pre-stage the GGUF: download "
        f"{cdn_base()}/{info['filename']} and put it at "
        f"{cache_path(role)}.\n"
        f"  3. Skip the bundled path: install Ollama, run "
        f"`ollama pull {info['ollama_fallback']}`, and use "
        f'default_verifier(prefer="local") instead.'
    )


def _download_failed_message(role: Role, url: str, err: str) -> str:
    info = _REGISTRY[role]
    return (
        f"Could not download bundled {role} model from {url}: {err}\n"
        f"Recovery options:\n"
        f"  1. Check connectivity and retry.\n"
        f"  2. Use a private mirror: set POSTRULE_BUNDLED_CDN_BASE to "
        f"a reachable URL hosting {info['filename']}.\n"
        f"  3. Use Ollama instead: `ollama pull "
        f"{info['ollama_fallback']}` + "
        f'`default_verifier(prefer="local")`.\n'
        f"  4. Use a cloud verifier: set OPENAI_API_KEY or "
        f'ANTHROPIC_API_KEY and pass prefer="openai" / "anthropic".'
    )


__all__ = [
    "BundledModelUnavailableError",
    "Role",
    "cache_dir",
    "cache_path",
    "cdn_base",
    "default_classifier",
    "default_verifier_bundled",
    "ensure_model",
    "is_cached",
]
