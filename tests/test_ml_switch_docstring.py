# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
#
# Discoverability guard (#72): the @ml_switch docstring (which surfaces in
# IDE hovers, `help()`, and rendered API docs) must front-load the fact
# that wrapping alone does not log verdicts — the operator has to call
# wrapped_fn.record_verdict(...) after each invocation. A new user reading
# only the first paragraph and an example must come away knowing this.

import re

from postrule import ml_switch


def _first_block_of_docstring(doc: str) -> str:
    """Return everything before the ``Args:`` block — the part most readers
    actually read."""
    return doc.split("Args:", 1)[0]


class TestMlSwitchDocstringFrontLoadsRecordVerdict:
    def test_docstring_exists(self):
        assert ml_switch.__doc__ is not None

    def test_record_verdict_is_mentioned_before_args(self):
        # If it's only in the Returns section (or worse, only in the body),
        # a hurried reader will miss it. The fix is to surface it in the
        # opening explanation.
        front = _first_block_of_docstring(ml_switch.__doc__ or "")
        assert "record_verdict" in front

    def test_docstring_includes_an_example_calling_record_verdict(self):
        # An inline code example is the strongest discoverability signal —
        # it appears verbatim in IDE hovers and `help()` output.
        front = _first_block_of_docstring(ml_switch.__doc__ or "")
        # ``Example:: ...`` block followed by a record_verdict call somewhere
        # in the example. Match loosely on whitespace.
        assert re.search(r"Example\s*::", front), (
            "Front-loaded docstring should include an ``Example::`` block"
        )
        # The example must actually call record_verdict.
        # (We check both the front block and any example in it.)
        assert re.search(r"\.record_verdict\(", front)
