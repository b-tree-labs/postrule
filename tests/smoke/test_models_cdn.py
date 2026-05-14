# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Smoke: models.postrule.ai serves the bundled GGUFs at correct sizes."""

from __future__ import annotations

import urllib.request

import pytest

pytestmark = pytest.mark.smoke


# Expected byte counts must match src/postrule/bundled.py:_REGISTRY.
# When the registry updates (new model version), this list updates too.
_EXPECTED_OBJECTS = [
    ("qwen2.5-7b-instruct-q4_k_m.gguf", 4_683_074_240),
    ("gemma-2-2b-instruct-q4_k_m.gguf", 1_708_582_752),
]


def _head(url: str) -> dict:
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "postrule-smoke-test/1.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — HTTPS
        return {
            "status": resp.status,
            "content_length": int(resp.headers.get("content-length", 0)),
            "etag": resp.headers.get("etag", ""),
            "server": resp.headers.get("server", ""),
            "cf_ray": resp.headers.get("cf-ray", ""),
        }


@pytest.mark.parametrize("filename,expected_size", _EXPECTED_OBJECTS)
def test_model_object_serves_with_correct_size(
    smoke_models_target: str, filename: str, expected_size: int
) -> None:
    url = f"{smoke_models_target}/{filename}"
    info = _head(url)
    assert info["status"] == 200, f"{url} returned {info['status']}"
    assert info["content_length"] == expected_size, (
        f"{filename}: expected {expected_size} bytes, got {info['content_length']}"
    )


def test_models_served_through_cloudflare(smoke_models_target: str) -> None:
    """Cloudflare-edge identification: cf-ray header should be present.

    Catches the case where the custom domain attach reverted and
    requests are hitting R2 origin directly (which would still serve
    correctly but bypasses caching + DDoS protection).
    """
    info = _head(f"{smoke_models_target}/{_EXPECTED_OBJECTS[0][0]}")
    assert info["server"].lower() == "cloudflare", (
        f"expected server: cloudflare, got {info['server']!r}"
    )
    assert info["cf_ray"], "missing cf-ray header — request bypassed Cloudflare"
