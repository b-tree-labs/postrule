# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""New-user journey refinements:

1. `postrule init` emits a preserved-behavior test next to the source by
   default (the test the landing analyzer advertises), with --no-test opt-out
   and idempotent skip.
2. `postrule analyze` surfaces the LLM spend retired in its default text
   output, not only behind --project-savings/--format markdown.
"""

from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path

SAMPLE = (
    "def categorize_ticket(text):\n"
    '    if "refund" in text or "charge" in text:\n'
    '        return "billing"\n'
    '    if "broken" in text:\n'
    '        return "bug"\n'
    '    return "other"\n'
)


def _run_cli(cwd: Path, *args: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from postrule.cli import main; sys.exit(main())",
            *args,
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _sample(tmp_path: Path) -> Path:
    src = tmp_path / "support.py"
    src.write_text(SAMPLE, encoding="utf-8")
    return src


class TestInitEmitsTest:
    def test_init_writes_compilable_preserved_behavior_test(self, tmp_path: Path) -> None:
        _sample(tmp_path)
        code, _, stderr = _run_cli(
            tmp_path, "init", "support.py:categorize_ticket", "--author", "@ben:team"
        )
        assert code == 0, stderr
        test_file = tmp_path / "test_categorize_ticket.py"
        assert test_file.exists(), "init should write the preserved-behavior test by default"
        body = test_file.read_text()
        assert "def test_categorize_ticket_preserves_behavior" in body
        assert "KNOWN_LABELS" in body and "billing" in body
        assert "import pytest" in body
        py_compile.compile(str(test_file), doraise=True)

    def test_no_test_flag_skips(self, tmp_path: Path) -> None:
        _sample(tmp_path)
        code, _, stderr = _run_cli(
            tmp_path,
            "init",
            "support.py:categorize_ticket",
            "--author",
            "@ben:team",
            "--no-test",
        )
        assert code == 0, stderr
        assert not (tmp_path / "test_categorize_ticket.py").exists()

    def test_existing_test_is_not_overwritten(self, tmp_path: Path) -> None:
        _sample(tmp_path)
        sentinel = "# hand-written — do not clobber\n"
        (tmp_path / "test_categorize_ticket.py").write_text(sentinel, encoding="utf-8")
        code, _, stderr = _run_cli(
            tmp_path, "init", "support.py:categorize_ticket", "--author", "@ben:team"
        )
        assert code == 0, stderr
        assert (tmp_path / "test_categorize_ticket.py").read_text() == sentinel
        assert "already exists" in stderr

    def test_dry_run_prints_test_without_writing(self, tmp_path: Path) -> None:
        _sample(tmp_path)
        code, stdout, _ = _run_cli(
            tmp_path,
            "init",
            "support.py:categorize_ticket",
            "--author",
            "@ben:team",
            "--dry-run",
        )
        assert code == 0
        assert "test_categorize_ticket.py" in stdout
        assert "def test_categorize_ticket_preserves_behavior" in stdout
        assert not (tmp_path / "test_categorize_ticket.py").exists()


class TestAnalyzeShowsSavings:
    def test_savings_in_default_text_output(self, tmp_path: Path) -> None:
        _sample(tmp_path)
        code, stdout, stderr = _run_cli(tmp_path, "analyze", ".")
        assert code == 0, stderr
        assert "Estimated LLM spend retired" in stdout
