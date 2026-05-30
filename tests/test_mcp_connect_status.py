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

# Skip the whole module when the optional `mcp` extra isn't installed.
# Matches the pattern in tests/test_mcp_server.py — CI's base install
# doesn't pull in the mcp dependency.
pytest.importorskip("mcp")

from postrule.mcp_server import call_tool, list_tools  # noqa: E402

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
        assert saved["api_key"] == "prul_live_new"  # pragma: allowlist secret
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


# ---------------------------------------------------------------------------
# Coverage for the HTTP helpers — _status_whoami_for_mcp,
# _device_post_code, _device_poll_token. Tests mock urllib.request at the
# right layer so we exercise the parse + error-mapping branches.
# ---------------------------------------------------------------------------


class _FakeUrlopenResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeUrlopenResponse:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class TestStatusWhoamiForMcp:
    def test_2xx_returns_parsed_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.request

        from postrule import mcp_server as _mcp

        def fake_urlopen(req, timeout):
            return _FakeUrlopenResponse(200, b'{"email":"x@y","tier":"pro"}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        out = _mcp._status_whoami_for_mcp("http://api.example", "key")
        assert out["email"] == "x@y"
        assert out["tier"] == "pro"

    def test_non_2xx_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.request

        from postrule import mcp_server as _mcp

        def fake_urlopen(req, timeout):
            return _FakeUrlopenResponse(500, b"oops")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(OSError):
            _mcp._status_whoami_for_mcp("http://api.example", "key")


class TestDevicePostCode:
    def test_returns_parsed_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.request

        from postrule import mcp_server as _mcp

        body = b'{"device_code":"d","user_code":"U-1","verification_uri_complete":"x","interval":5,"expires_in":900}'

        def fake_urlopen(req, timeout):
            return _FakeUrlopenResponse(200, body)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        info = _mcp._device_post_code("http://api.example", {"device_name": "agent"})
        assert info["device_code"] == "d"
        assert info["user_code"] == "U-1"


class TestDevicePollToken:
    def _mock_http_error(self, code: int, body: str):
        import urllib.error

        class _Err(urllib.error.HTTPError):
            def __init__(self) -> None:
                self.code = code
                self._body = body.encode("utf-8")
                # HTTPError needs more init but read() is what we use.

            def read(self) -> bytes:
                return self._body

        return _Err()

    def test_2xx_maps_to_authorized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.request

        from postrule import mcp_server as _mcp

        def fake_urlopen(req, timeout):
            return _FakeUrlopenResponse(
                200, b'{"api_key":"k","email":"e","telemetry_enabled":true}'
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        out = _mcp._device_poll_token("http://api.example", "d")
        assert out["status"] == "authorized"
        assert out["email"] == "e"

    def test_authorization_pending_maps_to_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.error
        import urllib.request

        from postrule import mcp_server as _mcp

        def fake_urlopen(req, timeout):
            err = urllib.error.HTTPError(
                "http://x",
                400,
                "Bad Request",
                {},
                None,  # type: ignore[arg-type]
            )
            err.read = lambda: b'{"error":"authorization_pending"}'  # type: ignore[method-assign]
            raise err

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        out = _mcp._device_poll_token("http://api.example", "d")
        assert out["status"] == "pending"

    def test_access_denied_maps_to_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.error
        import urllib.request

        from postrule import mcp_server as _mcp

        def fake_urlopen(req, timeout):
            err = urllib.error.HTTPError("http://x", 400, "Bad", {}, None)  # type: ignore[arg-type]
            err.read = lambda: b'{"error":"access_denied"}'  # type: ignore[method-assign]
            raise err

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        out = _mcp._device_poll_token("http://api.example", "d")
        assert out["status"] == "denied"

    def test_expired_token_maps_to_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.error
        import urllib.request

        from postrule import mcp_server as _mcp

        def fake_urlopen(req, timeout):
            err = urllib.error.HTTPError("http://x", 400, "Bad", {}, None)  # type: ignore[arg-type]
            err.read = lambda: b'{"error":"expired_token"}'  # type: ignore[method-assign]
            raise err

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        out = _mcp._device_poll_token("http://api.example", "d")
        assert out["status"] == "expired"

    def test_unknown_error_maps_to_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.error
        import urllib.request

        from postrule import mcp_server as _mcp

        def fake_urlopen(req, timeout):
            err = urllib.error.HTTPError("http://x", 500, "Server", {}, None)  # type: ignore[arg-type]
            err.read = lambda: b"{}"  # type: ignore[method-assign]
            raise err

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        out = _mcp._device_poll_token("http://api.example", "d")
        assert out["status"] == "error"


class TestApiBaseHelper:
    def test_postrule_api_base_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from postrule import mcp_server as _mcp

        monkeypatch.setenv("POSTRULE_API_BASE", "http://localhost:8787/v1")
        assert _mcp._api_base_for_mcp() == "http://localhost:8787/v1"

    def test_falls_back_to_api_url_plus_v1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from postrule import mcp_server as _mcp

        monkeypatch.delenv("POSTRULE_API_BASE", raising=False)
        monkeypatch.setenv("POSTRULE_API_URL", "https://api.example.com")
        assert _mcp._api_base_for_mcp() == "https://api.example.com/v1"


class TestStatusToolServerUnreachable:
    def test_whoami_failure_reports_server_unreachable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
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

        def boom(api_url: str, api_key: str) -> dict:
            raise ConnectionError("simulated")

        monkeypatch.setattr(_mcp, "_status_whoami_for_mcp", boom)
        monkeypatch.chdir(tmp_path)
        result = asyncio.run(call_tool("postrule_status", {}))
        assert result["connected"] is True
        assert result.get("server_reachable") is False
        assert "next_step" in result
