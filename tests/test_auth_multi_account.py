# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Multi-account credential store + per-project account binding (epic #142, P0).

A single global ``~/.postrule/credentials`` forced every project onto one account.
This adds: multiple accounts keyed by email in the creds file, a default, and a
per-project ``postrule.toml`` that names which account a repo reports to.
Precedence: a resolved stored account (project postrule.toml > default) wins;
``$POSTRULE_API_KEY`` is the fallback when nothing is stored (CI / Cloud Run).
No raw key ever lives in postrule.toml.
"""

from __future__ import annotations

import json

import pytest

from postrule import auth

# Fake keys, isolated so detect-secrets sees one allowlisted literal each.
K1 = "k1"  # pragma: allowlist secret
K2 = "k2"  # pragma: allowlist secret
K_LEGACY = "k-legacy"  # pragma: allowlist secret
K_ENV = "k-env"  # pragma: allowlist secret

CUST = "customer@acme.example"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # conftest already sandboxes HOME to tmp; ensure no env key leaks in.
    monkeypatch.delenv("POSTRULE_API_KEY", raising=False)
    monkeypatch.delenv("POSTRULE_ACCOUNT", raising=False)


# --- backward compatibility -------------------------------------------------


def test_legacy_flat_file_still_loads():
    auth.credentials_path().parent.mkdir(parents=True, exist_ok=True)
    payload = {"api_key": K_LEGACY, "email": "old@x.com", "telemetry_enabled": True}
    auth.credentials_path().write_text(json.dumps(payload))
    creds = auth.load_credentials()
    assert creds["api_key"] == K_LEGACY
    assert creds["email"] == "old@x.com"


# --- multi-account store ----------------------------------------------------


def test_add_and_list_accounts():
    auth.add_account(api_key=K1, email="a@x.com", api_url="https://api.postrule.ai")
    auth.add_account(api_key=K2, email="b@y.com")
    emails = {a["email"] for a in auth.list_accounts()}
    assert emails == {"a@x.com", "b@y.com"}
    # First added becomes default.
    assert auth.load_credentials()["email"] == "a@x.com"


def test_load_specific_account():
    auth.add_account(api_key=K1, email="a@x.com")
    auth.add_account(api_key=K2, email="b@y.com")
    assert auth.load_credentials(account="b@y.com")["api_key"] == K2


def test_set_default_account():
    auth.add_account(api_key=K1, email="a@x.com")
    auth.add_account(api_key=K2, email="b@y.com")
    assert auth.set_default_account("b@y.com") is True
    assert auth.load_credentials()["email"] == "b@y.com"
    assert auth.set_default_account("missing@z.com") is False


def test_remove_account():
    auth.add_account(api_key=K1, email="a@x.com")
    auth.add_account(api_key=K2, email="b@y.com")
    assert auth.remove_account("a@x.com") is True
    assert {a["email"] for a in auth.list_accounts()} == {"b@y.com"}
    # Removing the default repoints default to a remaining account.
    assert auth.load_credentials()["email"] == "b@y.com"


# --- per-project binding ----------------------------------------------------


def test_project_toml_selects_account(tmp_path):
    auth.add_account(api_key=K1, email="a@x.com")
    auth.add_account(api_key=K2, email=CUST)
    proj = tmp_path / "soilmetrix"
    proj.mkdir()
    (proj / "postrule.toml").write_text(f'[postrule]\naccount = "{CUST}"\n')
    assert auth.resolve_account(start_dir=proj) == CUST
    creds = auth.load_credentials(start_dir=proj)
    assert creds["email"] == CUST
    assert creds["api_key"] == K2


def test_project_toml_walks_up_from_subdir(tmp_path):
    auth.add_account(api_key=K2, email=CUST)
    auth.add_account(api_key=K1, email="a@x.com")
    auth.set_default_account("a@x.com")
    proj = tmp_path / "repo"
    (proj / "services" / "webapp").mkdir(parents=True)
    (proj / "postrule.toml").write_text(f'[postrule]\naccount = "{CUST}"\n')
    assert auth.resolve_account(start_dir=proj / "services" / "webapp") == CUST


def test_env_key_used_when_no_store(monkeypatch):
    # No creds file at all (e.g. CI / Cloud Run) -> env POSTRULE_API_KEY is the
    # fallback. A resolved stored account wins over env (logout-honoring), so
    # env only fills in when nothing is stored.
    monkeypatch.setenv("POSTRULE_API_KEY", K_ENV)
    assert auth.load_credentials()["api_key"] == K_ENV


def test_unknown_project_account_falls_back_to_default(tmp_path):
    auth.add_account(api_key=K1, email="a@x.com")
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / "postrule.toml").write_text('[postrule]\naccount = "ghost@nowhere.com"\n')
    # Bound account isn't in the store -> fall back to the default account.
    assert auth.load_credentials(start_dir=proj)["email"] == "a@x.com"


# --- edge cases (coverage of resolution + normalization branches) -----------


def test_no_file_and_no_env_returns_none():
    assert auth.load_credentials() is None


def test_legacy_flat_with_api_url_loads():
    auth.credentials_path().parent.mkdir(parents=True, exist_ok=True)
    payload = {"api_key": K_LEGACY, "email": "old@x.com", "api_url": "https://staging.example"}
    auth.credentials_path().write_text(json.dumps(payload))
    creds = auth.load_credentials()
    assert creds["api_key"] == K_LEGACY
    assert creds["api_url"] == "https://staging.example"


def test_malformed_file_falls_back_to_none():
    auth.credentials_path().parent.mkdir(parents=True, exist_ok=True)
    auth.credentials_path().write_text("{ not valid json")
    assert auth.load_credentials() is None


def test_file_without_key_returns_none():
    auth.credentials_path().parent.mkdir(parents=True, exist_ok=True)
    auth.credentials_path().write_text(json.dumps({"email": "x@y.com"}))
    assert auth.load_credentials() is None


def test_add_account_explicit_set_default_repoints():
    auth.add_account(api_key=K1, email="a@x.com")
    auth.add_account(api_key=K2, email="b@y.com", set_default=True)
    assert auth.load_credentials()["email"] == "b@y.com"


def test_loaded_account_includes_api_url():
    auth.add_account(api_key=K1, email="a@x.com", api_url="https://api.postrule.ai")
    assert auth.load_credentials()["api_url"] == "https://api.postrule.ai"


def test_resolve_account_none_when_store_empty():
    assert auth.resolve_account() is None


def test_malformed_project_toml_returns_none(tmp_path):
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / "postrule.toml").write_text("{ not toml")
    assert auth.project_account(start_dir=proj) is None


def test_remove_missing_account_is_false():
    auth.add_account(api_key=K1, email="a@x.com")
    assert auth.remove_account("nope@z.com") is False


def test_project_account_none_when_no_toml(tmp_path):
    # Walk finds no postrule.toml anywhere -> None (the loop-completes branch).
    assert auth.project_account(start_dir=tmp_path) is None


def test_load_explicit_missing_account_uses_default(tmp_path):
    auth.add_account(api_key=K1, email="a@x.com")
    # Explicit account not in the store -> fall through to the default account.
    assert auth.load_credentials(account="missing@z.com", start_dir=tmp_path)["email"] == "a@x.com"


def test_v2_default_pointing_to_missing_account_repoints():
    auth.credentials_path().parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "accounts": {"a@x.com": {"api_key": K1, "email": "a@x.com"}},
        "default": "ghost@z.com",
    }
    auth.credentials_path().write_text(json.dumps(payload))
    # default names a missing account -> normalization repoints to the present one.
    assert auth.load_credentials()["email"] == "a@x.com"
    assert [a["is_default"] for a in auth.list_accounts()] == [True]
