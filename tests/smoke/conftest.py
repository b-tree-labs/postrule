# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""pytest fixtures for smoke tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def smoke_target() -> str:
    """Resolved base URL for smoke tests.

    Defaults to https://staging.dendra.run; overridable via env.
    """
    return os.environ.get("DENDRA_SMOKE_BASE_URL", "https://staging.dendra.run").rstrip(
        "/"
    )


@pytest.fixture(scope="session")
def smoke_models_target(smoke_target: str) -> str:
    """Models CDN base URL.

    Default conventions:
      - https://staging.dendra.run → https://staging-models.dendra.run
      - https://dendra.run → https://models.dendra.run
      - http://localhost:* → uses smoke_target itself (test fixture serves)
    """
    override = os.environ.get("DENDRA_SMOKE_MODELS_URL")
    if override:
        return override.rstrip("/")
    if "staging" in smoke_target:
        return "https://staging-models.dendra.run"
    if "dendra.run" in smoke_target and "staging" not in smoke_target:
        return "https://models.dendra.run"
    return smoke_target  # localhost / fixture-served


@pytest.fixture(scope="session")
def smoke_collector_target(smoke_target: str) -> str:
    override = os.environ.get("DENDRA_SMOKE_COLLECTOR_URL")
    if override:
        return override.rstrip("/")
    if "staging" in smoke_target:
        return "https://staging-collector.dendra.run"
    if "dendra.run" in smoke_target and "staging" not in smoke_target:
        return "https://collector.dendra.run"
    return smoke_target


@pytest.fixture(scope="session")
def is_production(smoke_target: str) -> bool:
    """Whether smoke tests are running against the production environment.

    Read-only restriction kicks in when this is True — no POSTs allowed.
    """
    return smoke_target.rstrip("/") == "https://dendra.run"
