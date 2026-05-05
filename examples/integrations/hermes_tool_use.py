# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0
"""Dendra + NousResearch Hermes — wrap a tool-selection call.

Hermes is the de-facto open-weights model family for function-
calling / tool-use. A typical Hermes-driven agent loop calls the
model to pick which tool to invoke for the current step. That tool-
selection decision is a classifier with a finite label set (the
tools you registered) — exactly the shape Dendra graduates.

Why this matters for an open-weights stack: Hermes self-hosts cheaply
(~$0.00002/call on a single A100), but the latency floor is the
network + model. Graduating to an in-process sklearn head removes
both — sub-millisecond decisions for the easy cases.

Run: ``python examples/integrations/hermes_tool_use.py``
Hermes-Function-Calling client optional — falls back to a stub.
"""

from __future__ import annotations

import json

from dendra import ml_switch

# Hermes Function Calling typically goes via a local OpenAI-compatible
# endpoint (vLLM / Ollama / llamafile). Use openai-python as the client
# if available; otherwise use the offline stub.
try:
    from openai import OpenAI

    _HAS_OPENAI_CLIENT = True
except ImportError:
    _HAS_OPENAI_CLIENT = False


# Tools the agent has access to. Hermes picks one per step.
_TOOLS = ["search_web", "read_file", "run_sql", "send_email", "ask_user", "finish"]


_SYSTEM = """You are a tool-selecting agent. Given the current task and the
recent step history, choose ONE tool to invoke next from this list:
{tool_list}
Reply as JSON: {{"tool": "<one of the above>", "reason": "<one sentence>"}}.
"""


def _stub_pick_tool(task: str, history: list[str]) -> str:
    """Offline heuristic dispatch when the Hermes endpoint isn't reachable."""
    t = task.lower()
    if "find" in t or "look up" in t:
        return "search_web"
    if "read" in t or "open" in t:
        return "read_file"
    if "query" in t or "select" in t or "rows" in t:
        return "run_sql"
    if "notify" in t or "email" in t:
        return "send_email"
    if len(history) >= 5:
        return "finish"
    return "ask_user"


def _hermes_pick_tool(task: str, history: list[str]) -> str:
    """Call a local Hermes endpoint (OpenAI-compatible) for tool selection.

    Falls back to the offline stub when the openai client isn't installed
    OR when the local Hermes endpoint isn't reachable — so this example
    runs end-to-end on a fresh dev machine.
    """
    if not _HAS_OPENAI_CLIENT:
        return _stub_pick_tool(task, history)
    # Production wiring: point base_url at your Hermes endpoint.
    try:
        client = OpenAI(base_url="http://localhost:11434/v1", api_key="local")
        resp = client.chat.completions.create(
            model="NousResearch/Hermes-4-70B",  # or whatever you self-host
            messages=[
                {
                    "role": "system",
                    "content": _SYSTEM.format(tool_list=", ".join(_TOOLS)),
                },
                {
                    "role": "user",
                    "content": f"Task: {task}\nHistory: {history!r}",
                },
            ],
            temperature=0,
            max_tokens=80,
            timeout=2.0,
        )
        body = json.loads(resp.choices[0].message.content)
        return body["tool"]
    except Exception:  # noqa: BLE001 — endpoint unreachable / parse error
        return _stub_pick_tool(task, history)


@ml_switch(
    labels=_TOOLS,
    author="@your-team:agent-tool-select",
)
def pick_next_tool(task: str, history: list[str]) -> str:
    return _hermes_pick_tool(task, history)


if __name__ == "__main__":
    scenarios = [
        ("Find recent papers on graduated autonomy", []),
        ("Read the config at /etc/dendra/cohort.yaml", []),
        ("Query the events table for yesterday's signups", []),
        ("Notify @oncall that drift was detected", []),
        (
            "Continue from previous step",
            ["search_web", "read_file", "run_sql", "ask_user", "ask_user"],
        ),
    ]
    print("Tool selections (Phase.RULE — Hermes still primary):")
    for task, history in scenarios:
        print(f"  {pick_next_tool(task, history):>11s}  ←  {task[:54]}")
    print()
    status = pick_next_tool.status()
    print(f"Switch '{status.name}' phase={status.phase} outcomes={status.outcomes_total}")
    print()
    print(
        "Latency at Phase.RULE (Hermes 70B local):  ~150 ms p50.\n"
        "Latency post-graduation (sklearn head):    <1 ms p50.\n"
        "Open-weights win — keep your stack on-prem, add Dendra's gate."
    )
