# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""TDD spec for branch-lifter v1.5 relaxations.

Two relaxations to the v1.1 single-elif-chain shape, sized by the Phase 0
sizing study (#128) as the highest-leverage v1.5 work:

1. Multi-top-level-if support: a flat sequence of ``if cond: ... return``
   statements at function top level, optionally followed by a default
   (statements + bare return), is treated as canonical (semantically
   equivalent to the elif chain when each branch returns).

2. Leading-bind passthrough: assignments before the if/elif chain
   (e.g. ``title = ticket.get("title", "").lower()``) are lifted to
   ``_evidence_<name>`` methods rather than refused as ``shared_state``.

The two relaxations compose: a function with both forms lifts cleanly.

Negative cases that MUST still refuse (out of safe subset):
- Leading bind whose RHS reads from ``self.<attr>`` or a module global
  (those are the evidence lifter's job, not the branch lifter's).
- Multi-top-level-if where a non-final top-level ``if`` body falls
  through without returning.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from postrule.lifters import LiftRefused, lift_branches

FIXTURE_DIR = Path(__file__).parent / "lifter_fixtures" / "branch_v1_5"


def _normalize(src: str) -> str:
    """Round-trip through ``ast.unparse`` so whitespace differences
    in fixtures don't fail snapshot comparisons.
    """
    return ast.unparse(ast.parse(src))


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


# ----------------------------------------------------------------------
# Relaxation 1: multi-top-level-if support
# ----------------------------------------------------------------------


class TestMultiTopLevelIf:
    """Flat ``if cond: ... return`` sequences lift like an elif chain."""

    def test_basic_three_arms_with_default_body(self):
        src = _read("multi_top_if_basic.input.py")
        result = lift_branches(src, "triage")
        expected = _read("multi_top_if_basic.expected.py")
        assert _normalize(result) == _normalize(expected)

    def test_bare_default_return(self):
        src = _read("multi_top_if_bare_default.input.py")
        result = lift_branches(src, "classify")
        expected = _read("multi_top_if_bare_default.expected.py")
        assert _normalize(result) == _normalize(expected)

    def test_four_arms_no_side_effects(self):
        src = _read("multi_top_if_four_arms.input.py")
        result = lift_branches(src, "handling_rule")
        expected = _read("multi_top_if_four_arms.expected.py")
        assert _normalize(result) == _normalize(expected)

    def test_mixed_elif_and_top_level_if(self):
        src = _read("multi_top_if_with_elif.input.py")
        result = lift_branches(src, "route")
        expected = _read("multi_top_if_with_elif.expected.py")
        assert _normalize(result) == _normalize(expected)


# ----------------------------------------------------------------------
# Relaxation 2: leading-bind passthrough
# ----------------------------------------------------------------------


class TestLeadingBind:
    """Assignments before the if/elif chain lift to ``_evidence_<name>``."""

    def test_single_bind_simple_chain(self):
        src = _read("leading_bind_simple.input.py")
        result = lift_branches(src, "triage")
        expected = _read("leading_bind_simple.expected.py")
        assert _normalize(result) == _normalize(expected)

    def test_two_chained_binds(self):
        src = _read("leading_bind_two.input.py")
        result = lift_branches(src, "classify")
        expected = _read("leading_bind_two.expected.py")
        assert _normalize(result) == _normalize(expected)

    def test_bind_unused_in_handler_body(self):
        src = _read("leading_bind_with_action.input.py")
        result = lift_branches(src, "route")
        expected = _read("leading_bind_with_action.expected.py")
        assert _normalize(result) == _normalize(expected)

    def test_bind_used_in_handler_body(self):
        src = _read("leading_bind_used_in_action.input.py")
        result = lift_branches(src, "label")
        expected = _read("leading_bind_used_in_action.expected.py")
        assert _normalize(result) == _normalize(expected)


# ----------------------------------------------------------------------
# Composition: both relaxations together
# ----------------------------------------------------------------------


class TestComposed:
    """Multi-top-level-if + leading-bind together must lift cleanly."""

    def test_basic_composed(self):
        src = _read("composed_basic.input.py")
        result = lift_branches(src, "triage_rule")
        expected = _read("composed_basic.expected.py")
        assert _normalize(result) == _normalize(expected)

    def test_composed_with_action_handlers(self):
        src = _read("composed_with_actions.input.py")
        result = lift_branches(src, "route")
        expected = _read("composed_with_actions.expected.py")
        assert _normalize(result) == _normalize(expected)

    def test_composed_with_default_action(self):
        src = _read("composed_default_action.input.py")
        result = lift_branches(src, "classify")
        expected = _read("composed_default_action.expected.py")
        assert _normalize(result) == _normalize(expected)

    def test_composed_bind_used_in_handler(self):
        src = _read("composed_bind_in_handler.input.py")
        result = lift_branches(src, "handle")
        expected = _read("composed_bind_in_handler.expected.py")
        assert _normalize(result) == _normalize(expected)


# ----------------------------------------------------------------------
# Negative cases: still refuse (safe-subset boundaries unchanged)
# ----------------------------------------------------------------------


class TestStillRefuses:
    """Two cases the v1.5 relaxations deliberately do NOT cover."""

    def test_refuses_leading_bind_reading_self_attr(self):
        """Leading binds whose RHS reads ``self.<attr>`` are evidence-
        lifter territory: they require a hidden-state gather, which
        the branch lifter does not synthesize. Must refuse with a
        specific reason (not ``non_canonical_chain`` / ``shared_state``).
        """
        src = """
def f(self, x):
    text = self.cache_state.get(x)
    if text == 'hot':
        return 'hot'
    return 'cold'
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_branches(src, "f")
        reason = exc_info.value.reason.lower()
        # Must NOT be the old generic refusal (those are exactly what
        # v1.5 set out to relax).
        assert "shared mid-function state" not in reason
        # MUST mention self / hidden-state-ish reason so the user
        # knows to reach for the evidence lifter.
        assert "self" in reason or "hidden" in reason or "evidence" in reason

    def test_refuses_leading_bind_reading_global(self):
        """Leading bind whose RHS reads a module-level global is also
        evidence-lifter territory (the global isn't a function input).
        """
        src = """
THRESHOLD = 100

def f(x):
    over = x > THRESHOLD
    if over:
        return 'high'
    return 'low'
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_branches(src, "f")
        reason = exc_info.value.reason.lower()
        assert "shared mid-function state" not in reason
        assert "global" in reason or "hidden" in reason or "evidence" in reason

    def test_refuses_top_level_if_falls_through_without_return(self):
        """A non-terminal top-level ``if`` whose body lacks a final
        ``return`` cannot be a flat-fallthrough chain. The refusal
        reason must distinguish this from the relaxed canonical shape.
        """
        src = """
def f(x):
    if x.kind == 'a':
        emit('a')   # no return — falls through
    if x.kind == 'b':
        return 'b'
    return 'c'
"""
        with pytest.raises(LiftRefused) as exc_info:
            lift_branches(src, "f")
        reason = exc_info.value.reason.lower()
        assert "fall" in reason or "return" in reason


# ----------------------------------------------------------------------
# Output validity: every snapshot must round-trip the parser
# ----------------------------------------------------------------------


class TestOutputIsParseable:
    @pytest.mark.parametrize(
        "input_name,func_name",
        [
            ("multi_top_if_basic.input.py", "triage"),
            ("multi_top_if_bare_default.input.py", "classify"),
            ("multi_top_if_four_arms.input.py", "handling_rule"),
            ("multi_top_if_with_elif.input.py", "route"),
            ("leading_bind_simple.input.py", "triage"),
            ("leading_bind_two.input.py", "classify"),
            ("leading_bind_with_action.input.py", "route"),
            ("leading_bind_used_in_action.input.py", "label"),
            ("composed_basic.input.py", "triage_rule"),
            ("composed_with_actions.input.py", "route"),
            ("composed_default_action.input.py", "classify"),
            ("composed_bind_in_handler.input.py", "handle"),
        ],
    )
    def test_output_parses(self, input_name, func_name):
        src = _read(input_name)
        result = lift_branches(src, func_name)
        ast.parse(result)
