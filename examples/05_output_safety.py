# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""Output safety — classify language model *output* before it reaches users.

Run: `python examples/05_output_safety.py`

The same primitive that gates input routing gates output delivery.
``safety_critical=True`` guarantees the rule floor remains reachable
even after the switch graduates — PII and confidential markers are
caught even if an ML head is compromised or silently fails. Paired
with shadow-logging, the rule/language model boundary converges on what
actually ships rather than what an analyst guessed.
"""

from __future__ import annotations

import re

from postrule import ml_switch

_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_CONFIDENTIAL = ("CONFIDENTIAL", "INTERNAL ONLY", "DO NOT DISTRIBUTE")


@ml_switch(
    # list[str] labels: the caller wants the verdict back and decides
    # what to do in its own layer. Use dict-labels (example 01) when
    # you want Postrule to dispatch a handler on match.
    labels=["safe", "pii", "confidential"],
    safety_critical=True,
)
def output_safety_rule(response: str) -> str:
    """Return the first matching class. Order matters — PII is
    checked before confidentiality markers because leaking an SSN
    via a "CONFIDENTIAL" cover message is worse than either alone.
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

    print("model output -> safety classification")
    print("-" * 72)
    for generated in generated_responses:
        verdict = output_safety_rule(generated)
        action = {
            "safe": "deliver",
            "pii": "BLOCK — redact before reply",
            "confidential": "BLOCK — internal-only marker",
        }[verdict]
        preview = generated if len(generated) <= 50 else generated[:47] + "..."
        print(f"{preview:52s}  {verdict:14s}  {action}")
