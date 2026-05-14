# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Round-trip contract: ``postrule init --auto-lift`` followed by
``postrule refresh --check`` must report no drift on the freshly-lifted
file.

Regression test for the v1.1 launch-blocker reported in
``docs/working/real-codebase-testing-2026-04-28.md``: the writer hashed
the pre-decoration full-file source while the reader hashed only the
post-decoration extracted function source, guaranteeing a mismatch on
every fresh init.
"""

from __future__ import annotations

from postrule.cli import main


def _run_cli(argv, capsys):
    try:
        code = main(argv)
    except SystemExit as e:
        code = int(e.code) if e.code is not None else 0
    out = capsys.readouterr()
    return code, out.out, out.err


_SRC = (
    "def triage(ticket):\n"
    "    t = ticket.get('title', '').lower()\n"
    "    if 'crash' in t:\n"
    "        return 'bug'\n"
    "    if '?' in t:\n"
    "        return 'question'\n"
    "    return 'feature'\n"
)


class TestInitRefreshRoundTrip:
    """init --auto-lift then refresh --check must be a clean no-op."""

    def test_init_auto_lift_then_refresh_check_reports_no_drift(self, tmp_path, capsys):
        src = tmp_path / "triage.py"
        src.write_text(_SRC)
        code, _, _ = _run_cli(
            [
                "init",
                f"{src}:triage",
                "--author",
                "@test:bot",
                "--auto-lift",
            ],
            capsys,
        )
        assert code == 0

        # Generated file must exist next to the source.
        gen_dir = tmp_path / "__postrule_generated__"
        gen_files = [p for p in gen_dir.glob("*.py") if p.name != "__init__.py"]
        assert len(gen_files) == 1, f"expected exactly one generated file, got {gen_files}"

        # Refresh --check must report exit 0 (no drift).
        code, out, err = _run_cli(["refresh", "--check", str(tmp_path)], capsys)
        assert code == 0, (
            f"refresh --check reported drift right after init --auto-lift.\n"
            f"stdout:\n{out}\nstderr:\n{err}"
        )
        assert "source_drift:      0" in out

    def test_cosmetic_edit_to_source_does_not_trigger_drift(self, tmp_path, capsys):
        """Adding a comment inside the function body should NOT change the
        AST hash and therefore must not trigger drift."""
        src = tmp_path / "triage.py"
        src.write_text(_SRC)
        code, _, _ = _run_cli(
            [
                "init",
                f"{src}:triage",
                "--author",
                "@test:bot",
                "--auto-lift",
            ],
            capsys,
        )
        assert code == 0

        # Inject a comment line in the function body. AST is unchanged.
        modified = src.read_text()
        # Insert `# cosmetic comment` after the `t = ticket.get(...)` line.
        marker = "    t = ticket.get('title', '').lower()\n"
        assert marker in modified
        modified = modified.replace(marker, marker + "    # cosmetic comment, not a logic change\n")
        src.write_text(modified)

        code, out, err = _run_cli(["refresh", "--check", str(tmp_path)], capsys)
        assert code == 0, (
            f"refresh --check reported drift after a comment-only edit.\n"
            f"stdout:\n{out}\nstderr:\n{err}"
        )

    def test_semantic_edit_to_source_triggers_drift(self, tmp_path, capsys):
        """Changing a return label is a real logic change and MUST flag drift."""
        src = tmp_path / "triage.py"
        src.write_text(_SRC)
        code, _, _ = _run_cli(
            [
                "init",
                f"{src}:triage",
                "--author",
                "@test:bot",
                "--auto-lift",
            ],
            capsys,
        )
        assert code == 0

        # Change a return-label string. AST changes.
        modified = src.read_text()
        modified = modified.replace("return 'bug'", "return 'defect'")
        src.write_text(modified)

        code, out, err = _run_cli(["refresh", "--check", str(tmp_path)], capsys)
        assert code == 1, (
            f"refresh --check failed to detect a real semantic edit.\n"
            f"stdout:\n{out}\nstderr:\n{err}"
        )
        assert "source_drift" in out


# ---------------------------------------------------------------------------
# Direct exercise of the writer/reader contract — useful when CLI subprocess
# behavior is hard to debug.
# ---------------------------------------------------------------------------


class TestWriterReaderHashSymmetry:
    """The hash the writer stores must match the hash the reader computes,
    given the same source state."""

    def test_writer_stores_hash_that_reader_reproduces(self, tmp_path, capsys):
        src = tmp_path / "triage.py"
        src.write_text(_SRC)
        code, _, _ = _run_cli(
            [
                "init",
                f"{src}:triage",
                "--author",
                "@test:bot",
                "--auto-lift",
            ],
            capsys,
        )
        assert code == 0

        from postrule.refresh import (
            _extract_function_source,
            ast_hash,
            parse_generated_header,
        )

        gen_dir = tmp_path / "__postrule_generated__"
        gen_files = [p for p in gen_dir.glob("*.py") if p.name != "__init__.py"]
        assert len(gen_files) == 1
        gen_path = gen_files[0]

        header = parse_generated_header(gen_path.read_text())

        post_decoration_source = src.read_text()
        fn_src = _extract_function_source(post_decoration_source, "triage")
        assert fn_src is not None
        reader_hash = ast_hash(fn_src)

        assert header.source_ast_hash == reader_hash, (
            "writer hash and reader hash must agree on the same source state. "
            f"writer={header.source_ast_hash} reader={reader_hash}"
        )
