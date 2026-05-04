# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""Tests for the offline license verifier in src/dendra/license.py.

The matching signer lives at cloud/api/src/license.ts (TypeScript,
running on Cloudflare Workers via SubtleCrypto). This file regenerates
test fixtures locally with the same Ed25519 primitive (via the
``cryptography`` package) so the round-trip — sign here, verify here —
exercises the same JWS-compact format the real Worker emits.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

# Skip the whole module when the optional `license` extra is missing.
pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

from dendra.license import (  # noqa: E402
    LicenseClaims,
    LicenseInvalid,
    verify_license,
)


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _make_token(
    private_key: Ed25519PrivateKey,
    claims: dict,
    *,
    header: dict | None = None,
) -> str:
    """Sign a token using the same JWS-compact shape the Worker emits."""
    h = header or {"alg": "EdDSA", "typ": "DendraLicense", "v": 1}
    header_b64 = _b64u(json.dumps(h, separators=(",", ":")).encode())
    payload_b64 = _b64u(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = private_key.sign(signing_input)
    return f"{header_b64}.{payload_b64}.{_b64u(sig)}"


@pytest.fixture
def keypair():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    pub_hex = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    return priv, pub_hex


@pytest.fixture
def baseline_claims():
    now = int(time.time())
    return {
        "iss": "dendra.run",
        "sub": "42",
        "tier": "business",
        "account_hash": "abc123",
        "iat": now,
        "exp": now + 3600,
        "max_seats": 10,
        "license_id": "lic_test_001",
    }


def test_verify_round_trip(monkeypatch, keypair, baseline_claims):
    priv, pub_hex = keypair
    monkeypatch.setenv("DENDRA_LICENSE_PUBLIC_KEY_HEX", pub_hex)

    token = _make_token(priv, baseline_claims)
    claims = verify_license(token)

    assert isinstance(claims, LicenseClaims)
    assert claims.tier == "business"
    assert claims.sub == "42"
    assert claims.max_seats == 10
    assert claims.license_id == "lic_test_001"


def test_verify_rejects_tampered_payload(monkeypatch, keypair, baseline_claims):
    priv, pub_hex = keypair
    monkeypatch.setenv("DENDRA_LICENSE_PUBLIC_KEY_HEX", pub_hex)

    token = _make_token(priv, baseline_claims)
    h, p, s = token.split(".")
    # Flip a bit in the payload — Ed25519 catches it.
    bad = list(base64.urlsafe_b64decode(p + "==="))
    bad[0] ^= 0xFF
    bad_p = base64.urlsafe_b64encode(bytes(bad)).rstrip(b"=").decode("ascii")
    tampered = f"{h}.{bad_p}.{s}"

    with pytest.raises(LicenseInvalid):
        verify_license(tampered)


def test_verify_rejects_wrong_key(monkeypatch, keypair, baseline_claims):
    priv, _ = keypair
    other = Ed25519PrivateKey.generate().public_key()
    other_hex = other.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    monkeypatch.setenv("DENDRA_LICENSE_PUBLIC_KEY_HEX", other_hex)

    token = _make_token(priv, baseline_claims)
    with pytest.raises(LicenseInvalid, match="signature does not match"):
        verify_license(token)


def test_verify_rejects_expired(monkeypatch, keypair, baseline_claims):
    priv, pub_hex = keypair
    monkeypatch.setenv("DENDRA_LICENSE_PUBLIC_KEY_HEX", pub_hex)

    expired = dict(baseline_claims)
    expired["exp"] = int(time.time()) - 60
    expired["iat"] = int(time.time()) - 3600
    token = _make_token(priv, expired)
    with pytest.raises(LicenseInvalid, match="expired"):
        verify_license(token)


def test_verify_rejects_future_issuance(monkeypatch, keypair, baseline_claims):
    priv, pub_hex = keypair
    monkeypatch.setenv("DENDRA_LICENSE_PUBLIC_KEY_HEX", pub_hex)

    future = dict(baseline_claims)
    future["iat"] = int(time.time()) + 3600  # 1h in the future, beyond skew
    future["exp"] = future["iat"] + 3600
    token = _make_token(priv, future)
    with pytest.raises(LicenseInvalid, match="future"):
        verify_license(token)


def test_verify_rejects_bad_header(monkeypatch, keypair, baseline_claims):
    priv, pub_hex = keypair
    monkeypatch.setenv("DENDRA_LICENSE_PUBLIC_KEY_HEX", pub_hex)

    # Wrong typ: spoofed header claiming this is a different token kind
    token = _make_token(
        priv,
        baseline_claims,
        header={"alg": "EdDSA", "typ": "OtherToken", "v": 1},
    )
    with pytest.raises(LicenseInvalid, match="unsupported header"):
        verify_license(token)


def test_verify_rejects_malformed_token():
    with pytest.raises(LicenseInvalid):
        verify_license("not.a.jws-compact")


def test_verify_no_keys_configured(monkeypatch, keypair, baseline_claims):
    # No env override; built-in registry has only the placeholder which
    # the verifier silently skips → "no public keys configured".
    priv, _ = keypair
    monkeypatch.delenv("DENDRA_LICENSE_PUBLIC_KEY_HEX", raising=False)
    token = _make_token(priv, baseline_claims)
    with pytest.raises(LicenseInvalid, match="no public keys"):
        verify_license(token)
