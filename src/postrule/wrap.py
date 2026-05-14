# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""AST-based decorator injection for ``postrule init``.

Given a target file and function name, produce a modified source
that wraps the function with ``@ml_switch``. The adapter:

- Parses the source with the stdlib ``ast`` module.
- Locates the target function by name.
- Infers labels from string literals appearing in ``return``
  statements if none are supplied.
- Emits either the modified source or a unified diff.

Zero new dependencies. Uses only line-based splicing over the
original source so comments, blank lines, and formatting are
preserved untouched outside the two insertion points.
"""

from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass


@dataclass
class WrapResult:
    """Verdict of a wrap operation."""

    original_source: str
    modified_source: str
    function_name: str
    labels: list[str]
    decorator_line: int  # 1-indexed line where @ml_switch was inserted
    import_line: int  # 1-indexed line where the postrule import was inserted
    inferred_labels: bool  # True when labels came from source inspection

    def diff(self, filename: str = "<source>") -> str:
        """Unified diff from original to modified source."""
        return "".join(
            difflib.unified_diff(
                self.original_source.splitlines(keepends=True),
                self.modified_source.splitlines(keepends=True),
                fromfile=f"{filename} (before postrule init)",
                tofile=f"{filename} (after postrule init)",
                lineterm="",
            )
        )


class WrapError(Exception):
    """Raised when the wrap operation cannot be performed."""


def wrap_function(
    source: str,
    function_name: str,
    *,
    author: str,
    labels: list[str] | None = None,
    phase: str = "RULE",
    safety_critical: bool = False,
) -> WrapResult:
    """Wrap ``function_name`` in ``source`` with ``@ml_switch(...)``.

    Behavior:
    - If ``labels`` is None, infer from string literals in the
      target function's ``return`` statements.
    - Insert a ``from postrule import ...`` line at the top of the
      file, after module-level docstrings and any existing
      ``from __future__`` imports.
    - Insert the ``@ml_switch(...)`` decorator directly above the
      function's ``def`` line, preserving indentation.
    - Do not modify any other code.

    Raises ``WrapError`` if the function isn't found or already
    decorated.
    """
    tree = ast.parse(source)
    target = _find_function(tree, function_name)
    if target is None:
        raise WrapError(f"function {function_name!r} not found in source")
    if _has_ml_switch_decorator(target):
        raise WrapError(f"function {function_name!r} is already decorated with @ml_switch")

    # Infer labels if not supplied.
    inferred = False
    if labels is None:
        labels = _infer_labels(target)
        inferred = True
        if not labels:
            raise WrapError(
                f"could not infer labels for {function_name!r}; pass --labels a,b,c explicitly"
            )

    source_lines = source.splitlines(keepends=True)

    # --- Insert import at the top of the file -------------------
    import_line = _find_import_insertion_line(tree, source_lines)
    import_stmt = "from postrule import ml_switch, Phase, SwitchConfig\n"
    # Add a blank line after the import if the next line isn't already blank.
    needs_trailing_blank = (
        import_line < len(source_lines) and source_lines[import_line].strip() != ""
    )
    new_import_block = [import_stmt]
    if needs_trailing_blank:
        new_import_block.append("\n")
    source_lines = source_lines[:import_line] + new_import_block + source_lines[import_line:]

    # --- Insert decorator above the function -------------------
    # Re-parse against the modified source to get accurate line numbers
    # for the target function after the import insertion.
    modified_preamble = "".join(source_lines)
    new_tree = ast.parse(modified_preamble)
    target = _find_function(new_tree, function_name)
    assert target is not None  # we already validated its presence
    decorator_line = target.lineno - 1  # 0-indexed line position of "def ..."
    indent = _function_indent(source_lines[decorator_line])
    decorator_text = _render_decorator(
        labels=labels,
        author=author,
        phase=phase,
        safety_critical=safety_critical,
        indent=indent,
    )
    source_lines = source_lines[:decorator_line] + [decorator_text] + source_lines[decorator_line:]

    return WrapResult(
        original_source=source,
        modified_source="".join(source_lines),
        function_name=function_name,
        labels=labels,
        decorator_line=decorator_line + 1,  # 1-indexed
        import_line=import_line + 1,  # 1-indexed
        inferred_labels=inferred,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    """Find a top-level or class-level function by name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _has_ml_switch_decorator(fn: ast.FunctionDef) -> bool:
    for dec in fn.decorator_list:
        func = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(func, ast.Name) and func.id == "ml_switch":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "ml_switch":
            return True
    return False


def _infer_labels(fn: ast.FunctionDef) -> list[str]:
    """Extract string-literal labels from the function's return statements."""
    seen: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
            value = node.value.value
            if isinstance(value, str) and value and value not in seen:
                seen.append(value)
    return seen


def _find_import_insertion_line(tree: ast.Module, source_lines: list[str]) -> int:
    """Return a 0-indexed line at which the postrule import can be inserted.

    Rules:
    - After a module docstring if present.
    - After any ``from __future__ import ...`` lines.
    - Before any other imports or code.
    """
    idx = 0
    # Skip module docstring.
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        # end_lineno is 1-indexed and inclusive.
        end = getattr(tree.body[0], "end_lineno", tree.body[0].lineno)
        idx = end  # first line after the docstring
    # Skip __future__ imports immediately following the docstring.
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.ImportFrom)
            and stmt.module == "__future__"
            and stmt.lineno - 1 >= idx
        ):
            end = getattr(stmt, "end_lineno", stmt.lineno)
            idx = end
    return idx


def _function_indent(def_line: str) -> str:
    stripped = def_line.lstrip()
    return def_line[: len(def_line) - len(stripped)]


def _render_decorator(
    *,
    labels: list[str],
    author: str,
    phase: str,
    safety_critical: bool,
    indent: str,
) -> str:
    labels_repr = "[" + ", ".join(repr(lbl) for lbl in labels) + "]"
    phase_expr = f"Phase.{phase}"
    if safety_critical:
        config_expr = f"SwitchConfig(phase={phase_expr}, safety_critical=True)"
    else:
        config_expr = f"SwitchConfig(phase={phase_expr})"
    return (
        f"{indent}@ml_switch(\n"
        f"{indent}    labels={labels_repr},\n"
        f"{indent}    author={author!r},\n"
        f"{indent}    config={config_expr},\n"
        f"{indent})\n"
    )


__all__ = ["WrapError", "WrapResult", "wrap_function"]
