# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Drift detection for generated files in ``__dendra_generated__/``.

When the user runs ``dendra init --auto-lift`` against a function, Dendra
emits a generated module that mirrors the function's structure into a
``Switch`` subclass. If the user later edits the source function, the
generated file goes stale. ``dendra refresh`` regenerates on drift;
``dendra refresh --check`` is the CI gate; ``dendra doctor`` repairs
missing/orphaned files.

This file specifies the contracts. TDD-first, implementation in
``src/dendra/refresh.py``.
"""

from __future__ import annotations

import pytest

# Module under test — created next.
from dendra.refresh import (
    DriftStatus,
    GeneratedHeader,
    ast_hash,
    detect_drift,
    parse_generated_header,
    write_generated_file,
)

# ----------------------------------------------------------------------
# AST hash — must be stable under cosmetic changes, sensitive to logic.
# ----------------------------------------------------------------------


class TestAstHashStability:
    """The hash should reflect *meaning*, not formatting."""

    def test_same_source_same_hash(self):
        src = "def f(x):\n    return x + 1\n"
        assert ast_hash(src) == ast_hash(src)

    def test_extra_whitespace_same_hash(self):
        a = "def f(x):\n    return x + 1\n"
        b = "def f(x):\n\n    return x + 1\n\n"
        assert ast_hash(a) == ast_hash(b)

    def test_comment_changes_same_hash(self):
        a = "def f(x):\n    return x + 1\n"
        b = "def f(x):\n    # added comment\n    return x + 1\n"
        assert ast_hash(a) == ast_hash(b)

    def test_docstring_change_changes_hash(self):
        """Docstrings are part of the AST. We hash them in (so a docstring
        update triggers regeneration; that's load-bearing for places where
        the generated code surfaces the docstring to LLM prompts)."""
        a = 'def f(x):\n    """v1"""\n    return x + 1\n'
        b = 'def f(x):\n    """v2"""\n    return x + 1\n'
        assert ast_hash(a) != ast_hash(b)

    def test_logic_change_changes_hash(self):
        a = "def f(x):\n    return x + 1\n"
        b = "def f(x):\n    return x + 2\n"
        assert ast_hash(a) != ast_hash(b)

    def test_local_variable_rename_changes_hash(self):
        """Variable renames matter — lifter output may depend on names
        (e.g., evidence field names derive from bound locals)."""
        a = "def f(x):\n    user = x\n    return user\n"
        b = "def f(x):\n    person = x\n    return person\n"
        assert ast_hash(a) != ast_hash(b)


# ----------------------------------------------------------------------
# Generated file header — written by lifters, parsed by refresh.
# ----------------------------------------------------------------------


class TestGeneratedHeaderRoundTrip:
    def test_write_and_parse_round_trip(self, tmp_path):
        src = "def route_user(text):\n    return 'standard'\n"
        gen_body = "class RouteUserSwitch:\n    pass\n"
        out_path = tmp_path / "__dendra_generated__" / "routing__route_user.py"

        write_generated_file(
            out_path,
            source_module="myapp.routing",
            source_function="route_user",
            source_ast_hash=ast_hash(src),
            content=gen_body,
            dendra_version="1.0.0",
        )
        header = parse_generated_header(out_path.read_text())

        assert isinstance(header, GeneratedHeader)
        assert header.source_module == "myapp.routing"
        assert header.source_function == "route_user"
        assert header.source_ast_hash == ast_hash(src)
        assert header.dendra_version == "1.0.0"
        # The generated content hash is over body-after-header; manual
        # edits to the body would invalidate it.
        assert header.generated_content_hash  # non-empty

    def test_parse_missing_header_raises(self, tmp_path):
        bad = "# just a normal Python file\nprint('hi')\n"
        with pytest.raises(ValueError, match=r"not.*Dendra.*generated"):
            parse_generated_header(bad)


# ----------------------------------------------------------------------
# Drift detection — three buckets, four outcomes.
# ----------------------------------------------------------------------


class TestDriftDetection:
    """detect_drift() returns one of: UP_TO_DATE, SOURCE_DRIFT,
    USER_EDITED, ORPHANED, MISSING_GENERATED."""

    def _setup(self, tmp_path, src_text, gen_body):
        """Set up a tiny project with one source function + its generated file."""
        src_path = tmp_path / "myapp" / "routing.py"
        src_path.parent.mkdir(parents=True)
        src_path.write_text(src_text)
        gen_path = tmp_path / "myapp" / "__dendra_generated__" / "routing__route_user.py"
        write_generated_file(
            gen_path,
            source_module="myapp.routing",
            source_function="route_user",
            source_ast_hash=ast_hash(src_text),
            content=gen_body,
            dendra_version="1.0.0",
        )
        return src_path, gen_path

    def test_unchanged_source_is_up_to_date(self, tmp_path):
        src = "def route_user(text):\n    return 'standard'\n"
        body = "class RouteUserSwitch:\n    pass\n"
        src_path, gen_path = self._setup(tmp_path, src, body)
        status = detect_drift(src_path, "route_user", gen_path)
        assert status is DriftStatus.UP_TO_DATE

    def test_changed_source_is_source_drift(self, tmp_path):
        src = "def route_user(text):\n    return 'standard'\n"
        body = "class RouteUserSwitch:\n    pass\n"
        src_path, gen_path = self._setup(tmp_path, src, body)
        # User edits the source function
        src_path.write_text("def route_user(text):\n    return 'premium'\n")
        status = detect_drift(src_path, "route_user", gen_path)
        assert status is DriftStatus.SOURCE_DRIFT

    def test_user_edited_generated_file_detected(self, tmp_path):
        src = "def route_user(text):\n    return 'standard'\n"
        body = "class RouteUserSwitch:\n    pass\n"
        src_path, gen_path = self._setup(tmp_path, src, body)
        # User manually appends to generated file
        gen_path.write_text(gen_path.read_text() + "\n# manual edit\n")
        status = detect_drift(src_path, "route_user", gen_path)
        assert status is DriftStatus.USER_EDITED

    def test_missing_generated_file(self, tmp_path):
        src_path = tmp_path / "myapp" / "routing.py"
        src_path.parent.mkdir(parents=True)
        src_path.write_text("def route_user(text):\n    return 'standard'\n")
        gen_path = tmp_path / "myapp" / "__dendra_generated__" / "routing__route_user.py"
        # gen_path doesn't exist
        status = detect_drift(src_path, "route_user", gen_path)
        assert status is DriftStatus.MISSING_GENERATED

    def test_orphaned_generated_file_when_source_function_gone(self, tmp_path):
        src = "def route_user(text):\n    return 'standard'\n"
        body = "class RouteUserSwitch:\n    pass\n"
        src_path, gen_path = self._setup(tmp_path, src, body)
        # User deleted the function (source file still exists, function doesn't)
        src_path.write_text("def some_other_function(): pass\n")
        status = detect_drift(src_path, "route_user", gen_path)
        assert status is DriftStatus.ORPHANED


# ----------------------------------------------------------------------
# Header is malformed → loud diagnostic, never silent.
# ----------------------------------------------------------------------


class TestMalformedHeaderHandling:
    def test_corrupted_hash_raises(self, tmp_path):
        gen_path = tmp_path / "broken.py"
        gen_path.write_text(
            "# Generated by Dendra v1.0.0 - DO NOT EDIT\n"
            "# Source: myapp.routing:route_user\n"
            "# AST hash: not-a-real-hash\n"
            "# Content hash: also-not-real\n"
            "\n"
            "pass\n"
        )
        with pytest.raises(ValueError, match=r"hash"):
            parse_generated_header(gen_path.read_text())
