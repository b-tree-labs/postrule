# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#35 — MCP onboarding tools: postrule.connect / postrule.status.

Agent-driven repos bootstrap via slash-command harnesses that can't run
CLI prompts. The MCP layer already exposes `analyze` / `init` /
`refresh` / `doctor`; this PR adds the connect + status pair so an
agent can stand up an authenticated Postrule session conversationally.

Three tools (two-step connect avoids long-blocking the agent's tool
runner):

  postrule_status:
    Returns {connected, identity, project, telemetry, outbox_pending}
    in the same shape as the `postrule status` CLI but as JSON.

  postrule_connect_start:
    Initiates the RFC 8628 device flow. Returns
    {device_code, user_code, verification_uri_complete, interval,
    expires_at} for the agent to present.

  postrule_connect_complete:
    Agent polls with device_code. Returns either
    {state: 'pending', retry_after_s: N} or
    {state: 'authorized', email, tier} (and writes credentials), or
    {state: 'denied' | 'expired' | 'error', message}.

The tools are registered alongside the existing four; tests cover
both the registration shape (list_tools sees them) and the dispatch
contracts.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from postrule.mcp_server import call_tool, list_tools

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestToolsRegistered:
    def test_three_new_tools_are_registered(self) -> None:
        tools = asyncio.run(list_tools())
        names = {t.name for t in tools}
        assert "postrule_status" in names
        assert "postrule_connect_start" in names
        assert "postrule_connect_complete" in names

    def test_existing_tools_still_present(self) -> None:
        # Regression guard — adding tools shouldn't remove the four prior ones.
        tools = asyncio.run(list_tools())
        names = {t.name for t in tools}
        for name in (
            "postrule_analyze",
            "postrule_init",
            "postrule_refresh",
            "postrule_doctor",
        ):
            assert name in names


# ---------------------------------------------------------------------------
# postrule_status
# ---------------------------------------------------------------------------


class TestStatusTool:
    def test_status_without_creds_reports_not_connected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from postrule import auth as _auth

        monkeypatch.setattr(_auth, "load_credentials", lambda: None)
        result = asyncio.run(call_tool("postrule_status", {}))
        assert result["connected"] is False
        assert "next_step" in result
        assert "postrule_connect_start" in result["next_step"]

    def test_status_with_creds_reports_full_shape(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        # Set up creds + a fake whoami so we don't hit the network.
        from postrule import auth as _auth
        from postrule import mcp_server as _mcp

        monkeypatch.setattr(
            _auth,
            "load_credentials",
            lambda: {
                "api_key": "prul_live_abc",  # pragma: allowlist secret
                "email": "ops@example.com",
                "api_url": "https://api.postrule.ai",
            },
        )
        monkeypatch.setattr(
            _mcp,
            "_status_whoami_for_mcp",
            lambda api_url, api_key: {
                "email": "ops@example.com",
                "tier": "pro",
                "key_kind": "user",
                "service_name": None,
                "telemetry_enabled": True,
            },
        )
        monkeypatch.chdir(tmp_path)

        result = asyncio.run(call_tool("postrule_status", {}))
        assert result["connected"] is True
        assert result["identity"]["email"] == "ops@example.com"
        assert result["identity"]["tier"] == "pro"
        # Service-account fields surface as null when running under a
        # user key — agents can branch on these to render the right UI.
        assert result["identity"]["key_kind"] == "user"
        assert result["identity"]["service_name"] is None
        # Project + outbox + telemetry always present in the shape.
        assert "project" in result
        assert "telemetry" in result
        assert "outbox_pending" in result

    def test_status_with_service_key_surfaces_service_identity(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from postrule import auth as _auth
        from postrule import mcp_server as _mcp

        monkeypatch.setattr(
            _auth,
            "load_credentials",
            lambda: {
                "api_key": "prul_live_abc",  # pragma: allowlist secret
                "email": "issuer@example.com",
                "api_url": "https://api.postrule.ai",
            },
        )
        monkeypatch.setattr(
            _mcp,
            "_status_whoami_for_mcp",
            lambda api_url, api_key: {
                "email": "issuer@example.com",
                "tier": "pro",
                "key_kind": "service",
                "service_name": "orders-api (production)",
                "telemetry_enabled": True,
            },
        )
        monkeypatch.chdir(tmp_path)

        result = asyncio.run(call_tool("postrule_status", {}))
        assert result["identity"]["key_kind"] == "service"
        assert result["identity"]["service_name"] == "orders-api (production)"
        assert result["identity"]["issued_by_email"] == "issuer@example.com"


# ---------------------------------------------------------------------------
# postrule_connect_start
# ---------------------------------------------------------------------------


class _FakeStartOk:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, api_base: str, body: dict) -> dict:
        self.calls.append({"api_base": api_base, "body": body})
        return {
            "device_code": "dev_xyz_secret",
            "user_code": "WXYZ-1234",
            "verification_uri_complete": "https://app.postrule.ai/cli-auth?user_code=WXYZ-1234",
            "interval": 5,
            "expires_in": 900,
        }


class _FakeStartFail:
    def __call__(self, api_base: str, body: dict) -> dict:
        raise RuntimeError("simulated server error")


class TestConnectStartTool:
    def test_start_returns_uri_and_code_to_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from postrule import mcp_server as _mcp

        monkeypatch.setattr(_mcp, "_device_post_code", _FakeStartOk())
        result = asyncio.run(call_tool("postrule_connect_start", {"device_name": "agent-host"}))
        assert result["user_code"] == "WXYZ-1234"
        assert result["verification_uri_complete"].endswith("WXYZ-1234")
        assert result["interval"] == 5
        # device_code is the secret the agent must round-trip to
        # postrule_connect_complete — surface it as opaque token.
        assert result["device_code"] == "dev_xyz_secret"

    def test_start_failure_returns_error_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from postrule import mcp_server as _mcp

        monkeypatch.setattr(_mcp, "_device_post_code", _FakeStartFail())
        result = asyncio.run(call_tool("postrule_connect_start", {}))
        assert result["state"] == "error"
        assert "message" in result


# ---------------------------------------------------------------------------
# postrule_connect_complete
# ---------------------------------------------------------------------------


class _FakeTokenAuthorized:
    def __call__(self, api_base: str, device_code: str) -> dict:
        return {
            "status": "authorized",
            "api_key": "prul_live_new",  # pragma: allowlist secret
            "email": "alice@example.com",
            "telemetry_enabled": True,
        }


class _FakeTokenPending:
    def __call__(self, api_base: str, device_code: str) -> dict:
        return {"status": "pending"}


class _FakeTokenDenied:
    def __call__(self, api_base: str, device_code: str) -> dict:
        return {"status": "denied"}


class TestConnectCompleteTool:
    def test_authorized_writes_credentials_and_returns_identity(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from postrule import auth as _auth
        from postrule import mcp_server as _mcp

        saved: dict[str, Any] = {}

        def fake_save(api_key: str, *, email: str, telemetry_enabled: bool) -> None:
            saved["api_key"] = api_key
            saved["email"] = email
            saved["telemetry_enabled"] = telemetry_enabled

        monkeypatch.setattr(_auth, "save_credentials", fake_save)
        monkeypatch.setattr(_mcp, "_device_poll_token", _FakeTokenAuthorized())

        result = asyncio.run(
            call_tool("postrule_connect_complete", {"device_code": "dev_xyz_secret"})
        )
        assert result["state"] == "authorized"
        assert result["email"] == "alice@example.com"
        assert saved["api_key"] == "prul_live_new"
        assert saved["email"] == "alice@example.com"

    def test_pending_response_signals_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from postrule import mcp_server as _mcp

        monkeypatch.setattr(_mcp, "_device_poll_token", _FakeTokenPending())
        result = asyncio.run(
            call_tool("postrule_connect_complete", {"device_code": "dev_xyz_secret"})
        )
        assert result["state"] == "pending"
        assert isinstance(result.get("retry_after_s"), int | float)

    def test_denied_response_signals_terminal_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from postrule import mcp_server as _mcp

        monkeypatch.setattr(_mcp, "_device_poll_token", _FakeTokenDenied())
        result = asyncio.run(
            call_tool("postrule_connect_complete", {"device_code": "dev_xyz_secret"})
        )
        assert result["state"] == "denied"

    def test_complete_without_device_code_returns_error(self) -> None:
        result = asyncio.run(call_tool("postrule_connect_complete", {}))
        assert result["state"] == "error"
        assert "device_code" in result.get("message", "").lower()
