# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""TDD spec for the evidence lifter v1.1 follow-ons.

This file pins the three deferred-from-v1 transforms:

1. Short-circuit Optional handling for ``or`` / ``and`` / nested chains.
   Each Call operand of a top-level BoolOp in an If test lifts to its own
   evidence field, with later fields gating on prior fields to preserve
   short-circuit semantics (later evidence returns None when prior fields
   already short-circuited).

2. Mutable closure snapshot with frozen-fast-path. Free variables that
   the function reads but does not bind become evidence fields. Where the
   capture is mutable (dict, list, untyped) we read at dispatch time;
   where it is annotated ``typing.Final`` or a frozen container, we
   snapshot once at decoration time via a default arg.

3. ``@evidence_via_probe`` and ``@evidence_inputs`` annotation API for
   user-declared evidence overrides. These bypass the analyzer's
   ``side_effect_evidence`` and ``dynamic_dispatch`` refusals respectively
   when the user has explicitly declared a safe lift.

The v1 test suite (``tests/test_lifter_evidence.py``) must remain green;
this file extends rather than supersedes it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dendra.lifters import LiftRefused, evidence_inputs, evidence_via_probe
from dendra.lifters.evidence import lift_evidence

FIXTURE_DIR = Path(__file__).parent / "lifter_fixtures" / "evidence_v1_1"


def _normalize(src: str) -> str:
    return ast.unparse(ast.parse(src))


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


# ----------------------------------------------------------------------
# 1. Short-circuit
# ----------------------------------------------------------------------


class TestShortCircuitOr:
    """``cheap_check(user) or expensive_db_lookup(user)`` lifts to two
    evidence fields. The second returns ``None`` when the first already
    short-circuited, preserving lazy-evaluation semantics.
    """

    def test_lifts_or(self):
        src = _read("short_circuit_or.input.py")
        result = lift_evidence(src, "authorize")
        expected = _read("short_circuit_or.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestShortCircuitAnd:
    """``has_account(user) and is_active(user)`` lifts symmetrically: the
    second is ``None`` when the first is falsy (because ``and`` would
    have short-circuited at that point).
    """

    def test_lifts_and(self):
        src = _read("short_circuit_and.input.py")
        result = lift_evidence(src, "gate")
        expected = _read("short_circuit_and.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestShortCircuitNested:
    """``a or b or c`` generalizes: each successive field is None when
    any prior field already short-circuited.
    """

    def test_lifts_nested_or(self):
        src = _read("short_circuit_nested.input.py")
        result = lift_evidence(src, "chain")
        expected = _read("short_circuit_nested.expected.py")
        assert _normalize(result) == _normalize(expected)


# ----------------------------------------------------------------------
# 2. Closure snapshot
# ----------------------------------------------------------------------


class TestClosureMutable:
    """Mutable closure capture (untyped or generic mutable container):
    the gather reads the closure variable at dispatch time so changes
    after decoration are observable.
    """

    def test_lifts_mutable_closure(self):
        src = _read("closure_mutable.input.py")
        result = lift_evidence(src, "route")
        expected = _read("closure_mutable.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestClosureFinal:
    """``typing.Final`` annotated capture: snapshot once at decoration
    time via a default arg.
    """

    def test_lifts_final_closure(self):
        src = _read("closure_final.input.py")
        result = lift_evidence(src, "gate")
        expected = _read("closure_final.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestClosureTuple:
    """Frozen-type annotation (tuple / frozenset / str / int): snapshot
    once at decoration time.
    """

    def test_lifts_tuple_closure(self):
        src = _read("closure_tuple.input.py")
        result = lift_evidence(src, "route")
        expected = _read("closure_tuple.expected.py")
        assert _normalize(result) == _normalize(expected)


# ----------------------------------------------------------------------
# 3. Annotation API
# ----------------------------------------------------------------------


class TestEvidenceViaProbeSuccess:
    """``@evidence_via_probe`` overrides the side_effect_evidence refusal
    by declaring a probe expression for the dangerous bind.
    """

    def test_lifts_with_probe(self):
        src = _read("probe_success.input.py")
        result = lift_evidence(src, "maybe_charge")
        expected = _read("probe_success.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestEvidenceViaProbeTrailingAttr:
    """When the probe expression already extracts an attribute (e.g.
    ``api.charge_probe(req).ok``) and the rule body's reference accesses
    the same attribute on the bind (``response.ok``), the rewriter must
    drop the trailing ``.attr`` so the lifted body reads
    ``evidence.<field>`` directly, not ``evidence.<field>.<attr>``.
    """

    def test_drops_trailing_attr_when_probe_extracts_it(self):
        src = """
@evidence_via_probe(charge_ok="api.charge_probe(req).ok")
def maybe_charge(req):
    response = api.charge(req)
    if response.ok:
        return "charged"
    return "skipped"
"""
        result = lift_evidence(src, "maybe_charge")
        # The rule body must use `evidence.charge_ok` (a bool), NOT
        # `evidence.charge_ok.ok` (which would re-access .ok on the bool).
        assert "evidence.charge_ok.ok" not in result
        assert "if evidence.charge_ok:" in result

    def test_drops_trailing_attr_for_payload_field(self):
        src = """
@evidence_via_probe(payload="api.fetch(req).body")
def serve(req):
    response = api.do(req)
    if response.body == 'ready':
        return "ready"
    return "wait"
"""
        result = lift_evidence(src, "serve")
        assert "evidence.payload.body" not in result
        assert "if evidence.payload == 'ready':" in result

    def test_refuses_when_probe_attr_mismatches_rule_attr(self):
        src = """
@evidence_via_probe(charge_ok="api.charge_probe(req).ok")
def maybe_charge(req):
    response = api.charge(req)
    if response.error:
        return "failed"
    return "charged"
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_evidence(src, "maybe_charge")
        msg = exc_info.value.reason.lower()
        # Mismatch between probe-extracted attr and rule-body attr.
        assert "probe" in msg or "mismatch" in msg or "attr" in msg


class TestEvidenceViaProbeMissingField:
    """``@evidence_via_probe`` with no kwargs, or a kwarg whose value is
    not a parseable expression, is a usage error: refuse.
    """

    def test_refuses_empty_probe(self):
        src = """
@evidence_via_probe()
def maybe_charge(req):
    response = api.charge(req)
    if response.ok:
        notify(req)
        return "charged"
    return "skipped"
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_evidence(src, "maybe_charge")
        # The annotation is empty, so the underlying side_effect_evidence
        # refusal still fires (no override applied).
        msg = exc_info.value.reason.lower()
        assert "side_effect" in msg or "side-effect" in msg or "probe" in msg


class TestEvidenceInputsSuccess:
    """``@evidence_inputs`` overrides the dynamic_dispatch refusal by
    declaring each evidence field's gatherer as a lambda.
    """

    def test_lifts_with_inputs(self):
        src = _read("inputs_success.input.py")
        result = lift_evidence(src, "route")
        expected = _read("inputs_success.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestEvidenceInputsPartial:
    """``@evidence_inputs`` declares only some fields; an unannotated
    hazard (a different ``getattr``) still triggers refusal.
    """

    def test_refuses_when_partial(self):
        src = """
@evidence_inputs(handler_priority=lambda self, text, kind: 1)
def route(self, text: str, kind: str):
    other = getattr(self, "unrelated")
    if other.flag:
        return "flagged"
    return "clear"
"""
        # The annotation only covers `handler_priority`. The body still
        # contains a bare getattr the analyzer flags as dynamic_dispatch.
        # That hazard is unannotated, so the lifter must refuse.
        with pytest.raises(LiftRefused) as exc_info:
            lift_evidence(src, "route")
        msg = exc_info.value.reason.lower()
        assert "dynamic" in msg or "getattr" in msg


# ----------------------------------------------------------------------
# Decorator runtime sanity (they should be no-ops attaching metadata).
# ----------------------------------------------------------------------


class TestDecoratorRuntime:
    """``evidence_via_probe`` and ``evidence_inputs`` are runtime no-ops
    that attach a metadata attribute. They should pass the original
    callable through unchanged.
    """

    def test_via_probe_attaches_metadata(self):
        @evidence_via_probe(charge_status="api.charge_probe(req)")
        def maybe_charge(req):
            return "ok"

        assert maybe_charge("anything") == "ok"
        assert hasattr(maybe_charge, "_dendra_evidence_probes")
        assert maybe_charge._dendra_evidence_probes == {
            "charge_status": "api.charge_probe(req)"
        }

    def test_inputs_attaches_metadata(self):
        def gather(self, text, kind):
            return 1

        @evidence_inputs(handler_priority=gather)
        def route(self, text, kind):
            return "low"

        assert hasattr(route, "_dendra_evidence_inputs")
        assert route._dendra_evidence_inputs == {"handler_priority": gather}
