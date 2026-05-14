# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Site fingerprint — blake2b over normalized AST shape.

A site fingerprint is a non-reversible hash of the *structure* of a
classification site, with all identifiers, string literals, and
numeric literals replaced by stable placeholders. The same algorithm
produces the same fingerprint for two functions that differ only in
variable names, label values, or local renames — which is what we
want for cohort matching ("how many sites of this AST shape have
graduated, and at what outcome count?") without leaking content.

The fingerprint is intentionally NOT a content hash. Content hashes
of source files would let an attacker run a dictionary attack against
the cohort warehouse: hash a target file, look it up. The
shape-normalization step makes that attack useless because thousands
of distinct classifiers collapse to the same shape signature.
"""

from __future__ import annotations

import ast
import hashlib
from typing import Any


def _normalize_node(node: ast.AST) -> Any:
    """Recursively normalize an AST node to a hashable shape signature.

    The output is a nested tuple: ``(node_type_name, *normalized_children)``.
    All identifiers, constants, and attribute names are replaced with
    stable placeholders so the resulting shape doesn't depend on what
    the user named their variables, what their labels were, or what
    string constants they returned.
    """
    if isinstance(node, ast.AST):
        children: list[Any] = []
        for field_name, value in ast.iter_fields(node):
            children.append((field_name, _normalize_node(value)))
        return (type(node).__name__, tuple(children))
    if isinstance(node, list):
        return tuple(_normalize_node(item) for item in node)
    # Identifiers and constants — squash to placeholders.
    if isinstance(node, str):
        return "<str>"
    if isinstance(node, (int, float, complex)):
        return "<num>"
    if isinstance(node, (bytes, bool)) or node is None:
        return f"<{type(node).__name__}>"
    return repr(type(node).__name__)


def _strip_identifiers(shape: Any) -> Any:
    """Replace identifier-bearing fields with placeholders.

    ``ast.Name(id="best_label")`` and ``ast.Name(id="result")`` should
    fingerprint identically. Attribute names, function names, argument
    names, and class names get the same treatment so a user's
    business-domain naming never bleeds into the cohort signature.
    """
    if not isinstance(shape, tuple):
        return shape
    if len(shape) >= 1 and isinstance(shape[0], str):
        node_type = shape[0]
        # Fields whose presence carries semantic shape but whose
        # *value* is identifier-text (and therefore content-leaky).
        # Squash those to a constant placeholder.
        identifier_fields = {
            "id",  # ast.Name
            "attr",  # ast.Attribute
            "arg",  # ast.arg
            "name",  # ast.FunctionDef, ast.ClassDef, etc.
            "module",  # ast.ImportFrom
        }
        rebuilt_children = []
        for child in shape[1:]:
            if (
                isinstance(child, tuple)
                and len(child) == 2
                and isinstance(child[0], str)
                and child[0] in identifier_fields
            ):
                rebuilt_children.append((child[0], "<id>"))
            else:
                rebuilt_children.append(_strip_identifiers(child))
        return (node_type, tuple(rebuilt_children))
    return tuple(_strip_identifiers(item) for item in shape)


def fingerprint_function(source: str) -> str:
    """Compute a site fingerprint from a function's source text.

    The source must contain at least one top-level function definition.
    The fingerprint is the blake2b digest of the canonicalized,
    identifier-stripped, literal-stripped AST shape.

    Returns a 32-character lowercase hex digest (16-byte blake2b).

    Raises ValueError if the source does not parse or contains no
    top-level function definition.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ValueError(f"source did not parse: {e.msg}") from e
    fn: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = node
            break
    if fn is None:
        raise ValueError("no top-level function definition found")
    shape = _normalize_node(fn)
    stripped = _strip_identifiers(shape)
    canonical = repr(stripped).encode("utf-8")
    return hashlib.blake2b(canonical, digest_size=16).hexdigest()


def fingerprint_repo_files(file_paths: list[str]) -> str:
    """Compute a repo-level fingerprint from a list of file paths.

    The fingerprint dedupes the same repo across days: if the user
    runs ``postrule analyze .`` twice on the same code, we get the same
    repo-fingerprint. The hash is over the *sorted set of file paths*
    only — not file content — so the cohort can count "how many
    distinct repos are running Postrule" without ingesting any code.

    Returns a 16-character lowercase hex digest (8-byte blake2b).
    """
    sorted_paths = sorted(set(file_paths))
    canonical = "\n".join(sorted_paths).encode("utf-8")
    return hashlib.blake2b(canonical, digest_size=8).hexdigest()


__all__ = ["fingerprint_function", "fingerprint_repo_files"]
