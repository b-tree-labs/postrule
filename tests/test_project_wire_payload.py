# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#107 PR 3 — wire-payload contract for the `project` field.

PR 1 added `@ml_switch(project="...")` + the auto-derive helper.
PR 2 added the projects + switch_meta tables + privacy boundary.
This PR plumbs `project` through to the verdict wire payload so the
server (PR 3 server side) can persist it.

Pinned by these tests:

  1. LearnedSwitch accepts `project` and exposes `self.project`.
  2. The decorator's `@ml_switch(project="...")` flows through to the
     underlying LearnedSwitch (was wrapper-only in PR 1).
  3. Every `emit("outcome", ...)` event dict includes `project` when
     the switch knows one, so verdict_telemetry can forward it.
  4. CloudVerdictEmitter._build_payload forwards `project` to the
     wire payload — but only when the field is a non-empty string,
     so absent / None / empty all get dropped (server contract).
"""

from __future__ import annotations

import pytest

from postrule import ml_switch
from postrule.cloud.verdict_telemetry import CloudVerdictEmitter
from postrule.core import LearnedSwitch

# ---------------------------------------------------------------------------
# LearnedSwitch carries the project
# ---------------------------------------------------------------------------


class TestLearnedSwitchProject:
    def test_project_kwarg_attaches_to_switch(self) -> None:
        s = LearnedSwitch(rule=lambda x: "a", name="t", project="billing-service")
        assert s.project == "billing-service"

    def test_no_project_kwarg_defaults_to_none(self) -> None:
        # Bare LearnedSwitch (constructed without the decorator) carries
        # None, not "default" — only the wrapper layer auto-derives, so
        # direct LearnedSwitch users can opt in by passing project=.
        s = LearnedSwitch(rule=lambda x: "a", name="t")
        assert s.project is None


class TestDecoratorPropagatesProjectToSwitch:
    def test_explicit_project_flows_to_underlying_switch(self) -> None:
        @ml_switch(project="billing-service")
        def classify(x: str) -> str:
            return "a"

        # Was wrapper-only in PR 1; now both layers know.
        assert classify.project == "billing-service"
        assert classify.switch.project == "billing-service"

    def test_auto_derived_project_flows_to_underlying_switch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "auto-app"\n')
        monkeypatch.chdir(tmp_path)

        @ml_switch()
        def classify(x: str) -> str:
            return "a"

        assert classify.project == "auto-app"
        assert classify.switch.project == "auto-app"


# ---------------------------------------------------------------------------
# Wire payload forwards project
# ---------------------------------------------------------------------------


class _NoopSender:
    """Bypass the real HTTP sender — we only care about payload shape."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, payload: dict) -> bool:
        self.calls.append(payload)
        return True


def _make_emitter() -> CloudVerdictEmitter:
    em = CloudVerdictEmitter(
        api_url="http://localhost:8787",
        bearer_token="prul_test",  # pragma: allowlist secret
        sender=_NoopSender(),
        rate_limit_per_second=1000,
        rate_limit_burst=1000,
        queue_capacity=64,
        start_thread=False,
    )
    return em


class TestBuildPayloadIncludesProject:
    def test_project_string_is_forwarded(self) -> None:
        em = _make_emitter()
        payload = em._build_payload(
            {
                "switch": "triage",
                "outcome": "correct",
                "phase": "P0",
                "project": "billing-service",
            }
        )
        assert payload is not None
        assert payload["project"] == "billing-service"
        assert payload["switch_name"] == "triage"

    def test_project_absent_is_omitted(self) -> None:
        em = _make_emitter()
        payload = em._build_payload({"switch": "triage", "outcome": "correct", "phase": "P0"})
        assert payload is not None
        assert "project" not in payload

    def test_project_none_is_omitted(self) -> None:
        em = _make_emitter()
        payload = em._build_payload(
            {
                "switch": "triage",
                "outcome": "correct",
                "phase": "P0",
                "project": None,
            }
        )
        assert payload is not None
        assert "project" not in payload

    def test_project_empty_string_is_omitted(self) -> None:
        # Server contract: project is either a meaningful slug or absent.
        em = _make_emitter()
        payload = em._build_payload(
            {
                "switch": "triage",
                "outcome": "correct",
                "phase": "P0",
                "project": "",
            }
        )
        assert payload is not None
        assert "project" not in payload

    def test_project_wrong_type_is_omitted(self) -> None:
        # Defensive: never pass through a non-string project (would
        # break the server-side upsert that joins on a TEXT slug).
        em = _make_emitter()
        payload = em._build_payload(
            {
                "switch": "triage",
                "outcome": "correct",
                "phase": "P0",
                "project": 123,
            }
        )
        assert payload is not None
        assert "project" not in payload


# ---------------------------------------------------------------------------
# End-to-end: decorator → LearnedSwitch.record_verdict → emit("outcome") →
# emitter payload contains project. Pins the integration path the server
# side will consume.
# ---------------------------------------------------------------------------


class TestEndToEndProjectFlow:
    def test_record_verdict_emits_outcome_with_project(self) -> None:
        sender = _NoopSender()
        em = CloudVerdictEmitter(
            api_url="http://localhost:8787",
            bearer_token="prul_test",  # pragma: allowlist secret
            sender=sender,
            rate_limit_per_second=1000,
            rate_limit_burst=1000,
            queue_capacity=64,
        )

        @ml_switch(project="billing-service", telemetry=em)
        def classify(x: str) -> str:
            return "a"

        # One round trip: predict + record. The outcome event reaches the
        # emitter, gets payload-built with project intact, and posts.
        classify.record_verdict(input={"x": "y"}, label="a", outcome="correct")
        em.flush(5.0)

        # At least one payload landed and it carries the project slug.
        assert len(sender.calls) >= 1
        wire = sender.calls[0]
        assert wire["switch_name"] == "classify"
        assert wire["project"] == "billing-service"
