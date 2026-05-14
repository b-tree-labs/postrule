# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
"""Offline verification of Postrule license tokens.

Customers on the Hosted Business tier can request a signed license
token (issued by the api Worker via POST /admin/licenses/issue) and
install it on offline / air-gapped systems. This module verifies the
token's Ed25519 signature against the embedded public key, with no
network call required.

Usage:

    from postrule.license import verify_license, LicenseInvalid

    try:
        claims = verify_license(token_string)
    except LicenseInvalid as e:
        raise SystemExit(f"license check failed: {e}")
    print(claims["tier"], claims["exp"])

Requires the ``license`` extra: ``pip install postrule[license]`` (which
pulls in ``cryptography``). Without it, importing this module raises
ImportError at first use.

The public key is hard-coded below. When the signing key rotates we
ship a new postrule release with the updated value; old tokens signed by
old private keys are still verifiable as long as the matching public
key is included in this module.
"""

from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------- #
# Public key registry. Multiple keys here means we can rotate without
# breaking previously-issued tokens — verification tries each in turn.
# Each entry: (key_id, hex-encoded raw 32-byte public key).
# --------------------------------------------------------------------------- #

LICENSE_PUBLIC_KEYS_HEX: tuple[tuple[str, str], ...] = (
    # Placeholder for the v1 staging+production key. Replace after
    # running cloud/api/scripts/generate-license-key.ts.
    ("v1-placeholder", "0" * 64),
)

# Allow installs to override at runtime (handy for staging / red-team
# testing without a release). DO NOT use in production.
_ENV_OVERRIDE = "POSTRULE_LICENSE_PUBLIC_KEY_HEX"


class LicenseInvalid(Exception):
    """Raised when a license token fails verification."""


@dataclass(frozen=True)
class LicenseClaims:
    """Parsed + validated claims from a verified license token."""

    iss: str
    sub: str
    tier: str
    account_hash: str
    iat: int
    exp: int
    max_seats: int | None
    license_id: str

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> LicenseClaims:
        try:
            return cls(
                iss=str(d["iss"]),
                sub=str(d["sub"]),
                tier=str(d["tier"]),
                account_hash=str(d["account_hash"]),
                iat=int(d["iat"]),
                exp=int(d["exp"]),
                max_seats=int(d["max_seats"]) if d.get("max_seats") is not None else None,
                license_id=str(d["license_id"]),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise LicenseInvalid(f"malformed claims: {e}") from e


def _b64u_decode(s: str) -> bytes:
    """Decode base64url, accepting strings without padding."""
    pad = (-len(s)) % 4
    return base64.urlsafe_b64decode(s + "=" * pad)


def _candidate_public_keys() -> list[bytes]:
    """Public keys to try, override-first then registry."""
    keys: list[bytes] = []
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        try:
            keys.append(bytes.fromhex(override))
        except ValueError as e:
            raise LicenseInvalid(f"invalid {_ENV_OVERRIDE}: {e}") from e
    for _, hex_value in LICENSE_PUBLIC_KEYS_HEX:
        if hex_value == "0" * 64:
            # Placeholder — skip silently.
            continue
        try:
            keys.append(bytes.fromhex(hex_value))
        except ValueError:
            continue
    return keys


def verify_license(token: str, *, now: float | None = None) -> LicenseClaims:
    """Verify a license token. Returns parsed claims or raises LicenseInvalid.

    The verification chain:

      1. Parse JWS-compact format (3 dot-separated base64url segments).
      2. Confirm header is ``{"alg": "EdDSA", "typ": "PostruleLicense"}``.
      3. Verify signature against each registered public key; pass if any.
      4. Confirm ``exp`` is in the future (with 60-second clock skew).
      5. Return parsed claims.

    Args:
      token: JWS-compact string from the issuer.
      now: Override "current time" in unix seconds (for tests).

    Raises:
      LicenseInvalid: on any verification failure.
      ImportError: if the ``cryptography`` package isn't installed
        (use ``pip install postrule[license]``).
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as e:
        raise ImportError(
            "License verification requires the 'cryptography' package. "
            "Install with: pip install postrule[license]"
        ) from e

    parts = token.split(".")
    if len(parts) != 3:
        raise LicenseInvalid("token must have three dot-separated segments")
    header_b64, payload_b64, sig_b64 = parts

    try:
        header = json.loads(_b64u_decode(header_b64))
    except Exception as e:
        raise LicenseInvalid(f"malformed header: {e}") from e
    if header.get("alg") != "EdDSA" or header.get("typ") != "PostruleLicense":
        raise LicenseInvalid(f"unsupported header: {header!r}")

    try:
        sig = _b64u_decode(sig_b64)
    except Exception as e:
        raise LicenseInvalid(f"malformed signature segment: {e}") from e
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    keys = _candidate_public_keys()
    if not keys:
        raise LicenseInvalid(
            "no public keys configured. Set "
            f"{_ENV_OVERRIDE} or install a postrule release with a real key."
        )

    for raw in keys:
        if len(raw) != 32:
            continue
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(sig, signing_input)
            break
        except InvalidSignature:
            continue
    else:
        raise LicenseInvalid("signature does not match any registered public key")

    try:
        claims_dict = json.loads(_b64u_decode(payload_b64))
    except Exception as e:
        raise LicenseInvalid(f"malformed payload: {e}") from e

    claims = LicenseClaims.from_dict(claims_dict)

    t = now if now is not None else time.time()
    if claims.exp < t:
        raise LicenseInvalid(f"license expired at {claims.exp}; current time is {int(t)}")
    if claims.iat > t + 60:
        raise LicenseInvalid("license issued in the future (clock skew?)")

    return claims


__all__ = ["verify_license", "LicenseClaims", "LicenseInvalid"]
