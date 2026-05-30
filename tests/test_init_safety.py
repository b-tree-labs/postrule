# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#84 — `postrule init` git-safety affordances.

The injection rewrites a file in place. Without safety affordances, an
operator running `init` on `main` with uncommitted work creates either a
mixed-purpose commit (instrumentation tangled with their own changes) or
pushes AST mutation straight to main.

Behavior pinned by these tests:

1. **Dirty working tree** — `init` refuses unless `--force` is passed,
   with a diagnostic naming the dirty files. Exit code != 0.

2. **Protected branch** — `init` refuses on `main` / `master` unless
   `--branch <name>` is passed (or `--branch` with no arg, auto-pick a
   sensible name) or `--force`. Exit code != 0.

3. **`--branch NAME`** — creates and switches to branch NAME before
   injection; the injection commit ends up on NAME, not on the protected
   parent.

4. **`--branch` with no arg** — auto-picks `postrule/instrument-<func>`.

5. **`--force`** — overrides both safety checks; behaves like today.

6. **Outside a git repo** — affordances are silent no-ops; the original
   in-place-rewrite behavior is preserved.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

from postrule.cli import (
    _git_create_and_checkout_branch,
    _git_current_branch,
    _git_repo_root,
    _git_tree_is_dirty,
    cmd_init,
)

_PLAIN_RULE = (
    "def triage_ticket(text):\n"
    "    if 'crash' in text:\n"
    "        return 'bug'\n"
    "    return 'question'\n"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=repo,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "HOME": str(repo),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _run_cli(cwd: Path, *args: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from postrule.cli import main; sys.exit(main())",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_repo_on_main(tmp_path: Path) -> Path:
    """Git repo on `main`, clean tree, one wrappable function."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "src" / "triage.py", _PLAIN_RULE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo


@pytest.fixture
def dirty_repo_on_feature(tmp_path: Path) -> Path:
    """Git repo on a feature branch with an unstaged edit. Not main, not clean."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "src" / "triage.py", _PLAIN_RULE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    _git(repo, "checkout", "-q", "-b", "wip")
    # Add an unstaged tweak to a different file so the dirty-tree check
    # bites without polluting the target.
    _write(repo / "src" / "other.py", "# wip\n")
    return repo


# ---------------------------------------------------------------------------
# protected branch
# ---------------------------------------------------------------------------


class TestProtectedBranchRefusal:
    def test_init_on_main_without_branch_or_force_refuses(self, clean_repo_on_main: Path) -> None:
        code, _, stderr = _run_cli(
            clean_repo_on_main,
            "init",
            "src/triage.py:triage_ticket",
            "--author",
            "@team",
        )
        assert code != 0
        assert "main" in stderr.lower() or "protected" in stderr.lower()
        # Diagnostic must point the operator at the fix:
        assert "--branch" in stderr or "--force" in stderr

    def test_init_on_main_with_force_proceeds(self, clean_repo_on_main: Path) -> None:
        code, _, stderr = _run_cli(
            clean_repo_on_main,
            "init",
            "src/triage.py:triage_ticket",
            "--author",
            "@team",
            "--force",
        )
        assert code == 0, stderr
        # File got rewritten in place; we're still on main (force didn't
        # silently create a branch).
        src = (clean_repo_on_main / "src" / "triage.py").read_text()
        assert "@ml_switch" in src
        head_branch = _git(clean_repo_on_main, "rev-parse", "--abbrev-ref", "HEAD").strip()
        assert head_branch == "main"

    def test_init_on_main_with_named_branch_switches_first(self, clean_repo_on_main: Path) -> None:
        code, _, stderr = _run_cli(
            clean_repo_on_main,
            "init",
            "src/triage.py:triage_ticket",
            "--author",
            "@team",
            "--branch",
            "instrument-triage",
        )
        assert code == 0, stderr
        head_branch = _git(clean_repo_on_main, "rev-parse", "--abbrev-ref", "HEAD").strip()
        assert head_branch == "instrument-triage"
        src = (clean_repo_on_main / "src" / "triage.py").read_text()
        assert "@ml_switch" in src


# ---------------------------------------------------------------------------
# dirty tree
# ---------------------------------------------------------------------------


class TestDirtyTreeRefusal:
    def test_init_on_dirty_tree_refuses(self, dirty_repo_on_feature: Path) -> None:
        code, _, stderr = _run_cli(
            dirty_repo_on_feature,
            "init",
            "src/triage.py:triage_ticket",
            "--author",
            "@team",
        )
        assert code != 0
        # Diagnostic mentions the dirty state + the --force escape hatch.
        assert "dirty" in stderr.lower() or "uncommitted" in stderr.lower()
        assert "--force" in stderr
        # File was NOT modified — refusal must be early.
        src = (dirty_repo_on_feature / "src" / "triage.py").read_text()
        assert "@ml_switch" not in src

    def test_init_on_dirty_tree_with_force_proceeds(self, dirty_repo_on_feature: Path) -> None:
        code, _, stderr = _run_cli(
            dirty_repo_on_feature,
            "init",
            "src/triage.py:triage_ticket",
            "--author",
            "@team",
            "--force",
        )
        assert code == 0, stderr
        src = (dirty_repo_on_feature / "src" / "triage.py").read_text()
        assert "@ml_switch" in src


# ---------------------------------------------------------------------------
# outside a git repo
# ---------------------------------------------------------------------------


class TestNonGitRepoIsTransparent:
    def test_init_outside_git_repo_is_unchanged(self, tmp_path: Path) -> None:
        _write(tmp_path / "src" / "triage.py", _PLAIN_RULE)
        code, _, stderr = _run_cli(
            tmp_path,
            "init",
            "src/triage.py:triage_ticket",
            "--author",
            "@team",
        )
        # No git repo = no protected-branch / dirty-tree affordances to
        # enforce. Original behaviour: in-place rewrite, success.
        assert code == 0, stderr
        src = (tmp_path / "src" / "triage.py").read_text()
        assert "@ml_switch" in src


# ---------------------------------------------------------------------------
# In-process helpers + cmd_init coverage. The subprocess-based tests above
# exercise the CLI end-to-end (right semantics, right exit codes), but
# pytest-cov can't instrument the child interpreter, so the cli.py git-
# safety code path looks uncovered to the ratchet. The tests below call
# the helpers + cmd_init directly so they count for the floor.
# ---------------------------------------------------------------------------


def _build_init_args(target: str, **kw: object) -> argparse.Namespace:
    """Stand-in for the argparse.Namespace cmd_init expects."""
    return argparse.Namespace(
        target=target,
        author=kw.get("author", "@team"),
        labels=kw.get("labels"),
        phase=kw.get("phase", "RULE"),
        safety_critical=kw.get("safety_critical", False),
        dry_run=kw.get("dry_run", False),
        auto_lift=kw.get("auto_lift", False),
        with_benchmarks=kw.get("with_benchmarks", False),
        branch=kw.get("branch"),
        force=kw.get("force", False),
    )


def _seed_clean_main(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "src" / "triage.py", _PLAIN_RULE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo


class TestGitHelpers:
    def test_repo_root_resolves_inside_repo(self, tmp_path: Path) -> None:
        repo = _seed_clean_main(tmp_path)
        # Resolve from a nested file path — must walk back to the repo root.
        out = _git_repo_root(repo / "src" / "triage.py")
        assert out is not None
        assert out.resolve() == repo.resolve()

    def test_repo_root_returns_none_outside_repo(self, tmp_path: Path) -> None:
        _write(tmp_path / "lonely.py", "x = 1\n")
        out = _git_repo_root(tmp_path / "lonely.py")
        assert out is None

    def test_current_branch_reports_main_after_init(self, tmp_path: Path) -> None:
        repo = _seed_clean_main(tmp_path)
        assert _git_current_branch(repo) == "main"

    def test_current_branch_returns_none_outside_repo(self, tmp_path: Path) -> None:
        # `rev-parse --abbrev-ref HEAD` errors outside a repo; helper returns None.
        assert _git_current_branch(tmp_path) is None

    def test_tree_is_dirty_false_when_clean(self, tmp_path: Path) -> None:
        repo = _seed_clean_main(tmp_path)
        assert _git_tree_is_dirty(repo) is False

    def test_tree_is_dirty_true_when_unstaged_change(self, tmp_path: Path) -> None:
        repo = _seed_clean_main(tmp_path)
        _write(repo / "src" / "other.py", "# wip\n")
        assert _git_tree_is_dirty(repo) is True

    def test_create_and_checkout_branch_switches_head(self, tmp_path: Path) -> None:
        repo = _seed_clean_main(tmp_path)
        # Override git config so the helper sees a deterministic identity
        # in CI environments that have no user.email configured.
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.com",
            }
        )
        old_env = dict(os.environ)
        try:
            os.environ.update(env)
            assert _git_create_and_checkout_branch(repo, "feature-x") is True
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        assert _git_current_branch(repo) == "feature-x"

    def test_create_and_checkout_branch_returns_false_on_collision(self, tmp_path: Path) -> None:
        repo = _seed_clean_main(tmp_path)
        # Re-using `main` collides — git refuses; helper must report failure
        # so the caller can surface a clean error instead of silently
        # leaving the operator on the wrong branch.
        assert _git_create_and_checkout_branch(repo, "main") is False


class TestCmdInitInProcess:
    """Call cmd_init directly — pytest-cov instruments these calls."""

    def _chdir(self, monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
        monkeypatch.chdir(path)

    def test_cmd_init_refuses_dirty_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _seed_clean_main(tmp_path)
        _git(repo, "checkout", "-q", "-b", "wip")
        _write(repo / "src" / "other.py", "# wip\n")
        self._chdir(monkeypatch, repo)
        rc = cmd_init(_build_init_args("src/triage.py:triage_ticket"))
        assert rc != 0
        err = capsys.readouterr().err
        assert "uncommitted" in err.lower() or "dirty" in err.lower()
        assert "--force" in err

    def test_cmd_init_refuses_protected_main(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _seed_clean_main(tmp_path)
        self._chdir(monkeypatch, repo)
        rc = cmd_init(_build_init_args("src/triage.py:triage_ticket"))
        assert rc != 0
        err = capsys.readouterr().err
        assert "protected" in err.lower() or "main" in err.lower()

    def test_cmd_init_force_on_main_proceeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _seed_clean_main(tmp_path)
        self._chdir(monkeypatch, repo)
        rc = cmd_init(_build_init_args("src/triage.py:triage_ticket", force=True))
        assert rc == 0
        assert "@ml_switch" in (repo / "src" / "triage.py").read_text()

    def test_cmd_init_auto_branch_picks_postrule_instrument_prefix(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = _seed_clean_main(tmp_path)
        self._chdir(monkeypatch, repo)
        rc = cmd_init(_build_init_args("src/triage.py:triage_ticket", branch="__auto__"))
        assert rc == 0
        # Branch name follows the auto-pick convention.
        assert _git_current_branch(repo) == "postrule/instrument-triage_ticket"

    def test_cmd_init_dry_run_skips_safety_checks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # dry-run doesn't write anything; safety affordances should be skipped
        # so the operator can preview from any state. main + dirty + no
        # --force still works.
        repo = _seed_clean_main(tmp_path)
        _write(repo / "src" / "other.py", "# wip\n")
        self._chdir(monkeypatch, repo)
        rc = cmd_init(_build_init_args("src/triage.py:triage_ticket", dry_run=True))
        assert rc == 0
        # File on disk unchanged — dry-run prints the diff to stdout.
        assert "@ml_switch" not in (repo / "src" / "triage.py").read_text()

    def test_cmd_init_outside_repo_is_transparent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write(tmp_path / "src" / "triage.py", _PLAIN_RULE)
        self._chdir(monkeypatch, tmp_path)
        rc = cmd_init(_build_init_args("src/triage.py:triage_ticket"))
        assert rc == 0
        assert "@ml_switch" in (tmp_path / "src" / "triage.py").read_text()
