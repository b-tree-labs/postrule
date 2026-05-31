# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tests for the LLM tool-schema coercion helper (#54).

Every Postrule+Gemini integrator hit the same wall: a Pydantic
``model_json_schema()`` emits ``title``/``default`` and renders
``Optional[...]`` as ``anyOf: [{type}, {null}]`` — both of which
``google.generativeai``'s ``FunctionDeclaration`` rejects. This helper
bakes the coercion in so nobody re-implements it.
"""

from __future__ import annotations

from postrule import coerce_tool_schema


def test_strips_unsupported_metadata_keys() -> None:
    raw = {
        "title": "MyModel",
        "type": "object",
        "additionalProperties": False,
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "name": {"type": "string", "title": "Name", "default": "x"},
        },
    }
    clean = coerce_tool_schema(raw)
    assert "title" not in clean
    assert "additionalProperties" not in clean
    assert "$schema" not in clean
    assert "title" not in clean["properties"]["name"]
    assert "default" not in clean["properties"]["name"]
    assert clean["properties"]["name"]["type"] == "string"


def test_collapses_optional_anyof_to_nullable() -> None:
    raw = {
        "type": "object",
        "properties": {
            "note": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "title": "Note",
            },
        },
    }
    clean = coerce_tool_schema(raw)
    note = clean["properties"]["note"]
    assert "anyOf" not in note
    assert note["type"] == "string"
    assert note["nullable"] is True


def test_wrapper_keys_win_over_variant_and_no_spurious_nullable() -> None:
    # A non-optional anyOf (genuine union, no null member) still collapses to a
    # concrete variant, and must NOT gain nullable.
    raw = {
        "anyOf": [{"type": "integer"}, {"type": "string"}],
        "description": "an id",
    }
    clean = coerce_tool_schema(raw)
    assert clean["type"] == "integer"
    assert clean["description"] == "an id"
    assert "nullable" not in clean


def test_resolves_refs_against_defs_then_strips_defs() -> None:
    # Pydantic emits $ref + $defs for nested models. The reference impl just
    # dropped $defs, leaving dangling $refs Gemini also rejects. We resolve.
    raw = {
        "type": "object",
        "$defs": {
            "Addr": {
                "type": "object",
                "title": "Addr",
                "properties": {"zip": {"type": "string", "title": "Zip"}},
            }
        },
        "properties": {"addr": {"$ref": "#/$defs/Addr"}},
    }
    clean = coerce_tool_schema(raw)
    assert "$defs" not in clean
    addr = clean["properties"]["addr"]
    assert "$ref" not in addr
    assert addr["type"] == "object"
    assert addr["properties"]["zip"]["type"] == "string"
    assert "title" not in addr


def test_resolves_optional_ref_inside_anyof() -> None:
    raw = {
        "$defs": {"Inner": {"type": "object", "title": "Inner", "properties": {}}},
        "type": "object",
        "properties": {
            "maybe": {"anyOf": [{"$ref": "#/$defs/Inner"}, {"type": "null"}]},
        },
    }
    clean = coerce_tool_schema(raw)
    maybe = clean["properties"]["maybe"]
    assert "anyOf" not in maybe
    assert maybe["type"] == "object"
    assert maybe["nullable"] is True


def test_recurses_into_array_items() -> None:
    raw = {
        "type": "array",
        "items": {"type": "string", "title": "Item", "default": "z"},
    }
    clean = coerce_tool_schema(raw)
    assert clean["items"]["type"] == "string"
    assert "title" not in clean["items"]
    assert "default" not in clean["items"]


def test_does_not_mutate_input() -> None:
    raw = {"type": "object", "properties": {"x": {"type": "string", "title": "X"}}}
    snapshot = {"type": "object", "properties": {"x": {"type": "string", "title": "X"}}}
    coerce_tool_schema(raw)
    assert raw == snapshot


def test_unknown_target_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="target"):
        coerce_tool_schema({"type": "object"}, target="bogus")


def test_circular_ref_is_tolerated() -> None:
    # Self-referential models must not infinite-loop; the cycle edge is left as
    # a bare object rather than expanded forever.
    raw = {
        "$defs": {
            "Node": {
                "type": "object",
                "title": "Node",
                "properties": {"next": {"$ref": "#/$defs/Node"}},
            }
        },
        "$ref": "#/$defs/Node",
    }
    clean = coerce_tool_schema(raw)
    assert clean["type"] == "object"
    # The recursive edge resolves to an (empty-ish) object, not a dangling $ref.
    assert "$ref" not in clean["properties"]["next"]
