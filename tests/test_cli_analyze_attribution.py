# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#63 — `postrule analyze` must attribute runs to the signed-in account.

Before this, analyze was local-only: the dashboard's "Analyze your first
repo" onboarding step never checked off and the analyzer's findings never
surfaced per-account. We add an account-attributed, privacy-safe run
report (counts + projected-savings range + project slug — never per-site
code/content, per the "decisions, never your data" contract #53).

Behaviour pinned here:
  1. Authenticated → analyze POSTs a run summary to /v1/analyze.
  2. The summary is run-level only (no per-site / code fields).
  3. Not authenticated → no POST.
  4. POSTRULE_NO_TELEMETRY opt-out → no POST.
  5. A failing POST never breaks the analyze command.
"""

from __future__ import annotations

from postrule import auth as _auth
from postrule import cli as _cli


class _Site:
    pattern = "keyword"
    regime = "balanced"
    lift_status = "candidate"
    hazards: list = []


class _Report:
    files_scanned = 12
    sites = [_Site(), _Site()]

    def total_sites(self) -> int:
        return len(self.sites)

    def already_dendrified_count(self) -> int:
        return 1


def test_summary_is_run_level_only_no_per_site_content() -> None:
    summary = _cli._analyze_account_summary(_Report())
    assert summary["total_sites"] == 2
    assert summary["files_scanned"] == 12
    assert summary["already_instrumented"] == 1
    assert "project" in summary
    assert "projected_annual_savings_low_usd" in summary
    # No per-site / code-bearing fields ride along.
    for forbidden in ("sites", "source", "code", "file", "lineno", "snippet"):
        assert forbidden not in summary


def test_authenticated_run_posts_summary(monkeypatch) -> None:
    monkeypatch.delenv("POSTRULE_NO_TELEMETRY", raising=False)
    monkeypatch.setattr(
        _auth,
        "load_credentials",
        lambda: {
            "api_key": "prul_live_x",  # pragma: allowlist secret
            "api_url": "https://api.test",
        },
    )
    captured: dict = {}

    def fake_report(api_url, api_key, summary):
        captured["url"] = api_url
        captured["summary"] = summary
        return True

    monkeypatch.setattr(_cli, "_report_analyze_run", fake_report)
    _cli._maybe_report_analyze_to_account(_Report())
    assert captured["url"] == "https://api.test"
    assert captured["summary"]["total_sites"] == 2


def test_not_authenticated_does_not_post(monkeypatch) -> None:
    monkeypatch.delenv("POSTRULE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("POSTRULE_API_KEY", raising=False)
    monkeypatch.setattr(_auth, "load_credentials", lambda: None)
    called = {"n": 0}
    monkeypatch.setattr(
        _cli, "_report_analyze_run", lambda *a: called.__setitem__("n", called["n"] + 1)
    )
    _cli._maybe_report_analyze_to_account(_Report())
    assert called["n"] == 0


def test_opt_out_does_not_post(monkeypatch) -> None:
    monkeypatch.setenv("POSTRULE_NO_TELEMETRY", "1")
    monkeypatch.setattr(
        _auth,
        "load_credentials",
        lambda: {"api_key": "prul_live_x"},  # pragma: allowlist secret
    )
    called = {"n": 0}
    monkeypatch.setattr(
        _cli, "_report_analyze_run", lambda *a: called.__setitem__("n", called["n"] + 1)
    )
    _cli._maybe_report_analyze_to_account(_Report())
    assert called["n"] == 0


def test_failing_post_never_raises(monkeypatch) -> None:
    monkeypatch.delenv("POSTRULE_NO_TELEMETRY", raising=False)
    monkeypatch.setattr(
        _auth,
        "load_credentials",
        lambda: {"api_key": "prul_live_x"},  # pragma: allowlist secret
    )

    def boom(*a):
        raise RuntimeError("network down")

    monkeypatch.setattr(_cli, "_report_analyze_run", boom)
    # Must swallow — attribution can't break analyze.
    _cli._maybe_report_analyze_to_account(_Report())
