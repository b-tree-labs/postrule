# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#34 PR 3 — SDK auto-registration at decorator time.

PR 1 shipped `POST /v1/switches/register` (the endpoint).
PR 2 shipped the dashboard query update so registered switches appear
even before their first verdict.
This PR closes the loop: when `@ml_switch` is applied with credentials
present, the SDK fires a best-effort registration call so the switch
shows up in the dashboard immediately.

Contract pinned by these tests:

  1. `register_switch(api_url, bearer, switch_name, project)` POSTs the
     expected JSON to /v1/switches/register and tolerates any HTTP /
     transport failure silently.
  2. Process-level dedup: a second call with the same
     (switch_name, project) tuple skips the POST.
  3. Re-registering the same switch with a *different* project DOES
     fire a new POST (the operator just changed the binding).
  4. The decorator integration: `@ml_switch()` with credentials present
     triggers a register call exactly once per (name, project). With no
     credentials, no call is attempted. Failures don't break decorator
     import.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from postrule import ml_switch
from postrule.cloud import registration


@pytest.fixture(autouse=True)
def _clear_registry_cache() -> None:
    """Each test gets a fresh dedup cache."""
    registration._registered.clear()


class _Recorder:
    """Test double for the POST client — captures calls + scripts outcome."""

    def __init__(self, *, succeed: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self._succeed = succeed

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> bool:
        self.calls.append(
            {"url": url, "headers": headers, "body": json.loads(body.decode("utf-8"))}
        )
        return self._succeed


# ---------------------------------------------------------------------------
# Direct register_switch() contract
# ---------------------------------------------------------------------------


class TestRegisterSwitchPosts:
    def test_register_posts_expected_url_and_body(self) -> None:
        rec = _Recorder()
        ok = registration.register_switch(
            api_url="http://localhost:8787",
            bearer_token="prul_test",  # pragma: allowlist secret
            switch_name="intent",
            project="acme/billing",
            sender=rec.post,
        )
        assert ok is True
        assert len(rec.calls) == 1
        call = rec.calls[0]
        assert call["url"] == "http://localhost:8787/v1/switches/register"
        assert call["headers"]["Authorization"] == "Bearer prul_test"
        assert call["body"] == {"switch_name": "intent", "project": "acme/billing"}

    def test_register_omits_project_when_none(self) -> None:
        rec = _Recorder()
        registration.register_switch(
            api_url="http://localhost:8787",
            bearer_token="prul_test",  # pragma: allowlist secret
            switch_name="intent",
            project=None,
            sender=rec.post,
        )
        assert rec.calls[0]["body"] == {"switch_name": "intent"}

    def test_register_returns_false_on_post_failure_without_raising(self) -> None:
        rec = _Recorder(succeed=False)
        ok = registration.register_switch(
            api_url="http://localhost:8787",
            bearer_token="prul_test",  # pragma: allowlist secret
            switch_name="intent",
            project=None,
            sender=rec.post,
        )
        assert ok is False

    def test_register_swallows_transport_exceptions(self) -> None:
        def boom(url: str, *, headers: dict[str, str], body: bytes) -> bool:
            raise ConnectionError("simulated network down")

        ok = registration.register_switch(
            api_url="http://localhost:8787",
            bearer_token="prul_test",  # pragma: allowlist secret
            switch_name="intent",
            project=None,
            sender=boom,
        )
        # Best-effort: no exception bubbles up; we just return False.
        assert ok is False


# ---------------------------------------------------------------------------
# Per-process dedup
# ---------------------------------------------------------------------------


class TestPerProcessDedup:
    def test_second_call_same_pair_skips_post(self) -> None:
        rec = _Recorder()
        for _ in range(3):
            registration.register_switch(
                api_url="http://localhost:8787",
                bearer_token="prul_test",  # pragma: allowlist secret
                switch_name="intent",
                project="acme/billing",
                sender=rec.post,
            )
        assert len(rec.calls) == 1

    def test_different_project_for_same_switch_re_fires(self) -> None:
        rec = _Recorder()
        registration.register_switch(
            api_url="http://localhost:8787",
            bearer_token="prul_test",  # pragma: allowlist secret
            switch_name="intent",
            project="acme/billing",
            sender=rec.post,
        )
        registration.register_switch(
            api_url="http://localhost:8787",
            bearer_token="prul_test",  # pragma: allowlist secret
            switch_name="intent",
            project="acme/auth",
            sender=rec.post,
        )
        assert len(rec.calls) == 2
        projects = [c["body"].get("project") for c in rec.calls]
        assert projects == ["acme/billing", "acme/auth"]

    def test_dedup_keys_on_both_name_and_project(self) -> None:
        # Different switches under the same project register independently.
        rec = _Recorder()
        for name in ["a", "b", "c"]:
            registration.register_switch(
                api_url="http://localhost:8787",
                bearer_token="prul_test",  # pragma: allowlist secret
                switch_name=name,
                project="acme/billing",
                sender=rec.post,
            )
        assert len(rec.calls) == 3


# ---------------------------------------------------------------------------
# Decorator integration — exactly one register per (name, project) at
# decorator time. Failures don't break decorator import.
# ---------------------------------------------------------------------------


class TestDecoratorAutoRegister:
    def test_decorator_with_credentials_fires_one_register_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = _Recorder()
        # Force the credentials lookup + sender hook for the duration of
        # this test.
        monkeypatch.setattr(
            registration,
            "_load_credentials_or_none",
            lambda: {
                "api_key": "prul_test",  # pragma: allowlist secret
                "api_url": "http://localhost:8787",
            },
        )
        monkeypatch.setattr(registration, "_default_sender", rec.post)

        @ml_switch(project="acme/billing")
        def intent(x: str) -> str:
            return "a"

        # Drain any background thread the registrar may have spawned.
        registration._wait_for_pending(2.0)

        assert len(rec.calls) == 1
        assert rec.calls[0]["body"]["switch_name"] == "intent"
        assert rec.calls[0]["body"]["project"] == "acme/billing"

    def test_decorator_without_credentials_does_not_fire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = _Recorder()
        monkeypatch.setattr(registration, "_load_credentials_or_none", lambda: None)
        monkeypatch.setattr(registration, "_default_sender", rec.post)

        @ml_switch(project="acme/billing")
        def intent(x: str) -> str:
            return "a"

        registration._wait_for_pending(0.5)
        # Silent — no credentials means we never attempt the POST.
        assert rec.calls == []

    def test_decorator_swallowing_register_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(url: str, *, headers: dict[str, str], body: bytes) -> bool:
            raise RuntimeError("simulated registrar failure")

        monkeypatch.setattr(
            registration,
            "_load_credentials_or_none",
            lambda: {
                "api_key": "prul_test",  # pragma: allowlist secret
                "api_url": "http://localhost:8787",
            },
        )
        monkeypatch.setattr(registration, "_default_sender", boom)

        # Decorator import MUST NOT raise even when registrar explodes.
        @ml_switch(project="acme/billing")
        def intent(x: str) -> str:
            return "a"

        registration._wait_for_pending(2.0)
        # Wrapper still callable + introspectable.
        assert intent.project == "acme/billing"
