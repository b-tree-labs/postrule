# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Dendra + Axiom OS — wrap a local-LLM classifier through axi serve.

Axiom OS (https://github.com/b-tree-labs/axiom-os) is the shared
local-LLM runtime that other tools on the same machine can talk to:
``pip install axi-platform && axi serve`` brings up a local-LM server
on localhost. Dendra's ``LlamafileAdapter`` connects to it directly.

This is the canonical Path C from the Dendra README — when you'd
rather not depend on a hosted provider at all. The trade-off is
local-LM latency at Phase.RULE; graduation to an in-process sklearn
head erases that on the easy cases.

Run: ``python examples/integrations/axiom_local_lm.py``
Axiom optional — falls back to a stub when the local node isn't up.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from dendra import LlamafileAdapter, ml_switch


def _axiom_node_reachable() -> bool:
    """Best-effort check for a running ``axi serve`` on localhost."""
    try:
        with urllib.request.urlopen(  # noqa: S310 — local URL only
            "http://localhost:8080/v1/models", timeout=0.25
        ) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


# Build the rule. When Axiom is up, we route real LLM calls through
# the LlamafileAdapter; otherwise a tiny offline classifier stands in.
_LABELS = ("safe", "pii", "confidential")


def _offline_stub(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ("ssn", "social security", "credit card")):
        return "pii"
    if "[confidential]" in t or "internal only" in t:
        return "confidential"
    return "safe"


if _axiom_node_reachable():
    _adapter = LlamafileAdapter(model="LLaMA_CPP")  # talks to axi serve

    def _axiom_classify(text: str) -> str:
        # ModelClassifier.classify returns ModelPrediction(label=..., confidence=...).
        try:
            return _adapter.classify(text, _LABELS).label
        except Exception:  # noqa: BLE001 — fall through to offline stub
            return _offline_stub(text)
else:

    def _axiom_classify(text: str) -> str:
        return _offline_stub(text)


@ml_switch(
    labels=["safe", "pii", "confidential"],
    author="@your-team:output-safety",
    safety_critical=True,
)
def safety_check(response: str) -> str:
    """Classify an LLM response before it leaves your service.

    safety_critical=True caps graduation at MODEL_PRIMARY — Dendra
    will not silently let an ML head replace this. The rule (and
    later the verifier) stay as the safety floor.
    """
    return _axiom_classify(response)


if __name__ == "__main__":
    candidate_responses = [
        "Sure, here's a summary of the article.",
        "Your SSN on file is 123-45-6789.",
        "[CONFIDENTIAL] The board meeting concluded with three action items.",
        "Internal only: the merger announcement is scheduled for Q3.",
    ]
    print("Safety classifications via Axiom OS (or offline stub):")
    for r in candidate_responses:
        print(f"  {safety_check(r):>13s}  ←  {r[:60]}")
    print()
    status = safety_check.status()
    print(
        f"Switch '{status.name}' phase={status.phase} "
        f"outcomes={status.outcomes_total}"
    )
    print()
    print(
        "Path C — single shared local-LM node serves Dendra + everything\n"
        "else on the box. Per-call cost is electricity (~$0.00002 on Apple\n"
        "Silicon); the latency win post-graduation still applies."
    )
