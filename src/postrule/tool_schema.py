# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""LLM tool-schema coercion (#54).

Postrule switches are frequently registered as LLM "tools"/"functions".
The JSON schema most authors reach for comes from Pydantic's
``model_json_schema()`` — but that dialect carries keys (``title``,
``default``, ``$schema``, ``$defs``/``$ref`` …) and an ``anyOf`` Optional
encoding that some provider SDKs reject outright. The most common casualty
is ``google.generativeai``'s ``FunctionDeclaration``, which raises
``Unknown field for Schema: title`` / ``anyOf`` and silently drops the
tool.

Rather than make every integrator rediscover and re-implement the same
non-obvious coercion, :func:`coerce_tool_schema` bakes it in. It is pure
(never mutates its input), recursive, resolves ``$ref`` against ``$defs``
before stripping them (so nested models survive), and is keyed by
``target`` so the SDK stays provider-agnostic.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

__all__ = ["coerce_tool_schema", "SUPPORTED_TOOL_TARGETS"]

# Keys that carry no meaning to the constrained tool-schema dialects and that
# at least one major provider SDK rejects. ``$defs``/``definitions`` are
# resolved (inlined) before being stripped — see ``_resolve_refs``.
_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "title",
        "default",
        "additionalProperties",
        "$schema",
        "$defs",
        "definitions",
        "examples",
        "readOnly",
        "writeOnly",
        "discriminator",
        "$id",
        "$comment",
    }
)

SUPPORTED_TOOL_TARGETS = frozenset({"gemini"})


def coerce_tool_schema(schema: dict[str, Any], *, target: str = "gemini") -> dict[str, Any]:
    """Return a copy of ``schema`` coerced into a provider-accepted dialect.

    Args:
        schema: A JSON schema dict, typically from ``model_json_schema()``.
        target: The destination provider dialect. Currently ``"gemini"``
            (the only target whose Schema proto needs the coercion); the
            argument exists so callers stay provider-agnostic and so new
            targets can be added without changing call sites.

    The input is never mutated.
    """
    if target not in SUPPORTED_TOOL_TARGETS:
        raise ValueError(
            f"unknown tool-schema target {target!r}; supported: {sorted(SUPPORTED_TOOL_TARGETS)}"
        )
    # Resolve $ref against $defs first so nested models survive the strip step,
    # then coerce. deepcopy keeps the caller's schema pristine.
    defs = _collect_defs(schema)
    resolved = _resolve_refs(deepcopy(schema), defs, seen=frozenset())
    return _coerce(resolved)


def _collect_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """Merge top-level ``$defs`` and ``definitions`` into one lookup table."""
    defs: dict[str, Any] = {}
    for key in ("$defs", "definitions"):
        section = schema.get(key)
        if isinstance(section, dict):
            defs.update(section)
    return defs


def _ref_name(ref: str) -> str | None:
    """Extract the local definition name from a ``#/$defs/Name`` pointer."""
    for prefix in ("#/$defs/", "#/definitions/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return None


def _resolve_refs(node: Any, defs: dict[str, Any], *, seen: frozenset[str]) -> Any:
    """Inline local ``$ref`` pointers; tolerate cycles by not re-expanding."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = _ref_name(ref)
            if name is not None and name in defs and name not in seen:
                target = deepcopy(defs[name])
                # Sibling keys on the $ref node (e.g. description) win.
                siblings = {k: v for k, v in node.items() if k != "$ref"}
                merged = {**target, **siblings}
                return _resolve_refs(merged, defs, seen=seen | {name})
            # Unresolvable or circular: drop the dangling $ref, keep siblings.
            return {k: _resolve_refs(v, defs, seen=seen) for k, v in node.items() if k != "$ref"}
        return {k: _resolve_refs(v, defs, seen=seen) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(v, defs, seen=seen) for v in node]
    return node


def _coerce(node: Any) -> Any:
    """Strip unsupported keys and collapse Optional ``anyOf`` → ``nullable``."""
    if isinstance(node, dict):
        cleaned = {k: _coerce(v) for k, v in node.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
        if "anyOf" in cleaned:
            variants = cleaned.pop("anyOf") or []
            non_null = [
                v for v in variants if not (isinstance(v, dict) and v.get("type") == "null")
            ]
            has_null = any(isinstance(v, dict) and v.get("type") == "null" for v in variants)
            # Take the first concrete variant; the wrapper's own keys
            # (description, etc.) win over the variant's.
            chosen = non_null[0] if non_null else {}
            merged = {**chosen, **cleaned}
            if has_null:
                merged["nullable"] = True
            return merged
        return cleaned
    if isinstance(node, list):
        return [_coerce(v) for v in node]
    return node
