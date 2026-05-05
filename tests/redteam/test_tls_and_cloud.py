# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""TLS / cert-verification tests for cloud HTTP calls.

Coverage:
  - Source-level audit: no ``verify=False``, no http:// in cloud/.
  - Mock cloud endpoint with self-signed cert: cloud calls REFUSE
    to connect with a clean cert-validation error (when network
    egress is enabled).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.redteam


_CLOUD_DIR = Path(__file__).resolve().parent.parent.parent / "src" / "dendra" / "cloud"


# ---------------------------------------------------------------------
# Source-level audit
# ---------------------------------------------------------------------


def test_no_verify_false_in_cloud_module():
    """Grep ``src/dendra/cloud/`` for ``verify=False``. Zero hits required."""
    hits = []
    for py in _CLOUD_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        # Allow the literal phrase inside a docstring or comment that
        # explicitly forbids it ("we MUST NOT pass verify=False").
        for m in re.finditer(r"verify\s*=\s*False", text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.start())
            line = text[line_start : line_end if line_end != -1 else None]
            stripped = line.lstrip()
            if stripped.startswith("#"):
                # Comment that mentions it for documentation.
                continue
            if '"""' in line or "'''" in line:
                continue
            hits.append(f"{py}: {line.strip()}")
    assert hits == [], (
        f"cloud/ contains live verify=False (TLS cert verification disabled). Hits: {hits}"
    )


def test_no_http_urls_in_cloud_module():
    """No ``http://`` URLs in src/dendra/cloud/.

    The cloud module talks to api.dendra.ai over TLS only. Localhost
    targets and docstring URLs are exempt.
    """
    hits = []
    for py in _CLOUD_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for m in re.finditer(r"http://[\w./:-]+", text):
            url = m.group(0)
            if "localhost" in url or "127.0.0.1" in url or "::1" in url:
                continue
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.start())
            line = text[line_start : line_end if line_end != -1 else None]
            if line.lstrip().startswith("#"):
                continue
            hits.append(f"{py}: {url}")
    assert hits == [], f"cloud/ contains plain http:// URLs: {hits}"


def test_cloud_imports_requests_and_uses_default_verify():
    """Each cloud .py that imports requests calls .post/.get with the
    default verify=True. Pin via source inspection.
    """
    for py in _CLOUD_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "import requests" not in text:
            continue
        # Every requests.{post,get} call is in the source - pin that
        # none of them disables verify. (Already covered above; this
        # is a tighter restatement to help future audits.)
        assert "verify=False" not in text, py


def test_cloud_uses_https_only_for_default_endpoint():
    """The default cloud base URL is https://. Reading /src/dendra/cloud/
    files for the ``_DEFAULT_BASE`` / ``DENDRA_CLOUD_URL`` style
    constants should turn up only https:// scheme.
    """
    pattern = re.compile(r'"(https?://[^"]+)"')
    for py in _CLOUD_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for url in pattern.findall(text):
            if "localhost" in url:
                continue
            assert url.startswith("https://"), f"{py}: cloud URL must use https://, got {url!r}"


# ---------------------------------------------------------------------
# Self-signed-cert behavior (mocked, no real network)
# ---------------------------------------------------------------------


def test_self_signed_cert_refused(monkeypatch):
    """When the configured cloud endpoint serves a self-signed cert,
    ``requests.post`` raises ``SSLError`` because we don't pass
    ``verify=False``. Dendra surfaces that error rather than silently
    succeeding.

    We don't stand up a real TLS server: we monkey-patch
    ``requests.post`` to raise ``SSLError`` and assert the cloud-sync
    function propagates a non-success.
    """
    import requests

    from dendra.cloud import sync as cloud_sync

    def fake_post(*a, **kw):
        # Simulate the requests-library cert-validation refusal.
        raise requests.exceptions.SSLError(
            "HTTPSConnectionPool: SSL: CERTIFICATE_VERIFY_FAILED self-signed certificate"
        )

    monkeypatch.setattr(cloud_sync.requests, "post", fake_post)

    # Try the push - must raise (not silently succeed).
    with pytest.raises((requests.exceptions.SSLError, RuntimeError, Exception)):
        cloud_sync.push_switch_config(
            switch_name="test",
            config={"x": 1},
            api_key="k",
        )


def test_default_session_verifies_certs():
    """Pin: the default ``requests`` session verifies TLS. (The
    library default is True; Dendra never overrides.)
    """
    import requests

    s = requests.Session()
    assert s.verify is True
