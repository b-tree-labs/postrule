# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Enrollment-flag management for Postrule Insights.

Opt-in is a single file at ``~/.postrule/insights-enroll`` containing one
JSON line with the enrollment metadata (timestamp, version, account ID
hash). The file's *existence* is the opt-in flag; its *content* is the
audit-friendly record of when and how the user enrolled.

The OSS path remains telemetry-free unless this file is present. We
read it on every CLI invocation; if absent, we don't queue events.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from typing import Final

from postrule.insights._paths import (
    enrollment_path,
    ensure_postrule_home,
    postrule_home,
)

#: Schema version for the enrollment record. Bump when the structure
#: changes; readers must accept any version they recognize and
#: degrade gracefully on newer ones.
ENROLLMENT_SCHEMA_VERSION: Final[int] = 1


@dataclass(frozen=True)
class EnrollmentState:
    """Captured enrollment record. None of the fields contain PII."""

    enrolled: bool
    enrolled_at: str | None = None  # ISO-8601 UTC; None when not enrolled
    schema_version: int | None = None
    account_hash: str | None = None  # Optional: server-issued, opaque
    consent_text_sha256: str | None = None  # Hash of disclosure shown


def write_enrollment(
    *,
    account_hash: str | None = None,
    consent_text_sha256: str | None = None,
) -> EnrollmentState:
    """Write the enrollment flag and return the captured state.

    ``account_hash`` is optional; the offline-only flow leaves it None
    until the user runs ``postrule login`` and the cloud issues an
    account ID.

    ``consent_text_sha256`` records which disclosure copy the user saw
    so a future audit can confirm the user consented to the version of
    the policy that was current at enrollment time.
    """
    ensure_postrule_home()
    state = EnrollmentState(
        enrolled=True,
        enrolled_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        schema_version=ENROLLMENT_SCHEMA_VERSION,
        account_hash=account_hash,
        consent_text_sha256=consent_text_sha256,
    )
    payload = {
        "enrolled": True,
        "enrolled_at": state.enrolled_at,
        "schema_version": state.schema_version,
        "account_hash": state.account_hash,
        "consent_text_sha256": state.consent_text_sha256,
    }
    enrollment_path().write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return state


def write_unenrollment() -> EnrollmentState:
    """Remove the enrollment flag. Idempotent; missing file is fine."""
    p = enrollment_path()
    if p.exists():
        p.unlink()
    return EnrollmentState(enrolled=False)


def read_enrollment() -> EnrollmentState:
    """Read the enrollment file; return ``EnrollmentState(enrolled=False)`` if absent.

    Tolerates: missing file (returns not-enrolled), empty file (returns
    not-enrolled), corrupt JSON (returns not-enrolled — fail closed; we
    never silently treat a corrupt file as enrolled).
    """
    p = enrollment_path()
    if not p.exists():
        return EnrollmentState(enrolled=False)
    try:
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            return EnrollmentState(enrolled=False)
        payload = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return EnrollmentState(enrolled=False)
    if not isinstance(payload, dict) or not payload.get("enrolled"):
        return EnrollmentState(enrolled=False)
    return EnrollmentState(
        enrolled=True,
        enrolled_at=payload.get("enrolled_at"),
        schema_version=payload.get("schema_version"),
        account_hash=payload.get("account_hash"),
        consent_text_sha256=payload.get("consent_text_sha256"),
    )


def is_enrolled() -> bool:
    """Fast check: returns True iff the user has opted in to Insights.

    Cheap enough to call on every CLI invocation; reads one small file.
    """
    return read_enrollment().enrolled


# Re-exported so callers can avoid importing ``_paths`` directly.
__all__ = [
    "ENROLLMENT_SCHEMA_VERSION",
    "EnrollmentState",
    "postrule_home",
    "is_enrolled",
    "read_enrollment",
    "write_enrollment",
    "write_unenrollment",
]
