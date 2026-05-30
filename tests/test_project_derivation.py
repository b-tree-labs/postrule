# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#107 PR 1 — SDK foundation for the "project" grouping unit.

Postrule's per-codebase grouping is "project" (Account → Project →
Switch). The SDK side of the MVP:

  1. `@ml_switch(project="...")` — explicit operator override.
  2. When not supplied, auto-derive from environment in priority order:
       a. ``git remote get-url origin``  →  ``<owner>/<repo>`` slug
       b. ``pyproject.toml [project] name``  →  the project name
       c. literal ``"default"``  →  fallback so no switch disappears

This PR introduces the derivation helper + plumbs the kwarg through the
wrapper. The verdict wire payload, server persistence, and dashboard
surface ship in follow-on PRs so the privacy boundary (project info is
account-scoped, never cohort-shared) gets a separate, focused review.

Tests cover the helper's three branches + the kwarg propagation through
`@ml_switch`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from postrule import ml_switch
from postrule.project import derive_project_slug, project_from_git_remote, project_from_pyproject


def _run(cwd: Path, *args: str) -> None:
    subprocess.check_call(
        ["git", *args],
        cwd=cwd,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )


def _seed_git_repo_with_remote(tmp_path: Path, remote_url: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "remote", "add", "origin", remote_url)
    return repo


# ---------------------------------------------------------------------------
# project_from_git_remote
# ---------------------------------------------------------------------------


class TestProjectFromGitRemote:
    @pytest.mark.parametrize(
        "remote,expected",
        [
            ("https://github.com/owner/repo.git", "owner/repo"),
            ("https://github.com/owner/repo", "owner/repo"),
            ("git@github.com:owner/repo.git", "owner/repo"),
            ("ssh://git@github.com/owner/repo.git", "owner/repo"),
            ("https://gitlab.com/group/sub/repo.git", "sub/repo"),  # last 2 segments
        ],
    )
    def test_canonical_remotes_yield_owner_repo_slug(
        self, tmp_path: Path, remote: str, expected: str
    ) -> None:
        repo = _seed_git_repo_with_remote(tmp_path, remote)
        assert project_from_git_remote(repo) == expected

    def test_returns_none_outside_a_repo(self, tmp_path: Path) -> None:
        # tmp_path contains no .git — derivation must fall through quietly.
        assert project_from_git_remote(tmp_path) is None

    def test_returns_none_when_origin_remote_missing(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _run(repo, "init", "-q", "-b", "main")
        # init but no remote add — origin doesn't exist.
        assert project_from_git_remote(repo) is None


# ---------------------------------------------------------------------------
# project_from_pyproject
# ---------------------------------------------------------------------------


class TestProjectFromPyproject:
    def test_reads_project_name_field(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "billing-service"\nversion = "1.0.0"\n'
        )
        assert project_from_pyproject(tmp_path) == "billing-service"

    def test_returns_none_when_no_pyproject(self, tmp_path: Path) -> None:
        assert project_from_pyproject(tmp_path) is None

    def test_returns_none_when_pyproject_has_no_name(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
        assert project_from_pyproject(tmp_path) is None

    def test_returns_none_when_pyproject_is_malformed(self, tmp_path: Path) -> None:
        # Quietly fall through rather than crashing user code at import.
        (tmp_path / "pyproject.toml").write_text("this is = not toml [")
        assert project_from_pyproject(tmp_path) is None


# ---------------------------------------------------------------------------
# derive_project_slug — priority chain
# ---------------------------------------------------------------------------


class TestDeriveProjectSlug:
    def test_prefers_git_remote_when_present(self, tmp_path: Path) -> None:
        repo = _seed_git_repo_with_remote(tmp_path, "https://github.com/o/r.git")
        # Add a pyproject too — the git remote wins.
        (repo / "pyproject.toml").write_text('[project]\nname = "should-not-win"\n')
        assert derive_project_slug(start=repo) == "o/r"

    def test_falls_back_to_pyproject_when_no_remote(self, tmp_path: Path) -> None:
        # No git repo, just a pyproject — derivation reaches the second branch.
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "pre-push-app"\n')
        assert derive_project_slug(start=tmp_path) == "pre-push-app"

    def test_falls_back_to_default_when_nothing_resolves(self, tmp_path: Path) -> None:
        assert derive_project_slug(start=tmp_path) == "default"

    def test_default_start_is_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # When no start is supplied, use the process CWD — the most useful
        # default for `@ml_switch(...)` at import time.
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "cwd-default"\n')
        monkeypatch.chdir(tmp_path)
        assert derive_project_slug() == "cwd-default"


# ---------------------------------------------------------------------------
# @ml_switch(project=...) plumbing
# ---------------------------------------------------------------------------


class TestMlSwitchProjectKwarg:
    def test_explicit_project_attaches_to_wrapper(self) -> None:
        @ml_switch(project="billing-service")
        def classify(x: str) -> str:
            return "a" if "a" in x else "b"

        # Surfaced for introspection — operators / SDK helpers / dashboard
        # can read this without re-deriving.
        assert classify.project == "billing-service"

    def test_no_project_kwarg_auto_derives(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # No project kwarg and a pyproject.toml in CWD → wrapper picks
        # the auto-derived slug.
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "auto-derived"\n')
        monkeypatch.chdir(tmp_path)

        @ml_switch()
        def classify(x: str) -> str:
            return "a"

        assert classify.project == "auto-derived"

    def test_no_project_no_repo_falls_through_to_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # No kwarg, no git, no pyproject → 'default' bucket so nothing
        # disappears from a future dashboard view.
        monkeypatch.chdir(tmp_path)

        @ml_switch()
        def classify(x: str) -> str:
            return "a"

        assert classify.project == "default"
