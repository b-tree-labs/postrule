# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Smoke: postrule.ai landing page is reachable + valid HTML."""

from __future__ import annotations

import urllib.request

import pytest

pytestmark = pytest.mark.smoke


def test_landing_root_returns_200(smoke_target: str) -> None:
    req = urllib.request.Request(
        smoke_target + "/", headers={"User-Agent": "postrule-smoke-test/1.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — HTTPS
        assert resp.status == 200
        body = resp.read().decode("utf-8")
    assert "<html" in body.lower(), "landing did not return HTML"
    # Brand sanity check — POSTRULE wordmark must appear
    assert "POSTRULE" in body or "Postrule" in body


def test_landing_serves_correct_content_type(smoke_target: str) -> None:
    req = urllib.request.Request(smoke_target + "/")
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        ctype = resp.headers.get("content-type", "")
    assert "text/html" in ctype.lower()


def test_tuned_defaults_endpoint_serves_valid_json(smoke_target: str) -> None:
    """The cohort-defaults endpoint that every Postrule install fetches.

    Critical: if this is broken, every install's `postrule insights status`
    falls back to baked-in defaults, which is recoverable but undermines
    the flywheel claim.
    """
    import json

    req = urllib.request.Request(
        smoke_target + "/insights/tuned-defaults.json",
        headers={"User-Agent": "postrule-smoke-test/1.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        assert resp.status == 200
        ctype = resp.headers.get("content-type", "")
        # Cloudflare Pages may serve as application/json or text/json
        assert "json" in ctype.lower(), f"unexpected content-type: {ctype}"
        data = json.loads(resp.read().decode("utf-8"))
    # Schema sniff — fields the SDK reads
    assert "version" in data
    assert "cohort_size" in data
    assert "defaults" in data
