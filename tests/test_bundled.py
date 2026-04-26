# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``dendra.bundled`` — lazy-download infrastructure for the
shipped local-LM defaults.

These tests do NOT exercise the real network or pull GGUF files.
They cover:

- Cache-path resolution + env override
- Cache-hit short-circuit (no download)
- Offline mode (DENDRA_BUNDLED_OFFLINE=1) raising helpfully
- Download-failure error message structure
- Registry shape
- ``default_verifier(prefer="bundled")`` failure mode is the
  expected ``NoVerifierAvailableError`` (not a silent skip)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dendra.bundled import (
    BundledModelUnavailableError,
    cache_dir,
    cache_path,
    cdn_base,
    ensure_model,
    is_cached,
)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Redirect bundled cache dir into a tmp path so tests can't
    touch a developer's real ``~/.cache/llama.cpp/models/``."""
    monkeypatch.setenv("DENDRA_BUNDLED_CACHE_DIR", str(tmp_path))
    return tmp_path


class TestCachePathResolution:
    def test_cache_dir_default_is_community_standard(self, monkeypatch):
        monkeypatch.delenv("DENDRA_BUNDLED_CACHE_DIR", raising=False)
        d = cache_dir()
        assert d.name == "models"
        assert d.parent.name == "llama.cpp"
        assert d.parent.parent.name == ".cache"

    def test_cache_dir_env_override(self, isolated_cache):
        assert cache_dir() == isolated_cache

    def test_cache_path_per_role(self, isolated_cache):
        judge = cache_path("judge")
        clf = cache_path("classifier")
        assert judge.parent == isolated_cache
        assert clf.parent == isolated_cache
        assert judge != clf
        # Filename is the canonical Hugging-Face GGUF naming
        assert judge.name.endswith(".gguf")
        assert clf.name.endswith(".gguf")
        # Different families so the same-LLM guardrail is satisfied
        assert "qwen" in judge.name.lower()
        assert "gemma" in clf.name.lower()


class TestIsCached:
    def test_returns_false_when_missing(self, isolated_cache):
        assert is_cached("judge") is False
        assert is_cached("classifier") is False

    def test_returns_true_when_file_present(self, isolated_cache):
        # Drop a stub GGUF in place; is_cached should accept it
        # because we're using placeholder size_bytes (=0 means
        # "any non-empty file"). Real CDN-published sizes will
        # tighten this once we go live.
        target = cache_path("judge")
        target.write_bytes(b"FAKE GGUF CONTENTS")
        assert is_cached("judge") is True

    def test_returns_false_for_empty_file(self, isolated_cache):
        target = cache_path("judge")
        target.write_bytes(b"")
        assert is_cached("judge") is False


class TestEnsureModelOffline:
    def test_offline_env_raises_when_not_cached(
        self, isolated_cache, monkeypatch
    ):
        monkeypatch.setenv("DENDRA_BUNDLED_OFFLINE", "1")
        with pytest.raises(BundledModelUnavailableError, match="not cached"):
            ensure_model("judge")

    def test_offline_env_returns_cached_path(
        self, isolated_cache, monkeypatch
    ):
        monkeypatch.setenv("DENDRA_BUNDLED_OFFLINE", "1")
        target = cache_path("judge")
        target.write_bytes(b"FAKE GGUF")
        # Cached → no network even with OFFLINE=1
        assert ensure_model("judge") == target

    def test_offline_error_lists_recovery_options(
        self, isolated_cache, monkeypatch
    ):
        monkeypatch.setenv("DENDRA_BUNDLED_OFFLINE", "1")
        with pytest.raises(BundledModelUnavailableError) as exc_info:
            ensure_model("classifier")
        msg = str(exc_info.value)
        # Every error must surface at least one actionable option
        assert "DENDRA_BUNDLED_OFFLINE" in msg
        assert "Ollama" in msg
        # And mention the canonical filename
        assert ".gguf" in msg


class TestEnsureModelDownload:
    def test_download_failure_raises_with_recovery_options(
        self, isolated_cache, monkeypatch
    ):
        # Point at a deliberately-unreachable CDN; the urllib call
        # will fail; we want a clean error with recovery paths.
        monkeypatch.setenv(
            "DENDRA_BUNDLED_CDN_BASE",
            "http://127.0.0.1:1/never-listening",
        )
        with pytest.raises(BundledModelUnavailableError) as exc_info:
            ensure_model("judge", progress=False)
        msg = str(exc_info.value)
        # All three recovery options are present
        assert "DENDRA_BUNDLED_CDN_BASE" in msg
        assert "ollama pull" in msg
        assert "OPENAI_API_KEY" in msg

    def test_failed_download_does_not_leave_partial_file(
        self, isolated_cache, monkeypatch
    ):
        monkeypatch.setenv(
            "DENDRA_BUNDLED_CDN_BASE",
            "http://127.0.0.1:1/never-listening",
        )
        target = cache_path("judge")
        with pytest.raises(BundledModelUnavailableError):
            ensure_model("judge", progress=False)
        # Cleanup happened — no zero-byte placeholder left behind
        assert not target.exists()


class TestCdnBase:
    def test_default_is_dendra_dev(self, monkeypatch):
        monkeypatch.delenv("DENDRA_BUNDLED_CDN_BASE", raising=False)
        assert cdn_base() == "https://models.dendra.dev"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv(
            "DENDRA_BUNDLED_CDN_BASE", "https://my-mirror.internal"
        )
        assert cdn_base() == "https://my-mirror.internal"


class TestDefaultVerifierBundledMode:
    """``default_verifier(prefer='bundled')`` should fail loudly
    and helpfully when the bundled path can't be served — not
    silently skip to another backend."""

    def test_bundled_unavailable_raises_no_verifier_available_error(
        self, isolated_cache, monkeypatch
    ):
        monkeypatch.setenv(
            "DENDRA_BUNDLED_CDN_BASE",
            "http://127.0.0.1:1/never-listening",
        )
        from dendra import default_verifier
        from dendra.verdicts import NoVerifierAvailableError

        with pytest.raises(NoVerifierAvailableError) as exc_info:
            default_verifier(prefer="bundled")
        msg = str(exc_info.value)
        # Must point users at viable alternatives
        assert "Ollama" in msg or "local" in msg
        assert "openai" in msg or "anthropic" in msg


class TestDefaultClassifierImportShape:
    """Don't exercise the real download — just confirm the symbol
    is exported at the module surface."""

    def test_default_classifier_is_importable(self):
        from dendra.bundled import default_classifier
        assert callable(default_classifier)

    def test_default_verifier_bundled_is_importable(self):
        from dendra.bundled import default_verifier_bundled
        assert callable(default_verifier_bundled)
