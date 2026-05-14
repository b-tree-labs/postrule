# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""Safety-critical decisions can never drop the rule floor.

Run: `python examples/03_safety_critical.py`

``safety_critical=True`` refuses CONSTRUCTION of the switch in
ML_PRIMARY — the only phase without a rule fallback on the hot
path. Construction-time (not call-time) because authorization,
HIPAA-bound triage, export-control gates have zero tolerance for
"we'll catch it in staging" — the bad config can't survive past
``python -c "import mymodule"``.
"""

from __future__ import annotations

from postrule import LearnedSwitch, Phase


def access_rule(request: dict) -> str:
    """Toy admin-only policy.

    In a real system, the carefully-audited "who can access what."
    Under ``safety_critical=True`` this is the only thing the switch
    can fall back to, so rules here tend to be smaller and more
    deny-by-default than their non-critical peers.
    """
    return "allow" if request.get("role") == "admin" else "deny"


if __name__ == "__main__":
    # Phases 0-4 all construct fine — the rule floor is reachable
    # at every one (RULE, MODEL_SHADOW, MODEL_PRIMARY, ML_SHADOW,
    # ML_WITH_FALLBACK). Only ML_PRIMARY is refused.
    for phase in [Phase.RULE, Phase.MODEL_SHADOW, Phase.ML_WITH_FALLBACK]:
        # Explicit name= because "access-check" is the domain-facing
        # audit identity; the rule is named access_rule.
        switch = LearnedSwitch(
            name="access-check",
            rule=access_rule,
            starting_phase=phase,
            safety_critical=True,
        )
        print(f"Constructed switch at {phase.name:20s} — ok")

    print()
    try:
        LearnedSwitch(
            name="access-check",
            rule=access_rule,
            starting_phase=Phase.ML_PRIMARY,
            safety_critical=True,
        )
    except ValueError as exc:
        # ValueError is the specific class Postrule raises for
        # safety_critical + ML_PRIMARY; narrowing keeps this block from
        # masking unrelated bugs during demo runs.
        print(f"{Phase.ML_PRIMARY.name:20s} refused: {type(exc).__name__}: {exc}")
