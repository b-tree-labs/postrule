# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the AST-based dendra-init wrapper."""

from __future__ import annotations

import ast

import pytest

from dendra.wrap import WrapError, wrap_function

# ---------------------------------------------------------------------------
# Label inference
# ---------------------------------------------------------------------------


class TestLabelInference:
    def test_infers_labels_from_return_strings(self):
        source = (
            "def triage(ticket):\n"
            "    if 'crash' in ticket['title']:\n"
            "        return 'bug'\n"
            "    if 'question' in ticket['title']:\n"
            "        return 'question'\n"
            "    return 'feature_request'\n"
        )
        result = wrap_function(source, "triage", author="@triage:support")
        assert result.inferred_labels is True
        assert set(result.labels) == {"bug", "question", "feature_request"}

    def test_supplied_labels_override_inference(self):
        source = "def triage(x):\n    return 'bug'\n"
        result = wrap_function(
            source,
            "triage",
            author="@triage:support",
            labels=["bug", "feature", "question"],
        )
        assert result.inferred_labels is False
        assert result.labels == ["bug", "feature", "question"]

    def test_no_labels_found_raises(self):
        source = (
            "def triage(x):\n"
            "    return classify(x)\n"  # not a string literal
        )
        with pytest.raises(WrapError, match="could not infer labels"):
            wrap_function(source, "triage", author="@triage:support")


# ---------------------------------------------------------------------------
# Decorator insertion
# ---------------------------------------------------------------------------


class TestDecoratorInsertion:
    def test_decorator_text_is_complete_and_parses(self):
        source = (
            "def triage(ticket):\n"
            "    if 'crash' in ticket:\n"
            "        return 'bug'\n"
            "    return 'feature'\n"
        )
        result = wrap_function(source, "triage", author="@triage:support")
        # Output must still be valid Python.
        ast.parse(result.modified_source)

        text = result.modified_source
        assert "@ml_switch(" in text
        assert "author='@triage:support'" in text
        assert "'bug'" in text
        assert "'feature'" in text
        assert "SwitchConfig(phase=Phase.RULE)" in text

    def test_import_inserted_at_top(self):
        source = "def triage(x):\n    return 'bug'\n"
        result = wrap_function(
            source,
            "triage",
            author="@triage:support",
            labels=["bug", "feature"],
        )
        assert result.modified_source.startswith(
            "from dendra import ml_switch, Phase, SwitchConfig\n"
        )

    def test_import_after_module_docstring(self):
        source = (
            '"""Module docstring.\n\nExplains this module.\n"""\n'
            "def triage(x):\n"
            "    return 'bug'\n"
        )
        result = wrap_function(
            source,
            "triage",
            author="@triage:support",
            labels=["bug"],
        )
        lines = result.modified_source.splitlines()
        # Docstring on lines 1-4, then blank, then import.
        assert lines[0].startswith('"""Module docstring')
        assert "from dendra import" in result.modified_source
        docstring_line_idx = next(i for i, ln in enumerate(lines) if ln.startswith('"""') and i > 0)
        import_line_idx = next(i for i, ln in enumerate(lines) if "from dendra import" in ln)
        assert import_line_idx > docstring_line_idx

    def test_import_after_future_imports(self):
        source = "from __future__ import annotations\n\ndef triage(x):\n    return 'bug'\n"
        result = wrap_function(
            source,
            "triage",
            author="@triage:support",
            labels=["bug"],
        )
        lines = result.modified_source.splitlines()
        assert lines[0] == "from __future__ import annotations"
        future_idx = 0
        dendra_idx = next(i for i, ln in enumerate(lines) if "from dendra import" in ln)
        assert dendra_idx > future_idx

    def test_safety_critical_flag_propagates(self):
        source = "def gate(x):\n    return 'safe'\n"
        result = wrap_function(
            source,
            "gate",
            author="@safety:gate",
            labels=["safe", "pii"],
            safety_critical=True,
        )
        assert "SwitchConfig(phase=Phase.RULE, safety_critical=True)" in result.modified_source

    def test_non_rule_phase_propagates(self):
        source = "def gate(x):\n    return 'safe'\n"
        result = wrap_function(
            source,
            "gate",
            author="@safety:gate",
            labels=["safe"],
            phase="ML_WITH_FALLBACK",
        )
        assert "Phase.ML_WITH_FALLBACK" in result.modified_source

    def test_preserves_surrounding_code(self):
        source = (
            "# top-level comment\n"
            "X = 42\n"
            "\n"
            "def triage(x):\n"
            "    return 'bug'\n"
            "\n"
            "def unrelated():\n"
            "    return X\n"
        )
        result = wrap_function(
            source,
            "triage",
            author="@triage:support",
            labels=["bug"],
        )
        # Comment, assignment, unrelated function all preserved.
        assert "# top-level comment" in result.modified_source
        assert "X = 42" in result.modified_source
        assert "def unrelated():" in result.modified_source
        assert "return X" in result.modified_source


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    def test_function_not_found_raises(self):
        source = "def triage(x):\n    return 'bug'\n"
        with pytest.raises(WrapError, match="not found"):
            wrap_function(
                source,
                "nonexistent",
                author="@triage:support",
                labels=["bug"],
            )

    def test_already_wrapped_raises(self):
        source = (
            "from dendra import ml_switch\n"
            "\n"
            "@ml_switch(labels=['bug'], author='@x:y')\n"
            "def triage(x):\n"
            "    return 'bug'\n"
        )
        with pytest.raises(WrapError, match="already decorated"):
            wrap_function(
                source,
                "triage",
                author="@triage:support",
                labels=["bug"],
            )


# ---------------------------------------------------------------------------
# Diff rendering
# ---------------------------------------------------------------------------


class TestDiff:
    def test_diff_shows_insertions(self):
        source = "def triage(x):\n    return 'bug'\n"
        result = wrap_function(
            source,
            "triage",
            author="@triage:support",
            labels=["bug"],
        )
        diff = result.diff(filename="triage.py")
        assert "+from dendra import ml_switch" in diff
        assert "+@ml_switch(" in diff
        assert "triage.py (before dendra init)" in diff
