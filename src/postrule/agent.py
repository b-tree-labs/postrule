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
#
# Additional Use Grant: see LICENSE-BSL. Production use is
# permitted; offering a competing hosted service is not.

"""Agent-facing orchestration — the higher-level flows MCP tools compose.

This is the home for "do the whole thing for me" operations that a coding
agent drives on a user's behalf, plus the cloud-account funnel that turns
an agent-run analysis into an account-creation moment right in the chat.

Three pieces:

- :func:`connection_state` — cheap, local "is an account configured?" check
  (no network). The signal the funnel branches on.
- :func:`connect_nudge` — the in-chat call-to-action shown to *unconnected*
  users: how much they'd retire + how to create a free account without
  leaving the conversation.
- :func:`instrument_codebase` — analyze a repo, rank sites by projected
  savings, and instrument the top-N in a single call (the one-shot tool the
  agent reaches for instead of looping analyze→init by hand).

Account attribution reuses the CLI's reporter so there is exactly one path
that talks to the account (no second copy of the wire format / privacy
posture). Whether per-site detail leaves the machine remains the user's
consented choice — this module only *offers* the cloud, never forces it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def connection_state() -> dict[str, Any]:
    """Local, network-free check: is a cloud account configured here?

    Returns ``{"connected": False}`` when no credentials are stored, else
    ``{"connected": True, "email": ..., "api_url": ...}``. Never raises.
    """
    try:
        from postrule import auth

        creds = auth.load_credentials()
    except Exception:  # noqa: BLE001 — a broken cred file must not break the tool
        creds = None
    if not creds:
        return {"connected": False}
    return {
        "connected": True,
        "email": creds.get("email"),
        "api_url": creds.get("api_url"),
    }


def report_analyze_run(report) -> bool:
    """Attribute an analyze run to the signed-in account, best-effort.

    Delegates to the CLI's reporter (the single source of truth for the
    account wire format + privacy posture); it no-ops when unauthenticated
    or telemetry-opted-out and never raises. Returns whether an account is
    connected (i.e. whether attribution had somewhere to go).
    """
    try:
        from postrule.cli import _maybe_report_analyze_to_account

        _maybe_report_analyze_to_account(report)
    except Exception:  # noqa: BLE001 — attribution must never break the tool
        pass
    return connection_state()["connected"]


def connect_nudge(*, total_sites: int, savings_low: float, savings_high: float) -> dict[str, Any]:
    """The in-chat account-creation call-to-action for unconnected users.

    Returned as a tool-result ``next_step`` so the agent renders it inline
    and can immediately drive ``postrule_connect_start`` — the user creates
    a free account from the conversation, no context switch.
    """
    sites_word = "site" if total_sites == 1 else "sites"
    return {
        "action": "postrule_connect_start",
        "message": (
            f"Found {total_sites} classification {sites_word} worth roughly "
            f"${savings_low:,.0f}–${savings_high:,.0f}/yr to retire. "
            "Create a free Postrule cloud account from right here to save this "
            "analysis to your dashboard and watch each switch graduate from "
            "rule → ML. Call postrule_connect_start, then share the link + code "
            "with the user to finish sign-up — no need to leave the chat."
        ),
    }


def instrument_codebase(
    path: str,
    *,
    top_n: int = 5,
    dry_run: bool = True,
    author: str = "@agent:mcp",
    safety_critical: bool = False,
) -> dict[str, Any]:
    """Analyze ``path``, rank sites by projected savings, instrument the top-N.

    One call replaces the analyze→init loop: returns each selected site with
    its projected annual savings and the unified diff that would instrument
    it (``@ml_switch`` + import + a behavior-preserving wrapper). With
    ``dry_run=True`` (the default) nothing is written — the agent shows the
    diffs and the user applies them (the privacy-max, copy-paste path). With
    ``dry_run=False`` the files are modified in place.

    The result is account-aware: when connected it attributes the run; when
    not, it carries a :func:`connect_nudge` so the agent can offer sign-up.
    """
    from postrule.analyzer import analyze, project_savings
    from postrule.wrap import WrapError, wrap_function

    report = analyze(path)
    # Rank by dollars (highest projected savings first) — that is the
    # "top sites to instrument" ordering the value pitch promises.
    ranked = sorted(project_savings(report), key=lambda p: p.total_high_usd, reverse=True)
    selected = ranked[: max(0, top_n)]

    root = Path(report.root)
    candidates: list[dict[str, Any]] = []
    agg_low = agg_high = 0.0
    for proj in selected:
        site = proj.site
        # Analyzer file paths are relative to report.root; resolve to an
        # absolute path the agent can read, edit, and show the user.
        abs_path = Path(site.file_path)
        if not abs_path.is_absolute():
            abs_path = root / site.file_path
        agg_low += proj.total_low_usd
        agg_high += proj.total_high_usd
        entry: dict[str, Any] = {
            "file": str(abs_path),
            "function_name": site.function_name,
            "line_start": site.line_start,
            "pattern": site.pattern,
            "priority_score": site.priority_score,
            "labels": list(site.labels),
            "savings": {
                "monthly_classifications_est": proj.monthly_classifications_est,
                "total_low_usd": proj.total_low_usd,
                "total_high_usd": proj.total_high_usd,
            },
            "diff": "",
            "wrote_file": False,
            "error": None,
        }
        try:
            src = abs_path.read_text(encoding="utf-8")
            wrapped = wrap_function(
                src,
                site.function_name,
                author=author,
                phase="RULE",
                safety_critical=safety_critical,
            )
            entry["diff"] = wrapped.diff(filename=site.file_path)
            entry["labels"] = list(wrapped.labels)
            if not dry_run:
                abs_path.write_text(wrapped.modified_source, encoding="utf-8")
                entry["wrote_file"] = True
        except (WrapError, SyntaxError, OSError) as e:
            entry["error"] = str(e)
        candidates.append(entry)

    connected = report_analyze_run(report)
    result: dict[str, Any] = {
        "root": report.root,
        "total_sites": report.total_sites(),
        "selected_count": len(selected),
        "candidates": candidates,
        "instrumented_count": sum(1 for c in candidates if c["error"] is None),
        "skipped_count": sum(1 for c in candidates if c["error"] is not None),
        "dry_run": dry_run,
        "projected_savings_total_low_usd": agg_low,
        "projected_savings_total_high_usd": agg_high,
        "connected": connected,
    }
    if not connected:
        result["next_step"] = connect_nudge(
            total_sites=report.total_sites(),
            savings_low=agg_low,
            savings_high=agg_high,
        )
    return result
