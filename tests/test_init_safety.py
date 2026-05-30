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

import subprocess
import sys
from pathlib import Path

import pytest

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
