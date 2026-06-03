# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
#
# Licensed under the Business Source License 1.1 (the "License").
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at LICENSE-BSL in the
# repository root, or at https://mariadb.com/bsl11/.
#
# Change Date:    2030-05-01
# Change License: Apache License, Version 2.0

"""Tests for postrule.agent — agent-facing orchestration + cloud funnel."""

from __future__ import annotations

import textwrap
from pathlib import Path

from postrule import agent


def _write_repo(tmp_path: Path) -> Path:
    # Two classification sites so ranking + top_n selection are exercised.
    (tmp_path / "triage.py").write_text(
        textwrap.dedent(
            """\
            def triage(text):
                if "billing" in text:
                    return "billing"
                if "outage" in text:
                    return "outage"
                return "general"
            """
        )
    )
    (tmp_path / "route.py").write_text(
        textwrap.dedent(
            """\
            def route(kind):
                mapping = {"a": "alpha", "b": "beta", "c": "gamma"}
                return mapping.get(kind, "unknown")
            """
        )
    )
    return tmp_path


class TestConnectionState:
    def test_unconnected_when_no_credentials(self, monkeypatch):
        monkeypatch.setattr("postrule.auth.load_credentials", lambda: None)
        assert agent.connection_state() == {"connected": False}

    def test_connected_surfaces_identity(self, monkeypatch):
        monkeypatch.setattr(
            "postrule.auth.load_credentials",
            lambda: {
                "email": "dev@example.com",
                "api_url": "https://api.postrule.ai",
                "api_key": "k",
            },
        )
        state = agent.connection_state()
        assert state["connected"] is True
        assert state["email"] == "dev@example.com"

    def test_never_raises_on_broken_creds(self, monkeypatch):
        def boom():
            raise RuntimeError("corrupt cred file")

        monkeypatch.setattr("postrule.auth.load_credentials", boom)
        assert agent.connection_state() == {"connected": False}


class TestConnectNudge:
    def test_nudge_carries_savings_and_action(self):
        n = agent.connect_nudge(total_sites=3, savings_low=1200.0, savings_high=4500.0)
        assert n["action"] == "postrule_connect_start"
        assert "3 classification sites" in n["message"]
        assert "$1,200" in n["message"] and "$4,500" in n["message"]

    def test_singular_site_wording(self):
        n = agent.connect_nudge(total_sites=1, savings_low=10.0, savings_high=20.0)
        assert "1 classification site " in n["message"]


class TestInstrumentCodebase:
    def test_dry_run_ranks_by_savings_and_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("postrule.auth.load_credentials", lambda: None)
        repo = _write_repo(tmp_path)
        before = {p: p.read_text() for p in repo.glob("*.py")}

        result = agent.instrument_codebase(str(repo), top_n=1, dry_run=True)

        assert result["total_sites"] >= 2
        assert result["selected_count"] == 1
        assert result["dry_run"] is True
        cand = result["candidates"][0]
        assert cand["diff"]  # a real unified diff to show / copy-paste
        assert cand["savings"]["total_high_usd"] >= cand["savings"]["total_low_usd"] >= 0
        assert result["projected_savings_total_high_usd"] >= 0
        # dry_run must not touch disk
        assert {p: p.read_text() for p in repo.glob("*.py")} == before
        assert all(c["wrote_file"] is False for c in result["candidates"])

    def test_top_n_ranked_descending_by_savings(self, tmp_path, monkeypatch):
        monkeypatch.setattr("postrule.auth.load_credentials", lambda: None)
        repo = _write_repo(tmp_path)
        result = agent.instrument_codebase(str(repo), top_n=5, dry_run=True)
        highs = [c["savings"]["total_high_usd"] for c in result["candidates"]]
        assert highs == sorted(highs, reverse=True)

    def test_unconnected_carries_connect_nudge(self, tmp_path, monkeypatch):
        monkeypatch.setattr("postrule.auth.load_credentials", lambda: None)
        repo = _write_repo(tmp_path)
        result = agent.instrument_codebase(str(repo), dry_run=True)
        assert result["connected"] is False
        assert result["next_step"]["action"] == "postrule_connect_start"

    def test_connected_attributes_run_and_omits_nudge(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "postrule.auth.load_credentials",
            lambda: {
                "email": "dev@example.com",
                "api_url": "https://api.postrule.ai",
                "api_key": "k",
            },
        )
        # Don't actually hit the network: stub the CLI reporter.
        reported = {"n": 0}
        monkeypatch.setattr(
            "postrule.cli._maybe_report_analyze_to_account",
            lambda report: reported.__setitem__("n", reported["n"] + 1),
        )
        repo = _write_repo(tmp_path)
        result = agent.instrument_codebase(str(repo), dry_run=True)
        assert result["connected"] is True
        assert reported["n"] == 1
        assert "next_step" not in result

    def test_apply_writes_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("postrule.auth.load_credentials", lambda: None)
        repo = _write_repo(tmp_path)
        result = agent.instrument_codebase(str(repo), top_n=1, dry_run=False)
        cand = result["candidates"][0]
        assert cand["wrote_file"] is True
        assert "@ml_switch" in Path(cand["file"]).read_text()
