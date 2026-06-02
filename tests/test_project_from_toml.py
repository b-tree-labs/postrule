# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#36 — `postrule.toml` as the committed project binding.

Connecting a switch to its cloud project today fuses three concerns
(identity, project binding, sync enablement) into a per-machine ritual.
The committed `postrule.toml` separates the project binding so any
environment with credentials auto-connects to the right project with
zero per-machine config.

Schema:

    [project]
    org     = "your-org"
    project = "your-service"

    [switches.intent]      # optional per-switch override
    project = "your-service"

    [switches.file_intake]
    project = "intake"

Resolution chain (highest priority first):

    1. `@ml_switch(project="…")` explicit kwarg (unchanged)
    2. `postrule.toml [switches.<switch_name>] project` → "<org>/<that>"
    3. `postrule.toml [project] project`                → "<org>/<that>"
    4. `git remote get-url origin`                       → "owner/repo"
    5. `pyproject.toml [project] name`
    6. literal "default"

The new step composes a slug as `"<org>/<project>"` so it matches the
auto-derived shape that the server already accepts in PR-2/PR-3.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from postrule import ml_switch
from postrule.project import derive_project_slug, project_from_postrule_toml

# ---------------------------------------------------------------------------
# project_from_postrule_toml
# ---------------------------------------------------------------------------


def _write_toml(tmp_path: Path, content: str) -> Path:
    (tmp_path / "postrule.toml").write_text(content, encoding="utf-8")
    return tmp_path


class TestProjectFromPostruleToml:
    def test_returns_org_slash_project_when_default_block_present(self, tmp_path: Path) -> None:
        _write_toml(
            tmp_path,
            '[project]\norg = "acme-co"\nproject = "intake"\n',
        )
        assert project_from_postrule_toml(tmp_path) == "acme-co/intake"

    def test_per_switch_override_wins_over_default(self, tmp_path: Path) -> None:
        _write_toml(
            tmp_path,
            (
                '[project]\norg = "acme-co"\nproject = "default-service"\n'
                "\n"
                '[switches.file_intake]\nproject = "intake"\n'
            ),
        )
        # default for unknown switch
        assert (
            project_from_postrule_toml(tmp_path, switch_name="anything_else")
            == "acme-co/default-service"
        )
        # override for the named switch
        assert project_from_postrule_toml(tmp_path, switch_name="file_intake") == "acme-co/intake"

    def test_missing_org_returns_none(self, tmp_path: Path) -> None:
        # An org is mandatory; without it we can't compose a slug. We
        # don't guess — fall through to the git remote / pyproject
        # branches that follow.
        _write_toml(tmp_path, '[project]\nproject = "intake"\n')
        assert project_from_postrule_toml(tmp_path) is None

    def test_missing_project_and_no_switch_override_returns_none(self, tmp_path: Path) -> None:
        _write_toml(tmp_path, '[project]\norg = "acme-co"\n')
        assert project_from_postrule_toml(tmp_path) is None

    def test_missing_project_but_switch_override_present_resolves(self, tmp_path: Path) -> None:
        _write_toml(
            tmp_path,
            ('[project]\norg = "acme-co"\n\n[switches.intent]\nproject = "narrow-service"\n'),
        )
        assert (
            project_from_postrule_toml(tmp_path, switch_name="intent") == "acme-co/narrow-service"
        )

    def test_no_postrule_toml_returns_none(self, tmp_path: Path) -> None:
        assert project_from_postrule_toml(tmp_path) is None

    def test_malformed_toml_returns_none(self, tmp_path: Path) -> None:
        # Decorator import MUST NOT raise. Bad TOML → quiet None.
        _write_toml(tmp_path, "this is = not toml [")
        assert project_from_postrule_toml(tmp_path) is None


# ---------------------------------------------------------------------------
# derive_project_slug priority chain — postrule.toml comes FIRST
# ---------------------------------------------------------------------------


class TestDeriveProjectSlugTomlPriority:
    def test_postrule_toml_beats_pyproject(self, tmp_path: Path) -> None:
        # Both exist; postrule.toml wins.
        _write_toml(tmp_path, '[project]\norg = "acme-co"\nproject = "wins"\n')
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "loser"\n')
        assert derive_project_slug(start=tmp_path) == "acme-co/wins"

    def test_postrule_toml_per_switch_threads_through_derive(self, tmp_path: Path) -> None:
        _write_toml(
            tmp_path,
            (
                '[project]\norg = "acme-co"\nproject = "default-service"\n'
                "\n"
                '[switches.file_intake]\nproject = "intake"\n'
            ),
        )
        assert derive_project_slug(start=tmp_path, switch_name="file_intake") == "acme-co/intake"

    def test_no_postrule_toml_falls_through_to_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "fallback"\n')
        assert derive_project_slug(start=tmp_path) == "fallback"

    def test_no_postrule_toml_no_pyproject_falls_through_to_default(self, tmp_path: Path) -> None:
        assert derive_project_slug(start=tmp_path) == "default"


# ---------------------------------------------------------------------------
# Decorator plumbing — switch_name flows through to the toml lookup
# ---------------------------------------------------------------------------


class TestDecoratorReadsPostruleToml:
    def test_repo_default_picks_up_at_decorate_time(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _write_toml(
            tmp_path,
            '[project]\norg = "acme-co"\nproject = "default-service"\n',
        )
        monkeypatch.chdir(tmp_path)

        @ml_switch()
        def my_switch(x: str) -> str:
            return "a"

        # Wrapper + underlying switch carry the same slug.
        assert my_switch.project == "acme-co/default-service"
        assert my_switch.switch.project == "acme-co/default-service"

    def test_per_switch_override_picked_up_by_switch_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _write_toml(
            tmp_path,
            (
                '[project]\norg = "acme-co"\nproject = "default-service"\n'
                "\n"
                '[switches.file_intake]\nproject = "intake"\n'
            ),
        )
        monkeypatch.chdir(tmp_path)

        @ml_switch()
        def file_intake(x: str) -> str:
            return "a"

        assert file_intake.project == "acme-co/intake"

    def test_explicit_kwarg_still_beats_postrule_toml(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _write_toml(tmp_path, '[project]\norg = "acme-co"\nproject = "would-default"\n')
        monkeypatch.chdir(tmp_path)

        @ml_switch(project="forced/value")
        def my_switch(x: str) -> str:
            return "a"

        assert my_switch.project == "forced/value"


# ---------------------------------------------------------------------------
# #110 — switches_from_postrule_toml: per-switch project map for `postrule status`
# ---------------------------------------------------------------------------
class TestSwitchesFromPostruleToml:
    def test_maps_each_declared_switch_to_resolved_project(self, tmp_path: Path) -> None:
        from postrule.project import switches_from_postrule_toml

        (tmp_path / "postrule.toml").write_text(
            '[project]\norg = "acme"\nproject = "support-triage"\n\n'
            '[switches.intent]\nproject = "support-triage"\n\n'
            '[switches.file_intake]\nproject = "intake"\n',
            encoding="utf-8",
        )
        assert switches_from_postrule_toml(start=tmp_path) == {
            "intent": "acme/support-triage",
            "file_intake": "acme/intake",
        }

    def test_absent_toml_or_switches_returns_empty(self, tmp_path: Path) -> None:
        from postrule.project import switches_from_postrule_toml

        assert switches_from_postrule_toml(start=tmp_path) == {}
        (tmp_path / "postrule.toml").write_text(
            '[project]\norg = "acme"\nproject = "p"\n', encoding="utf-8"
        )
        assert switches_from_postrule_toml(start=tmp_path) == {}
