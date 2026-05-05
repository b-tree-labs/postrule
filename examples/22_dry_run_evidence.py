# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Side-effect-bearing evidence with `@evidence_via_probe`.

The motivating shape:

    def maybe_charge(req):
        response = api.charge(req)         # side effect: charges the user
        if response.ok:                    # evidence: is the charge ok?
            return "charged"
        return "skipped"

Lifting this naively would call ``api.charge`` inside ``_gather``,
charging on every dispatch even when the model would have picked
``skipped``. The ``@evidence_via_probe`` decorator declares a
side-effect-free probe call to use in the lifter's ``_gather``
instead, while the original ``api.charge`` lifts cleanly into the
chosen label's ``_on_charged`` handler.

This example shows the BEFORE source, applies the lifter, and prints
the AFTER refactored Switch class. The actual ``api.charge`` /
``api.charge_probe`` are stubs; the lifter is what's load-bearing.

Run:
    python examples/22_dry_run_evidence.py
"""

from __future__ import annotations

import textwrap

from dendra.lifters.evidence import lift_evidence

SOURCE = textwrap.dedent(
    """\
    from dendra.lifters import evidence_via_probe

    @evidence_via_probe(charge_ok="api.charge_probe(req).ok")
    def maybe_charge(req):
        response = api.charge(req)
        if response.ok:
            return "charged"
        return "skipped"
    """
)


def main() -> None:
    print("Source — side-effect-bearing evidence with a dry-run annotation:")
    print()
    for line in SOURCE.rstrip().split("\n"):
        print(f"  {line}")
    print()
    print("After dendra init --auto-lift:")
    print()
    lifted = lift_evidence(SOURCE, "maybe_charge")
    for line in lifted.rstrip().split("\n"):
        print(f"  {line}")
    print()
    print("The generated _evidence_charge_ok() calls api.charge_probe(req)")
    print("instead of api.charge(req), so the dry-run probe runs on every")
    print("dispatch but the real charge does NOT. Wire your own _on_charged")
    print("handler (or use the dispatch form labels={'charged': real_charge})")
    print("to fire api.charge(req) only when the chosen label is 'charged'.")


if __name__ == "__main__":
    main()
