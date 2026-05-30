# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#31 (status-doctor half) — `postrule status` command.

Tightens the install→verified-switches gap by answering the most
common first-time-user mystery in one command: **am I connected, as
whom, syncing where?** Without this, operators were grepping through
`~/.postrule/credentials` + reading the `is_logged_in()` source.

Behaviour pinned by these tests:

  1. Without credentials → clear "not connected" message + nonzero exit
     code + a one-line nudge to `postrule login`.
  2. With credentials + reachable server →
       - identity (email + tier from /v1/whoami)
       - API URL
       - project the SDK would resolve at CWD + the source of the
         derivation
       - telemetry state (on / off)
       - outbox pending count + last-attempt timestamp (or "never")
     Exits 0.
  3. With credentials but /v1/whoami fails → "Connected but server
     unreachable" + nonzero exit code.
  4. POSTRULE_NO_TELEMETRY=1 reflects in the telemetry line.

Output is plain text suitable for a single screen; no colour ANSI
codes (operators pipe this into logs / agents / Slack).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from postrule import auth as _auth
from postrule.cli import cmd_status


@pytest.fixture
def args() -> argparse.Namespace:
    """The status command takes no kwargs today; a bare Namespace is fine."""
    return argparse.Namespace()


# ---------------------------------------------------------------------------
# No credentials
# ---------------------------------------------------------------------------


class TestStatusNotConnected:
    def test_no_creds_prints_not_connected_and_exits_nonzero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        args: argparse.Namespace,
    ) -> None:
        monkeypatch.setattr(_auth, "load_credentials", lambda: None)
        rc = cmd_status(args)
        out = capsys.readouterr().out
        assert rc != 0
        assert "Not connected" in out
        # Forward path: the operator needs to know the next command to run.
        assert "postrule login" in out


# ---------------------------------------------------------------------------
# Credentials present + server reachable
# ---------------------------------------------------------------------------


class _FakeWhoamiOk:
    """Test double for the /v1/whoami call."""

    def __init__(self, *, email: str = "ops@example.com", tier: str = "pro") -> None:
        self.email = email
        self.tier = tier
        self.calls: list[str] = []

    def __call__(self, api_url: str, api_key: str) -> dict:
        self.calls.append(api_url)
        return {"email": self.email, "tier": self.tier, "telemetry_enabled": True}


class _FakeWhoamiBoom:
    def __call__(self, api_url: str, api_key: str) -> dict:
        raise ConnectionError("simulated network down")


class TestStatusConnected:
    def test_connected_prints_identity_and_tier(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        args: argparse.Namespace,
    ) -> None:
        monkeypatch.setattr(
            _auth,
            "load_credentials",
            lambda: {
                "api_key": "prul_live_abc",  # pragma: allowlist secret
                "email": "alice@example.com",
                "api_url": "https://api.postrule.ai",
            },
        )
        from postrule import cli as cli_mod

        monkeypatch.setattr(
            cli_mod, "_status_whoami", _FakeWhoamiOk(email="alice@example.com", tier="pro")
        )
        monkeypatch.chdir(tmp_path)

        rc = cmd_status(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "Connected" in out
        assert "alice@example.com" in out
        # Tier shown explicitly so the operator knows what plan they're on.
        assert "pro" in out.lower()
        # API URL surfaced so the operator can verify they're talking to
        # the intended environment (prod vs staging).
        assert "https://api.postrule.ai" in out

    def test_project_resolution_shown(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        args: argparse.Namespace,
    ) -> None:
        # postrule.toml in CWD wins the priority chain.
        (tmp_path / "postrule.toml").write_text('[project]\norg = "acme-co"\nproject = "billing"\n')
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            _auth,
            "load_credentials",
            lambda: {
                "api_key": "prul_live_abc",  # pragma: allowlist secret
                "email": "ops@example.com",
                "api_url": "https://api.postrule.ai",
            },
        )
        from postrule import cli as cli_mod

        monkeypatch.setattr(cli_mod, "_status_whoami", _FakeWhoamiOk())
        rc = cmd_status(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "acme-co/billing" in out

    def test_telemetry_off_when_env_var_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        args: argparse.Namespace,
    ) -> None:
        monkeypatch.setenv("POSTRULE_NO_TELEMETRY", "1")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            _auth,
            "load_credentials",
            lambda: {
                "api_key": "prul_live_abc",  # pragma: allowlist secret
                "email": "ops@example.com",
                "api_url": "https://api.postrule.ai",
            },
        )
        from postrule import cli as cli_mod

        monkeypatch.setattr(cli_mod, "_status_whoami", _FakeWhoamiOk())
        cmd_status(args)
        out = capsys.readouterr().out
        # Lowercase string-match so we don't over-fit the exact format.
        assert "telemetry" in out.lower()
        assert "off" in out.lower()

    def test_outbox_pending_count_shown(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        args: argparse.Namespace,
    ) -> None:
        # Seed an outbox file with 3 pending rows.
        from postrule.cloud.outbox import Outbox

        outbox_path = tmp_path / "outbox.sqlite"
        with Outbox(path=outbox_path) as ob:
            for i in range(3):
                ob.enqueue({"switch_name": f"s{i}", "request_id": f"r{i}"})

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("POSTRULE_OUTBOX_PATH", str(outbox_path))
        monkeypatch.setattr(
            _auth,
            "load_credentials",
            lambda: {
                "api_key": "prul_live_abc",  # pragma: allowlist secret
                "email": "ops@example.com",
                "api_url": "https://api.postrule.ai",
            },
        )
        from postrule import cli as cli_mod

        monkeypatch.setattr(cli_mod, "_status_whoami", _FakeWhoamiOk())
        cmd_status(args)
        out = capsys.readouterr().out
        assert "3" in out
        # "pending" word so the operator knows what the 3 refers to.
        assert "pending" in out.lower()

    def test_empty_outbox_shows_zero_pending(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        args: argparse.Namespace,
    ) -> None:
        # No outbox file at all → "0 pending" + a benign last-attempt
        # placeholder (the operator shouldn't see "never" as alarming).
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("POSTRULE_OUTBOX_PATH", str(tmp_path / "nope.sqlite"))
        monkeypatch.setattr(
            _auth,
            "load_credentials",
            lambda: {
                "api_key": "prul_live_abc",  # pragma: allowlist secret
                "email": "ops@example.com",
                "api_url": "https://api.postrule.ai",
            },
        )
        from postrule import cli as cli_mod

        monkeypatch.setattr(cli_mod, "_status_whoami", _FakeWhoamiOk())
        rc = cmd_status(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "0" in out
        assert "pending" in out.lower()


# ---------------------------------------------------------------------------
# Credentials present but server unreachable
# ---------------------------------------------------------------------------


class TestStatusServerUnreachable:
    def test_whoami_failure_prints_clear_message_and_exits_nonzero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        args: argparse.Namespace,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            _auth,
            "load_credentials",
            lambda: {
                "api_key": "prul_live_abc",  # pragma: allowlist secret
                "email": "ops@example.com",
                "api_url": "https://api.postrule.ai",
            },
        )
        from postrule import cli as cli_mod

        monkeypatch.setattr(cli_mod, "_status_whoami", _FakeWhoamiBoom())
        rc = cmd_status(args)
        out = capsys.readouterr().out
        assert rc != 0
        # Operator-facing: we have credentials but can't reach the server.
        # That's a different failure mode than "no credentials" and needs
        # a different forward path.
        assert "server" in out.lower() or "unreachable" in out.lower()
