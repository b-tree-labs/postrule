# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures + autouse markers for the chaos suite.

Every test under tests/chaos/ inherits the global sandbox harness from
tests/conftest.py, plus the @pytest.mark.chaos marker auto-applied
here. Tests stay focused on ONE failure mode; helpers live in
``_helpers``.
"""

from __future__ import annotations

import time

import pytest

from dendra import (
    BoundedInMemoryStorage,
    ClassificationRecord,
    LearnedSwitch,
    Phase,
    SwitchConfig,
    Verdict,
)


def pytest_collection_modifyitems(config, items):
    """Auto-apply the chaos marker to every test in this directory.

    Saves boilerplate at the top of every chaos module while keeping
    the marker registered (and discoverable) via pyproject.toml.
    """
    for item in items:
        if "tests/chaos/" in str(getattr(item, "fspath", "")) or "tests\\chaos\\" in str(
            getattr(item, "fspath", "")
        ):
            item.add_marker(pytest.mark.chaos)


@pytest.fixture
def basic_record():
    """Factory: a minimal ClassificationRecord. Pass overrides as kwargs."""

    def _make(**kwargs) -> ClassificationRecord:
        defaults = {
            "timestamp": time.time(),
            "input": "i",
            "label": "x",
            "outcome": Verdict.CORRECT.value,
            "source": "rule",
            "confidence": 1.0,
        }
        defaults.update(kwargs)
        return ClassificationRecord(**defaults)

    return _make


@pytest.fixture
def basic_switch():
    """Factory: a LearnedSwitch with safe defaults for chaos tests.

    auto_record + auto_advance + auto_demote OFF so each test isolates
    the failure mode it cares about. Pass kwargs to override.
    """

    def _make(**kwargs) -> LearnedSwitch:
        defaults = {
            "rule": lambda x: "rule",
            "name": kwargs.pop("name", "chaos_switch"),
            "author": "chaos",
            "config": SwitchConfig(
                starting_phase=Phase.RULE,
                auto_record=False,
                auto_advance=False,
                auto_demote=False,
            ),
            "storage": BoundedInMemoryStorage(),
        }
        defaults.update(kwargs)
        return LearnedSwitch(**defaults)

    return _make
