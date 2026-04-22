# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Output safety — classify LLM *output* before it reaches users.

Run: `python examples/05_output_safety.py`

The same six-phase primitive that gates *input* routing can also
gate *output* delivery. Wrap a content-safety rule with
`safety_critical=True`, put it on the LLM's output path, and the
rule floor becomes the architectural guarantee that PII and
confidential markers are caught even if the ML head is compromised
or silently fails.

`safety_critical=True` prevents the switch from ever reaching
Phase.ML_PRIMARY — there is always a rule to catch the output.
"""

from __future__ import annotations

import re

from dendra import Phase, SwitchConfig, ml_switch

_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_CONFIDENTIAL = ("CONFIDENTIAL", "INTERNAL ONLY", "DO NOT DISTRIBUTE")


@ml_switch(
    labels=["safe", "pii", "confidential"],
    author="@safety:output-gate",
    config=SwitchConfig(phase=Phase.RULE, safety_critical=True),
)
def classify_output(response: str) -> str:
    """Rule-based safety-floor for LLM output.

    Returns the first class that matches. Order matters — PII is
    checked before confidentiality markers because leaking an SSN
    via the "CONFIDENTIAL" cover message is worse than either alone.
    """
    if _SSN.search(response) or _PHONE.search(response) or _EMAIL.search(response):
        return "pii"
    if any(marker in response for marker in _CONFIDENTIAL):
        return "confidential"
    return "safe"


if __name__ == "__main__":
    generated_responses = [
        "Here's the summary you asked for: everything looks good.",
        "Sure, my email is alice@example.com — reach out any time.",
        "The user's SSN is 123-45-6789, per the CRM record.",
        "Per CONFIDENTIAL planning doc, the launch is delayed.",
        "INTERNAL ONLY: see me about the Q2 numbers, please.",
    ]

    print("LLM output -> safety classification")
    print("-" * 72)
    for response in generated_responses:
        verdict = classify_output(response)
        action = {
            "safe": "deliver",
            "pii": "BLOCK — redact before reply",
            "confidential": "BLOCK — internal-only marker",
        }[verdict]
        # Truncate for display
        preview = response if len(response) <= 50 else response[:47] + "..."
        print(f"{preview:52s}  {verdict:14s}  {action}")
