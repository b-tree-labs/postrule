# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Config-driven state-storage resolution (#148 / #94).

Operators choose where switch state lives via config, not code — the SDK side of
a cloud-console `state_storage` setting. Resolved from env
(`POSTRULE_STATE_BACKEND` / `_DSN` / `_SCHEMA`), offline-safe: no config falls
through to the caller's default, and a configured-but-unreachable Postgres falls
back to the local default rather than breaking the app.
"""

from __future__ import annotations

import os

import pytest

from postrule import LearnedSwitch, Phase
from postrule.storage import BoundedInMemoryStorage, ResilientStorage
from postrule.storage_config import resolve_state_storage


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for k in (
        "POSTRULE_STATE_BACKEND",
        "POSTRULE_STATE_DSN",
        "POSTRULE_STATE_SCHEMA",
        "POSTRULE_STATE_PATH",
    ):
        monkeypatch.delenv(k, raising=False)
    # Reset the one-time warning guard so each test sees a clean slate.
    import postrule.storage_config as sc

    sc._warned = False


def test_no_config_returns_none(monkeypatch):
    assert resolve_state_storage() is None  # caller uses its existing default


def test_memory_backend(monkeypatch):
    monkeypatch.setenv("POSTRULE_STATE_BACKEND", "memory")
    assert isinstance(resolve_state_storage(), BoundedInMemoryStorage)


def test_file_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTRULE_STATE_BACKEND", "file")
    monkeypatch.setenv("POSTRULE_STATE_PATH", str(tmp_path / "state"))
    assert isinstance(resolve_state_storage(), ResilientStorage)


def test_postgres_without_dsn_falls_back(monkeypatch):
    monkeypatch.setenv("POSTRULE_STATE_BACKEND", "postgres")
    assert resolve_state_storage() is None  # no DSN -> local default, no crash


def test_postgres_unreachable_falls_back(monkeypatch):
    # Offline-first: a configured-but-unreachable DB must NOT raise.
    monkeypatch.setenv("POSTRULE_STATE_BACKEND", "postgres")
    monkeypatch.setenv(
        "POSTRULE_STATE_DSN",
        "postgresql://nobody:nobody@127.0.0.1:5999/nope?connect_timeout=2",  # pragma: allowlist secret
    )
    assert resolve_state_storage() is None  # closed port -> fallback, no exception


def test_unknown_backend_falls_back(monkeypatch):
    monkeypatch.setenv("POSTRULE_STATE_BACKEND", "weird")
    assert resolve_state_storage() is None


def test_switch_uses_resolved_backend(tmp_path, monkeypatch):
    # Integration: a switch with no explicit storage= picks up the config.
    monkeypatch.setenv("POSTRULE_STATE_BACKEND", "memory")
    s = LearnedSwitch(rule=lambda _x: "a", name="cfg_sw", starting_phase=Phase.RULE)
    assert isinstance(s._storage, BoundedInMemoryStorage)


def test_explicit_storage_overrides_config(tmp_path, monkeypatch):
    # An explicit storage= always wins over the config resolver.
    monkeypatch.setenv("POSTRULE_STATE_BACKEND", "memory")
    explicit = BoundedInMemoryStorage()
    s = LearnedSwitch(rule=lambda _x: "a", name="cfg_sw2", storage=explicit)
    assert s._storage is explicit


@pytest.mark.skipif(
    not os.environ.get("POSTRULE_TEST_PG_DSN"), reason="no Postgres for the success path"
)
def test_postgres_backend_reachable(monkeypatch):
    from postrule.storage_postgres import PostgresStorage

    monkeypatch.setenv("POSTRULE_STATE_BACKEND", "postgres")
    monkeypatch.setenv("POSTRULE_STATE_DSN", os.environ["POSTRULE_TEST_PG_DSN"])
    monkeypatch.setenv("POSTRULE_STATE_SCHEMA", "pr_cfg_test")
    s = resolve_state_storage()
    assert isinstance(s, PostgresStorage)
    s.delete_state("x", "y")  # smoke: it actually works
