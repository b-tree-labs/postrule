# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#51 — `postrule verify`: close the verify loop.

The #1 DX gap a real integration hit: a switch was wired correctly,
logged in, telemetry on — verdicts enqueued — but the fire-and-forget
emitter reported ``sent=0 / queued=2`` after ``flush()`` with **no way
to tell if anything reached the dashboard**. Right for the prod hot
path; confidence-destroying for setup.

`postrule verify` closes the loop synchronously:

  1. No credentials → clear "not connected" + nudge to login, exit 2.
  2. One blocking POST /v1/verdicts (a clearly-named probe switch),
     printing the HTTP result. Emit failure → nonzero exit (4).
  3. Read-back via GET /v1/switches confirming the probe is live, with
     the dashboard URL printed so "is it reporting?" is answerable from
     the shell. Exits 0 on delivery.
"""

from __future__ import annotations

import argparse

import pytest

from postrule import auth as _auth
from postrule import cli as _cli
from postrule.cli import cmd_verify


@pytest.fixture
def args() -> argparse.Namespace:
    return argparse.Namespace(switch=None)


CREDS = {
    "api_key": "prul_live_x",  # pragma: allowlist secret
    "api_url": "https://api.test",
    "email": "a@b.co",
}


class TestVerifyNotConnected:
    def test_no_creds_exits_2_with_login_nudge(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args
    ) -> None:
        monkeypatch.setattr(_auth, "load_credentials", lambda: None)
        rc = cmd_verify(args)
        out = capsys.readouterr().out.lower()
        assert rc == 2
        assert "not connected" in out
        assert "login" in out


class TestVerifyDelivered:
    def test_delivered_and_read_back_exits_0(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args
    ) -> None:
        monkeypatch.setattr(_auth, "load_credentials", lambda: dict(CREDS))
        seen: dict = {}

        def fake_emit(api_url, api_key, switch_name):
            seen["emit"] = (api_url, switch_name)
            return True, 202, ""

        def fake_readback(api_url, api_key, switch_name):
            seen["readback"] = switch_name
            return {"switch_name": switch_name, "total_verdicts": 1, "current_phase": "P0"}

        monkeypatch.setattr(_cli, "_verify_emit", fake_emit)
        monkeypatch.setattr(_cli, "_verify_readback", fake_readback)
        rc = cmd_verify(args)
        out = capsys.readouterr().out
        assert rc == 0
        # synchronous POST happened against the probe switch
        assert "probe" in seen["emit"][1]
        assert seen["readback"] == seen["emit"][1]
        # HTTP result + a dashboard URL are surfaced
        assert "202" in out
        assert "https://" in out and "/switches/" in out

    def test_custom_switch_name_is_used(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(_auth, "load_credentials", lambda: dict(CREDS))
        monkeypatch.setattr(_cli, "_verify_emit", lambda u, k, s: (True, 202, ""))
        monkeypatch.setattr(
            _cli, "_verify_readback", lambda u, k, s: {"switch_name": s, "total_verdicts": 3}
        )
        rc = cmd_verify(argparse.Namespace(switch="intent_classifier"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "intent_classifier" in out


class TestVerifyEmitFailure:
    def test_failed_post_exits_nonzero_and_does_not_read_back(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args
    ) -> None:
        monkeypatch.setattr(_auth, "load_credentials", lambda: dict(CREDS))
        monkeypatch.setattr(_cli, "_verify_emit", lambda u, k, s: (False, 401, "key revoked"))
        called = {"readback": False}

        def fake_readback(*a):
            called["readback"] = True
            return None

        monkeypatch.setattr(_cli, "_verify_readback", fake_readback)
        rc = cmd_verify(args)
        out = capsys.readouterr().out
        assert rc != 0
        assert "401" in out
        # No point reading back a verdict we failed to deliver.
        assert called["readback"] is False


class TestVerifyDeliveredButLagging:
    def test_delivered_but_not_yet_visible_still_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], args
    ) -> None:
        # Eventual consistency: a 2xx POST that isn't visible in the
        # read-back yet is a soft warning, not a failure.
        monkeypatch.setattr(_auth, "load_credentials", lambda: dict(CREDS))
        monkeypatch.setattr(_cli, "_verify_emit", lambda u, k, s: (True, 202, ""))
        monkeypatch.setattr(_cli, "_verify_readback", lambda u, k, s: None)
        rc = cmd_verify(args)
        out = capsys.readouterr().out.lower()
        assert rc == 0
        assert "delivered" in out
