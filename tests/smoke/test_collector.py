# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Smoke: collector Worker accepts events and answers /health."""

from __future__ import annotations

import datetime as _dt
import json
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.smoke


def test_collector_health_returns_200(smoke_collector_target: str) -> None:
    req = urllib.request.Request(
        smoke_collector_target + "/health",
        headers={"User-Agent": "dendra-smoke-test/1.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — HTTPS
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
    assert data.get("status") == "ok"
    assert "environment" in data
    assert "time" in data


def test_collector_returns_404_for_unknown_routes(smoke_collector_target: str) -> None:
    req = urllib.request.Request(
        smoke_collector_target + "/unknown-route",
        headers={"User-Agent": "dendra-smoke-test/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            status = resp.status
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8")
    assert status == 404
    assert "not_found" in body


def test_collector_post_synthetic_event(smoke_collector_target: str, is_production: bool) -> None:
    """POST a synthetic event to the collector — staging only.

    Skipped against production: smoke tests must NOT write to the
    real cohort warehouse. The Worker is exercised here so we know
    the staging environment ingests cleanly.
    """
    if is_production:
        pytest.skip("read-only smoke against production; POST exercised on staging only")

    payload = {
        "schema_version": 1,
        "events": [
            {
                "event_type": "analyze",
                "timestamp": _dt.datetime.now(_dt.UTC).isoformat(),
                "schema_version": 1,
                "site_fingerprint": None,
                "payload": {
                    "files_scanned": 100,
                    "total_sites": 12,
                    "already_dendrified_count": 0,
                    "pattern_histogram": {"P1": 8},
                    "regime_histogram": {"narrow": 8},
                    "lift_status_histogram": {"auto_liftable": 8},
                    "hazard_category_histogram": {},
                    # This unknown key MUST be stripped server-side
                    "secret_business_label": "should-not-persist",  # pragma: allowlist secret
                },
            }
        ],
    }

    req = urllib.request.Request(
        smoke_collector_target + "/v1/events",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "user-agent": "dendra-smoke-test/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
    assert data.get("status") == "ok"
    assert data.get("inserted") == 1
    assert data.get("rejected") == 0


def test_collector_rejects_malformed_event(
    smoke_collector_target: str, is_production: bool
) -> None:
    """The Worker should 400 on garbage payloads. Staging only."""
    if is_production:
        pytest.skip("read-only smoke against production")

    payload = {"not": "a valid batch"}
    req = urllib.request.Request(
        smoke_collector_target + "/v1/events",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            status = resp.status
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8")
    assert status == 400, f"expected 400, got {status}: {body}"


def test_collector_rejects_unknown_event_type(
    smoke_collector_target: str, is_production: bool
) -> None:
    """Privacy invariant: only whitelisted event types accepted.

    Verifies the server-side defense-in-depth still works even after
    deploys (a regression here would let an attacker spam unknown
    events into the warehouse).
    """
    if is_production:
        pytest.skip("read-only smoke against production")

    payload = {
        "schema_version": 1,
        "events": [
            {
                "event_type": "totally_made_up",
                "timestamp": _dt.datetime.now(_dt.UTC).isoformat(),
                "schema_version": 1,
                "site_fingerprint": None,
                "payload": {"key": "value"},
            }
        ],
    }
    req = urllib.request.Request(
        smoke_collector_target + "/v1/events",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    # Either 400 (no valid events in batch) or 200 with rejected=1.
    # Both are acceptable; the privacy invariant is "this didn't get
    # written to the events table."
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
            if resp.status == 200:
                assert data.get("inserted") == 0
                assert data.get("rejected") == 1
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_collector_leads_post_synthetic(smoke_collector_target: str, is_production: bool) -> None:
    """POST /v1/leads with a valid email — staging only.

    Skipped against production: smoke tests must not write to the
    real leads table.
    """
    if is_production:
        pytest.skip("read-only smoke against production")

    payload = {
        "email": "smoke-test@example.com",
        "teammate_email": None,
        "site_count": 3,
        "top_priority_score": 4.50,
        "top_pattern": "P1",
        "high_priority_count": 1,
    }
    req = urllib.request.Request(
        smoke_collector_target + "/v1/leads",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "user-agent": "dendra-smoke-test/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
    assert data.get("status") == "ok"
    assert data.get("forwarded_to_teammate") is False


def test_collector_leads_rejects_bad_email(
    smoke_collector_target: str, is_production: bool
) -> None:
    """Malformed email → 400. Staging only."""
    if is_production:
        pytest.skip("read-only smoke against production")

    payload = {"email": "not-an-email"}
    req = urllib.request.Request(
        smoke_collector_target + "/v1/leads",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "user-agent": "dendra-smoke-test/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            status = resp.status
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8")
    assert status == 400, f"expected 400, got {status}: {body}"
    assert "invalid_email" in body
