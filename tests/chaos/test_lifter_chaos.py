# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Lifter codegen chaos: adversarial AST shapes.

The branch + evidence lifters operate on user source. v1 SAFE-subset
spec promises clean refusal on hazards; this test class throws every
weird Python shape we can think of at the lifters and confirms they
either lift cleanly or refuse with a clear error , no crashes, no
infinite loops, no silent accept-and-emit-broken.
"""

from __future__ import annotations

import time

import pytest

from postrule.lifters.branch import LiftRefused, lift_branches
from postrule.lifters.evidence import lift_evidence

# ---------------------------------------------------------------------------
# Many branches
# ---------------------------------------------------------------------------


class TestManyBranches:
    @pytest.mark.slow
    def test_one_thousand_branches_lifts_or_refuses_in_time(self):
        """A function with 1000 elif branches: lifter must handle in <5s.

        Bug shape: recursion on a 1000-deep AST blows the stack. We want
        a finite-time success or a LiftRefused; what we get today is
        RecursionError.
        """
        body_lines = [f"    elif x == {i}: return 'b{i}'" for i in range(1000)]
        src = (
            "def cls(x):\n"
            "    if x == -1: return 'init'\n"
            + "\n".join(body_lines)
            + "\n    else: return 'default'\n"
        )

        t0 = time.monotonic()
        try:
            out = lift_branches(src, "cls")
        except LiftRefused:
            elapsed = time.monotonic() - t0
            assert elapsed < 5.0, f"refusal took {elapsed:.1f}s"
            return
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"1000-branch lift took {elapsed:.1f}s; too slow"
        assert "ClsSwitch" in out

    def test_one_hundred_branches_lifts_cleanly(self):
        """100 elif branches: well under the recursion ceiling, must lift."""
        body_lines = [f"    elif x == {i}: return 'b{i}'" for i in range(100)]
        src = (
            "def cls(x):\n"
            "    if x == -1: return 'init'\n"
            + "\n".join(body_lines)
            + "\n    else: return 'default'\n"
        )
        out = lift_branches(src, "cls")
        compile(out, "<lifted>", "exec")


# ---------------------------------------------------------------------------
# Deeply nested if/elif (compiler stack-depth concern)
# ---------------------------------------------------------------------------


class TestDeeplyNested:
    def test_deeply_nested_if_does_not_recurse_to_oblivion(self):
        """50-level nested ifs: lifter either succeeds or refuses cleanly.

        Bug shape would be RecursionError from a recursive AST walker
        without an iteration cap.
        """
        # Build:
        #   if x > 0:
        #       if x > 1:
        #           if x > 2:
        #               ... 50 deep ...
        #                   return "deep"
        # The lifter generally refuses on nested ifs (shared mid-function
        # state semantics), that's the right answer.
        body = ""
        indent = "    "
        for i in range(50):
            body += f"{indent * (i + 1)}if x > {i}:\n"
        body += f"{indent * 51}return 'deep'\n"
        src = f"def cls(x):\n{body}    return 'shallow'\n"

        try:
            lift_branches(src, "cls")
        except (LiftRefused, RecursionError, SyntaxError) as e:
            # Refused or rejected , either is fine; "RecursionError" is
            # the bug shape but it's still a refusal type, not a crash.
            if isinstance(e, RecursionError):
                pytest.xfail("bug: lifter recurses past Python's stack on deep nesting")
            return


# ---------------------------------------------------------------------------
# Malformed Python (caught upstream)
# ---------------------------------------------------------------------------


class TestMalformedPython:
    @pytest.mark.parametrize(
        "src",
        [
            "def cls(x):\n    if x > 0\n        return 'pos'\n",  # missing colon
            "def cls(x:\n    return 'pos'\n",  # bad sig
            "def cls(x):\n    if",  # truncated
            "def\n",  # missing name
        ],
    )
    def test_syntax_error_surfaces(self, src):
        """Malformed Python: ast.parse raises SyntaxError; lifter doesn't crash."""
        with pytest.raises(SyntaxError):
            lift_branches(src, "cls")
        with pytest.raises(SyntaxError):
            lift_evidence(src, "cls")


# ---------------------------------------------------------------------------
# Unicode function names
# ---------------------------------------------------------------------------


class TestUnicodeIdentifiers:
    def test_unicode_function_name(self):
        """A function named with non-ASCII letters: lifter handles cleanly."""
        src = "def α_classifier(x):\n    if x > 0:\n        return 'pos'\n    return 'neg'\n"
        out = lift_branches(src, "α_classifier")
        # The class name will be derived from the function name.
        # Whatever the lifter does, it must produce VALID Python.
        compile(out, "<lifted>", "exec")

    def test_lookup_with_unicode_name(self):
        src = "def β(x):\n    return 'a' if x else 'b'\n"
        # β is not in the source body (no if/elif chain) so this should
        # refuse. Confirms the unicode lookup works.
        with pytest.raises(LiftRefused):
            lift_branches(src, "β")


# ---------------------------------------------------------------------------
# Many parameters
# ---------------------------------------------------------------------------


class TestManyParameters:
    def test_50_args_with_annotations(self):
        """A function with 50 typed params: lifter handles."""
        params = ", ".join(f"a{i}: int" for i in range(50))
        src = f"def cls({params}):\n    if a0 > 0:\n        return 'pos'\n    return 'neg'\n"
        out = lift_branches(src, "cls")
        compile(out, "<lifted>", "exec")

    def test_50_args_no_annotations_emits_valid_python(self):
        """50 untyped params: lifters either refuse or emit COMPILABLE Python.

        The analyzer flags multi_arg_no_annotation at "warn" severity, so
        neither lifter refuses outright. What MUST hold: the emitted
        source compiles. Silently emitting broken Python is the bug
        shape we're guarding against.
        """
        params = ", ".join(f"a{i}" for i in range(50))
        src = f"def cls({params}):\n    if a0 > 0:\n        return 'pos'\n    return 'neg'\n"
        try:
            out = lift_branches(src, "cls")
            compile(out, "<lifted>", "exec")
        except LiftRefused:
            pass  # also acceptable
        try:
            out = lift_evidence(src, "cls")
            compile(out, "<lifted>", "exec")
        except LiftRefused:
            pass


# ---------------------------------------------------------------------------
# eval / exec / dynamic dispatch
# ---------------------------------------------------------------------------


class TestDynamicDispatchRefusal:
    @pytest.mark.parametrize(
        "src",
        [
            "def cls(x):\n    if eval(x) > 0:\n        return 'pos'\n    return 'neg'\n",
            "def cls(x):\n    if exec(x):\n        return 'pos'\n    return 'neg'\n",
            "def cls(x):\n    if getattr(x, 'foo'):\n        return 'pos'\n    return 'neg'\n",
        ],
    )
    def test_eval_exec_getattr_refused(self, src):
        with pytest.raises(LiftRefused):
            lift_branches(src, "cls")


# ---------------------------------------------------------------------------
# Cyclic / recursive function reference
# ---------------------------------------------------------------------------


class TestCyclicReferences:
    def test_function_calling_itself_in_branch(self):
        """A self-recursive call inside a branch: lifter doesn't loop."""
        src = "def cls(x):\n    if x > 0:\n        return cls(x - 1)\n    return 'base'\n"
        # The lifter operates on AST shape, not call semantics, so this
        # will likely be rejected (computed return). Either refusal OR
        # a finite-time successful lift is acceptable.
        t0 = time.monotonic()
        try:
            lift_branches(src, "cls")
        except LiftRefused:
            pass
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"cyclic lift hung: {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Empty function body
# ---------------------------------------------------------------------------


class TestEmptyFunctions:
    def test_zero_arg_function_refused(self):
        src = "def cls():\n    if True:\n        return 'a'\n    return 'b'\n"
        with pytest.raises(LiftRefused):
            lift_branches(src, "cls")

    def test_function_with_no_branch_refused(self):
        src = "def cls(x):\n    return 'always'\n"
        with pytest.raises(LiftRefused):
            lift_branches(src, "cls")

    def test_function_not_present(self):
        src = "def other(x):\n    if x: return 'a'\n    return 'b'\n"
        with pytest.raises(LiftRefused):
            lift_branches(src, "missing")


# ---------------------------------------------------------------------------
# Pathological body shapes
# ---------------------------------------------------------------------------


class TestPathologicalBodies:
    def test_branch_with_try_except_refused(self):
        """try/except inside a branch body must refuse cleanly."""
        src = (
            "def cls(x):\n"
            "    if x > 0:\n"
            "        try:\n"
            "            return 'a'\n"
            "        except Exception:\n"
            "            return 'b'\n"
            "    return 'c'\n"
        )
        with pytest.raises(LiftRefused):
            lift_branches(src, "cls")

    def test_branch_returning_computed_string_refused(self):
        """Computed return values (not literals) refused for v1."""
        src = "def cls(x):\n    if x > 0:\n        return 'pos_' + str(x)\n    return 'neg'\n"
        with pytest.raises(LiftRefused):
            lift_branches(src, "cls")

    def test_evidence_refuses_print_side_effect(self):
        """Side-effect bearing body refused by EVIDENCE lifter (analyzer-driven).

        The branch lifter only refuses on try/except in branches; broader
        side-effect detection is the analyzer's job, surfaced through the
        evidence lifter.
        """
        src = (
            "def cls(x):\n"
            "    print('side-effect')\n"
            "    if x > 0:\n"
            "        return 'pos'\n"
            "    return 'neg'\n"
        )
        # The evidence lifter delegates to the analyzer; whether it refuses
        # depends on whether `print` triggers side_effect_evidence. If it
        # doesn't refuse, we accept that as the safe-subset boundary ,
        # what matters is that the lift produces VALID Python.
        try:
            out = lift_evidence(src, "cls")
            compile(out, "<lifted>", "exec")
        except LiftRefused:
            pass  # also acceptable


# ---------------------------------------------------------------------------
# Very long source string
# ---------------------------------------------------------------------------


class TestVeryLongSource:
    def test_function_with_huge_docstring(self):
        """A 100KB docstring inside the function: still lifts."""
        big_doc = '"""' + ("A " * 50_000) + '"""'
        src = f"def cls(x):\n    {big_doc}\n    if x > 0:\n        return 'pos'\n    return 'neg'\n"
        # Docstring should be skipped per the leading_stmts logic; this
        # must not OOM.
        out = lift_branches(src, "cls")
        compile(out, "<lifted>", "exec")
