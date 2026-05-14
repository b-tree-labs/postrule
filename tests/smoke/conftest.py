# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""pytest fixtures for smoke tests."""

from __future__ import annotations

import os

import pytest


# Smoke tests need real network. The repo conftest.py installs an
# autouse outbound-network blocker that's the right behavior for the
# rest of the suite. Smoke tests opt out by requesting the
# ``network_enabled`` fixture; we autouse it here so every test under
# tests/smoke/ implicitly gets network access without per-test boilerplate.
@pytest.fixture(autouse=True)
def _allow_outbound_network_for_smoke(network_enabled):
    """Bypass the repo's outbound-network sandbox for smoke tests."""
    yield


@pytest.fixture(scope="session")
def smoke_target() -> str:
    """Resolved base URL for smoke tests.

    Defaults to https://staging.postrule.ai; overridable via env.
    """
    return os.environ.get("POSTRULE_SMOKE_BASE_URL", "https://staging.postrule.ai").rstrip("/")


@pytest.fixture(scope="session")
def smoke_models_target(smoke_target: str) -> str:
    """Models CDN base URL.

    Default conventions:
      - https://staging.postrule.ai → https://staging-models.postrule.ai
      - https://postrule.ai → https://models.postrule.ai
      - http://localhost:* → uses smoke_target itself (test fixture serves)
    """
    override = os.environ.get("POSTRULE_SMOKE_MODELS_URL")
    if override:
        return override.rstrip("/")
    if "staging" in smoke_target:
        return "https://staging-models.postrule.ai"
    if "postrule.ai" in smoke_target and "staging" not in smoke_target:
        return "https://models.postrule.ai"
    return smoke_target  # localhost / fixture-served


@pytest.fixture(scope="session")
def smoke_collector_target(smoke_target: str) -> str:
    override = os.environ.get("POSTRULE_SMOKE_COLLECTOR_URL")
    if override:
        return override.rstrip("/")
    if "staging" in smoke_target:
        return "https://staging-collector.postrule.ai"
    if "postrule.ai" in smoke_target and "staging" not in smoke_target:
        return "https://collector.postrule.ai"
    return smoke_target


@pytest.fixture(scope="session")
def is_production(smoke_target: str) -> bool:
    """Whether smoke tests are running against the production environment.

    Read-only restriction kicks in when this is True — no POSTs allowed.
    """
    return smoke_target.rstrip("/") == "https://postrule.ai"
