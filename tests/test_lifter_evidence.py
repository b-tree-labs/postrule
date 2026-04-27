# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""TDD spec for the evidence lifter (`dendra init --auto-lift`, Phase 3 v1).

The evidence lifter takes a function with hidden state reads (module
globals, ``self.attr``, mid-function call binds whose result is later
read in branch tests) and emits a refactored ``Switch`` subclass with
one ``_evidence_<name>`` method per detected hidden-state read.

Tests pin the v1 safe subset:
- module-global reads (incl. subscripted globals like FEATURE_FLAGS["x"]),
- ``self.<attr>`` reads,
- mid-function binds (``user = db.lookup(text); if user.tier == ...``),
- pure-arg functions (no hidden state).

And the refusal contract: side-effect-bearing evidence (when the
analyzer flags a side-effect-evidence hazard AND the branch body itself
runs side-effect statements), dynamic dispatch, ``eval`` / ``exec``,
zero-arg functions all raise :class:`LiftRefused`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dendra.lifters import LiftRefused
from dendra.lifters.evidence import lift_evidence

FIXTURE_DIR = Path(__file__).parent / "lifter_fixtures" / "evidence"


def _normalize(src: str) -> str:
    """Round-trip through ast.unparse so whitespace differences in the
    expected fixture don't fail the snapshot.
    """
    return ast.unparse(ast.parse(src))


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


# ----------------------------------------------------------------------
# Snapshot tests: input.py -> lifter -> expected.py
# ----------------------------------------------------------------------


class TestSnapshotGlobalsRead:
    """Function reads ``FEATURE_FLAGS["fast_lane"]`` — subscripted module
    global. Lifts to one ``_evidence_fast_lane`` method.
    """

    def test_lifts_globals_read(self):
        src = _read("globals_read.input.py")
        result = lift_evidence(src, "gate")
        expected = _read("globals_read.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestSnapshotSelfAttr:
    """Method reads ``self.cache_state``. Lifts to one
    ``_evidence_cache_state`` method.
    """

    def test_lifts_self_attr(self):
        src = _read("self_attr.input.py")
        result = lift_evidence(src, "check")
        expected = _read("self_attr.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestSnapshotMidFuncBind:
    """``user = db.lookup(text); if user.tier == 'vip': return 'vip'`` —
    mid-function bind. Lifts to ``_evidence_user`` calling ``db.lookup``.
    """

    def test_lifts_mid_func_bind(self):
        src = _read("mid_func_bind.input.py")
        result = lift_evidence(src, "route")
        expected = _read("mid_func_bind.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestSnapshotPureArg:
    """Function only reads its single arg — no hidden state. Lifts to a
    trivial Switch class with one passthrough evidence field.
    """

    def test_lifts_pure_arg(self):
        src = _read("pure_arg.input.py")
        result = lift_evidence(src, "classify")
        expected = _read("pure_arg.expected.py")
        assert _normalize(result) == _normalize(expected)


# ----------------------------------------------------------------------
# Refusal tests
# ----------------------------------------------------------------------


class TestRefuseSideEffectEvidence:
    """When the analyzer flags side-effect-bearing evidence AND the
    branch body itself runs side-effect statements, refuse: lifting
    the gather would re-fire the side effects on every dispatch.
    """

    def test_refuses_side_effect_evidence(self):
        src = """
def maybe_charge(req):
    response = api.charge(req)
    if response.ok:
        notify(req)
        return "charged"
    return "skipped"
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_evidence(src, "maybe_charge")
        assert (
            "side_effect" in exc_info.value.reason.lower()
            or "side-effect" in exc_info.value.reason.lower()
        )


class TestRefuseGetattr:
    """``getattr`` blocks static evidence detection — refuse."""

    def test_refuses_getattr(self):
        src = """
def f(x):
    handler = getattr(self, x.kind)
    if handler.priority > 5:
        return "high"
    return "low"
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_evidence(src, "f")
        assert (
            "getattr" in exc_info.value.reason.lower() or "dynamic" in exc_info.value.reason.lower()
        )


class TestRefuseEval:
    """``eval`` blocks static evidence detection — refuse."""

    def test_refuses_eval(self):
        src = """
def f(x):
    if eval(x) > 0:
        return "positive"
    return "nonpositive"
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_evidence(src, "f")
        assert "eval" in exc_info.value.reason.lower() or "dynamic" in exc_info.value.reason.lower()


class TestRefuseZeroArg:
    """A zero-arg function isn't a classifier of an input — refuse."""

    def test_refuses_zero_arg(self):
        src = """
def f():
    if random_flag():
        return "a"
    return "b"
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_evidence(src, "f")
        assert (
            "zero" in exc_info.value.reason.lower()
            or "argument" in exc_info.value.reason.lower()
            or "classifier" in exc_info.value.reason.lower()
        )
