# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""TDD spec for the branch-body lifter (`dendra init --auto-lift`, Phase 2).

The lifter takes a Python source file plus a target function name and
emits a refactored ``Switch`` subclass that preserves the rule's label
mapping while moving each branch's side-effect statements into a
generated ``_on_<label>`` method.

Tests in this module pin the safe-subset contract: which functions
lift cleanly, which raise :class:`LiftRefused` with a specific reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dendra.lifters import LiftRefused, lift_branches

FIXTURE_DIR = Path(__file__).parent / "lifter_fixtures"


def _normalize(src: str) -> str:
    """Round-trip through ast.unparse so whitespace differences in the
    expected fixture don't fail the snapshot. Both sides land in
    ``ast.unparse`` canonical form.
    """
    return ast.unparse(ast.parse(src))


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


# ----------------------------------------------------------------------
# Snapshot tests: input.py -> lifter -> expected.py
# ----------------------------------------------------------------------


class TestSnapshotSimpleIfElif:
    """Three-way if/elif/else, one side-effect call per branch."""

    def test_lifts_to_expected_class(self):
        src = _read("simple_if_elif.input.py")
        result = lift_branches(src, "triage")
        expected = _read("simple_if_elif.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestSnapshotMultiSideEffects:
    """One branch with three side-effect statements before its return."""

    def test_lifts_multi_side_effects(self):
        src = _read("multi_side_effects.input.py")
        result = lift_branches(src, "route")
        expected = _read("multi_side_effects.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestSnapshotMatchStatement:
    """match/case with literal cases, including a wildcard default."""

    def test_lifts_match_statement(self):
        src = _read("match_statement.input.py")
        result = lift_branches(src, "classify")
        expected = _read("match_statement.expected.py")
        assert _normalize(result) == _normalize(expected)


class TestSnapshotMultiArg:
    """Multi-positional-arg function packs into a synthetic args dataclass."""

    def test_lifts_multi_arg_function(self):
        src = _read("multi_arg.input.py")
        result = lift_branches(src, "route_request")
        expected = _read("multi_arg.expected.py")
        assert _normalize(result) == _normalize(expected)


# ----------------------------------------------------------------------
# Refusal tests: each asserts LiftRefused with a specific .reason.
# ----------------------------------------------------------------------


class TestRefuseComputedReturn:
    """A branch returning a non-literal expression cannot be lifted —
    the label set must be statically known."""

    def test_refuses_computed_return(self):
        src = """
def f(x):
    if x > 0:
        return compute_label(x)
    return 'low'
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_branches(src, "f")
        assert (
            "computed" in exc_info.value.reason.lower()
            or "literal" in exc_info.value.reason.lower()
        )
        assert exc_info.value.line > 0


class TestRefuseSharedMidFunctionState:
    """Variable defined before the if/elif and read in multiple branches
    is shared mid-function state — hazardous to duplicate, so refuse."""

    def test_refuses_shared_state(self):
        src = """
def f(x):
    title = x.lower()
    if 'crash' in title:
        return 'bug'
    if title.endswith('?'):
        return 'question'
    return 'other'
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_branches(src, "f")
        assert "shared" in exc_info.value.reason.lower() or "state" in exc_info.value.reason.lower()


class TestRefuseTryExceptInBranch:
    """try/except inside a branch couples exception flow to label
    selection. The branch-lifter splits classification from action,
    which would lose that coupling — refuse."""

    def test_refuses_try_except_in_branch(self):
        src = """
def f(x):
    if x > 0:
        try:
            do_thing(x)
        except ValueError:
            return 'caught'
        return 'ok'
    return 'low'
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_branches(src, "f")
        assert "try" in exc_info.value.reason.lower() or "except" in exc_info.value.reason.lower()


class TestRefuseGetattrOrEval:
    """Dynamic dispatch (getattr / eval / exec) blocks static analysis."""

    def test_refuses_getattr(self):
        src = """
def f(x):
    handler = getattr(self, x.kind)
    if handler.priority > 5:
        return 'high'
    return 'low'
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_branches(src, "f")
        assert (
            "getattr" in exc_info.value.reason.lower() or "dynamic" in exc_info.value.reason.lower()
        )

    def test_refuses_eval(self):
        src = """
def f(x):
    if eval(x) > 0:
        return 'positive'
    return 'nonpositive'
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_branches(src, "f")
        assert "eval" in exc_info.value.reason.lower() or "dynamic" in exc_info.value.reason.lower()


class TestRefuseZeroArg:
    """A zero-arg function isn't a classifier of an input — refuse."""

    def test_refuses_zero_arg(self):
        src = """
def f():
    if random_flag():
        return 'a'
    return 'b'
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_branches(src, "f")
        assert (
            "zero" in exc_info.value.reason.lower() or "argument" in exc_info.value.reason.lower()
        )


class TestRefuseFunctionNotFound:
    """Asking for a function the source doesn't define raises a clear error."""

    def test_refuses_missing_function(self):
        src = "def other(x):\n    return 'a'\n"
        with pytest.raises(LiftRefused) as exc_info:
            lift_branches(src, "missing")
        assert (
            "missing" in exc_info.value.reason.lower()
            or "not found" in exc_info.value.reason.lower()
        )


# ----------------------------------------------------------------------
# Output validity: emitted code must be syntactically valid Python.
# ----------------------------------------------------------------------


class TestOutputIsParseableAndImportable:
    """Sanity: every snapshot test's output must parse cleanly."""

    @pytest.mark.parametrize(
        "input_name,func_name",
        [
            ("simple_if_elif.input.py", "triage"),
            ("multi_side_effects.input.py", "route"),
            ("match_statement.input.py", "classify"),
            ("multi_arg.input.py", "route_request"),
        ],
    )
    def test_output_parses(self, input_name, func_name):
        src = _read(input_name)
        result = lift_branches(src, func_name)
        # Must round-trip through the parser without SyntaxError.
        ast.parse(result)


# ----------------------------------------------------------------------
# LiftRefused has a structured shape we can rely on.
# ----------------------------------------------------------------------


class TestLiftRefusedShape:
    """The refusal exception carries a reason string and a source line."""

    def test_lift_refused_has_reason_and_line_attrs(self):
        err = LiftRefused(reason="example", line=42)
        assert err.reason == "example"
        assert err.line == 42

    def test_lift_refused_str_includes_reason(self):
        err = LiftRefused(reason="example reason", line=10)
        assert "example reason" in str(err)
