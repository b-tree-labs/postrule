# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Safety-critical decisions can never drop the rule floor.

Run: `python examples/03_safety_critical.py`

Some classifications are too consequential to ever be made by a
pure ML head — authorization decisions, content-safety gates,
fraud blocks. Setting `safety_critical=True` refuses construction
of the switch in the final phase (ML_PRIMARY). The rule floor is
architecturally guaranteed.
"""

from __future__ import annotations

from dendra import LearnedSwitch, Phase, SwitchConfig


def rule(request: dict) -> str:
    # Oversimplified toy rule — in a real system this would be the
    # carefully-audited human-written policy for "who can access what".
    return "allow" if request.get("role") == "admin" else "deny"


if __name__ == "__main__":
    # Phases 0-4 construct fine for a safety-critical switch — the
    # rule floor remains reachable at every one of them.
    for phase in [Phase.RULE, Phase.LLM_SHADOW, Phase.ML_WITH_FALLBACK]:
        switch = LearnedSwitch(
            name="access-check",
            rule=rule,
            author="@security:access-check",
            config=SwitchConfig(phase=phase, safety_critical=True),
        )
        print(f"Constructed switch at {phase.name:20s} — ok")

    # Phase 5 (ML_PRIMARY) removes the rule floor. For a
    # safety_critical switch this must fail at *construction time*
    # — not at first call, not as a warning — so the misconfiguration
    # can never ship to production.
    print()
    try:
        LearnedSwitch(
            name="access-check",
            rule=rule,
            author="@security:access-check",
            config=SwitchConfig(phase=Phase.ML_PRIMARY, safety_critical=True),
        )
    except Exception as exc:
        print(f"{Phase.ML_PRIMARY.name:20s} refused: {type(exc).__name__}: {exc}")
