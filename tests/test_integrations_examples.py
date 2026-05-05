# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests for examples/integrations/*.py.

Each integration example is a self-contained file that demonstrates
wrapping a routing or classifier call with @ml_switch. The tests
check three things:

1. The example imports without ImportError (offline-stub fallback works
   when the framework dependency isn't installed in the test env).
2. The wrapped function is exposed and exposes the LearnedSwitch
   surface (``.status()`` returns Phase.RULE baseline).
3. The wrapped function returns one of its declared labels for a
   sample input.

Each example doubles as documentation; this file is the contract that
keeps them runnable.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_INTEGRATIONS_DIR = Path(__file__).resolve().parent.parent / "examples" / "integrations"


def _load(name: str):
    path = _INTEGRATIONS_DIR / name
    spec = importlib.util.spec_from_file_location(f"_integration_example_{path.stem}", path)
    assert spec and spec.loader, f"failed to spec module for {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    ("filename", "wrapped_attr", "sample_input", "expected_labels"),
    [
        (
            "langchain_triage.py",
            "route_query",
            ("Why was I charged $99 twice this month?",),
            {"billing", "technical", "account", "other"},
        ),
        (
            "llamaindex_router.py",
            "select_strategy",
            ("How does the auth service relate to the billing service?",),
            {"vector", "bm25", "hybrid", "summary", "graph"},
        ),
        (
            "litellm_classify.py",
            "classify_ticket",
            ("App keeps crashing when uploading a file",),
            {"bug", "feature_request", "question", "billing"},
        ),
        (
            "hermes_tool_use.py",
            "pick_next_tool",
            ("Find recent papers on graduated autonomy", []),
            {
                "search_web",
                "read_file",
                "run_sql",
                "send_email",
                "ask_user",
                "finish",
            },
        ),
        (
            "axiom_local_lm.py",
            "safety_check",
            ("Your SSN on file is 123-45-6789.",),
            {"safe", "pii", "confidential"},
        ),
    ],
)
def test_integration_example(filename, wrapped_attr, sample_input, expected_labels):
    mod = _load(filename)
    fn = getattr(mod, wrapped_attr)

    # Wrapped switches expose .status() — proves @ml_switch decoration applied.
    assert hasattr(fn, "status"), f"{wrapped_attr} not @ml_switch-wrapped"
    st = fn.status()
    assert str(st.phase) == "Phase.RULE", f"unexpected starting phase: {st.phase}"
    assert st.name == wrapped_attr

    # Calling the wrapped function returns a declared label.
    label = fn(*sample_input)
    assert label in expected_labels, (
        f"{wrapped_attr}({sample_input!r}) → {label!r}; expected one of {sorted(expected_labels)}"
    )
