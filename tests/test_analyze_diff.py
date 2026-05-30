# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
#83 — `postrule analyze --diff <ref>`.

Restrict analyzer output to classification sites in files that have
changed (Added / Modified / Renamed) between the working tree and a
caller-supplied git ref. Enables PR-CI workflows like "since main, what
new classification sites did this branch introduce" without forcing the
operator to grep through a full-tree report.

Implementation contract pinned by these tests:

1. `analyze(root, restrict_to_files=[...])` ignores any site whose file is
   not in the supplied set. The walk still respects ignore dirs +
   worktree skipping; the restrict set is intersected on top.

2. `analyze --diff <ref>` from the CLI shells out to
   `git diff --name-only <ref>` (rooted at the analyzer path), filters to
   `*.py`, and passes the result through as `restrict_to_files`.

3. When `--diff <ref>` is set but the path is not a git repo, the CLI
   errors with a clear diagnostic (not a stacktrace) and a non-zero exit.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from postrule.analyzer import analyze
from postrule.cli import cmd_analyze

_PLAIN_RULE = "def classify(text):\n    if 'a' in text:\n        return 'a'\n    return 'b'\n"


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


@pytest.fixture
def repo_with_baseline_and_branch(tmp_path: Path) -> Path:
    """
    Git repo seeded with two classification sites on `main`, then a
    branch `feature` that adds a third. Useful for asserting that
    --diff main sees only the new file.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo / "src" / "a.py", _PLAIN_RULE)
    _write(repo / "src" / "b.py", _PLAIN_RULE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")

    _git(repo, "checkout", "-q", "-b", "feature")
    _write(repo / "src" / "c.py", _PLAIN_RULE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add c")
    return repo


# ---------------------------------------------------------------------------
# analyzer-level contract (restrict_to_files)
# ---------------------------------------------------------------------------


class TestAnalyzeRestrictToFiles:
    def test_restrict_to_subset_keeps_only_those_files(self, tmp_path: Path) -> None:
        _write(tmp_path / "src" / "a.py", _PLAIN_RULE)
        _write(tmp_path / "src" / "b.py", _PLAIN_RULE)
        _write(tmp_path / "src" / "c.py", _PLAIN_RULE)
        report = analyze(tmp_path, restrict_to_files=[tmp_path / "src" / "c.py"])
        assert report.total_sites() == 1
        assert report.sites[0].file_path.endswith("c.py")

    def test_restrict_to_empty_yields_zero_sites(self, tmp_path: Path) -> None:
        _write(tmp_path / "src" / "a.py", _PLAIN_RULE)
        report = analyze(tmp_path, restrict_to_files=[])
        assert report.total_sites() == 0

    def test_restrict_accepts_relative_or_absolute_paths(self, tmp_path: Path) -> None:
        _write(tmp_path / "src" / "a.py", _PLAIN_RULE)
        _write(tmp_path / "src" / "b.py", _PLAIN_RULE)
        # Mix abs + rel forms — both should be normalized.
        report = analyze(
            tmp_path,
            restrict_to_files=[
                tmp_path / "src" / "a.py",
                Path("src/b.py"),
            ],
        )
        assert report.total_sites() == 2

    def test_restrict_intersects_with_ignore_dirs(self, tmp_path: Path) -> None:
        # Restrict listing includes a file under an ignored dir — should
        # still be excluded, because ignore dirs are a hard floor.
        _write(tmp_path / ".venv" / "x.py", _PLAIN_RULE)
        _write(tmp_path / "src" / "a.py", _PLAIN_RULE)
        report = analyze(
            tmp_path,
            restrict_to_files=[tmp_path / ".venv" / "x.py", tmp_path / "src" / "a.py"],
        )
        assert report.total_sites() == 1
        assert report.sites[0].file_path.endswith("src/a.py")


# ---------------------------------------------------------------------------
# CLI integration (`postrule analyze --diff <ref>`)
# ---------------------------------------------------------------------------


def _run_cli(repo: Path, *args: str) -> tuple[int, str, str]:
    # Drive the CLI through the same code path users hit on `postrule
    # <cmd>`, but without depending on the `postrule` script being on
    # PATH or shelling into a different interpreter — keeps the test
    # robust across editable installs / nested venvs.
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from postrule.cli import main; sys.exit(main())",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestAnalyzeDiffCLI:
    def test_diff_main_returns_only_changed_files(
        self, repo_with_baseline_and_branch: Path
    ) -> None:
        repo = repo_with_baseline_and_branch
        code, stdout, stderr = _run_cli(
            repo, "analyze", str(repo), "--diff", "main", "--format", "json"
        )
        assert code == 0, stderr
        import json

        payload = json.loads(stdout)
        files = sorted({s["file_path"] for s in payload["sites"]})
        # Exactly the file that the feature branch added — not the two
        # baseline files that already existed on main.
        assert any(f.endswith("src/c.py") for f in files)
        assert not any(f.endswith("src/a.py") for f in files)
        assert not any(f.endswith("src/b.py") for f in files)

    def test_diff_against_head_returns_no_sites(self, repo_with_baseline_and_branch: Path) -> None:
        # Nothing changed between HEAD and HEAD → empty restrict set →
        # zero sites.
        repo = repo_with_baseline_and_branch
        code, stdout, stderr = _run_cli(
            repo, "analyze", str(repo), "--diff", "HEAD", "--format", "json"
        )
        assert code == 0, stderr
        import json

        payload = json.loads(stdout)
        assert payload["sites"] == []

    def test_diff_in_non_git_directory_errors_clearly(self, tmp_path: Path) -> None:
        _write(tmp_path / "src" / "a.py", _PLAIN_RULE)
        code, stdout, stderr = _run_cli(tmp_path, "analyze", str(tmp_path), "--diff", "main")
        assert code != 0
        # No stacktrace; clear "not a git repo" diagnostic the operator
        # can fix.
        assert "Traceback" not in stderr
        assert "git" in stderr.lower()


# ---------------------------------------------------------------------------
# In-process cmd_analyze coverage. The subprocess tests above pin the
# end-to-end semantics; these call cmd_analyze() directly so pytest-cov
# can instrument the --diff code path (subprocess child interpreters
# don't credit toward the parent coverage report).
# ---------------------------------------------------------------------------


def _build_analyze_args(path: str, **kw: object) -> argparse.Namespace:
    return argparse.Namespace(
        path=path,
        format=kw.get("format", "json"),
        json=kw.get("json", False),
        project_savings=kw.get("project_savings", False),
        report=kw.get("report", False),
        report_out=kw.get("report_out"),
        cost_per_call=kw.get("cost_per_call"),
        llm_provider=kw.get("llm_provider", "default"),
        sort=kw.get("sort", "priority"),
        reverse=kw.get("reverse", False),
        diff=kw.get("diff"),
    )


class TestCmdAnalyzeInProcess:
    def test_diff_against_main_passes_restrict_through(
        self,
        repo_with_baseline_and_branch: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(repo_with_baseline_and_branch)
        rc = cmd_analyze(_build_analyze_args(str(repo_with_baseline_and_branch), diff="main"))
        assert rc == 0
        import json

        out = json.loads(capsys.readouterr().out)
        files = {s["file_path"] for s in out["sites"]}
        assert any(f.endswith("src/c.py") for f in files)
        assert not any(f.endswith("src/a.py") for f in files)

    def test_diff_in_non_git_directory_returns_nonzero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write(tmp_path / "src" / "a.py", _PLAIN_RULE)
        monkeypatch.chdir(tmp_path)
        rc = cmd_analyze(_build_analyze_args(str(tmp_path), diff="main"))
        assert rc != 0
        err = capsys.readouterr().err
        assert "git" in err.lower()

    def test_no_diff_does_not_invoke_git(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Sanity: without --diff, cmd_analyze takes the original path
        # (no git introspection, no restrict_to).
        _write(tmp_path / "src" / "a.py", _PLAIN_RULE)
        monkeypatch.chdir(tmp_path)
        rc = cmd_analyze(_build_analyze_args(str(tmp_path)))
        assert rc == 0
        import json

        out = json.loads(capsys.readouterr().out)
        assert len(out["sites"]) >= 1
