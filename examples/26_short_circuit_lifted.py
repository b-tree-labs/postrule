# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Short-circuit boolean logic lifted with ordered-Optional gather.

The motivating shape:

    def authorize(user):
        if cheap_check(user) or expensive_db_lookup(user):
            return "allowed"
        return "denied"

Lifting both predicates into ``_gather`` naively would always call the
expensive lookup, defeating the original short-circuit. The evidence
lifter detects the ``BoolOp(Or)`` and emits an ordered-Optional gather
that preserves short-circuit order: the expensive operand's evidence
field is ``None`` when the cheap one already returned True.

This example shows the BEFORE source, applies the lifter, and prints
the AFTER refactored Switch class. ``cheap_check`` / ``expensive_db_lookup``
are stubs; the lifter is what's load-bearing.

Run:
    python examples/26_short_circuit_lifted.py
"""

from __future__ import annotations

import textwrap

from postrule.lifters.evidence import lift_evidence

SOURCE = textwrap.dedent(
    """\
    def authorize(user):
        if cheap_check(user) or expensive_db_lookup(user):
            return "allowed"
        return "denied"
    """
)


def main() -> None:
    print("Source — short-circuit `or` over a cheap then expensive predicate:")
    print()
    for line in SOURCE.rstrip().split("\n"):
        print(f"  {line}")
    print()
    print("After postrule init --auto-lift:")
    print()
    lifted = lift_evidence(SOURCE, "authorize")
    for line in lifted.rstrip().split("\n"):
        print(f"  {line}")
    print()
    print("Note the ordered-Optional gather: `expensive_ok` is None when")
    print("`cheap_ok` already returned True, preserving the short-circuit.")
    print("The classifier's `or` over Optional[bool] gives the same boolean")
    print("result Python's short-circuit produced; the LLM/ML head sees both")
    print("fields and learns the cheap-vs-expensive trade-off.")


if __name__ == "__main__":
    main()
