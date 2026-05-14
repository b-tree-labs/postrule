# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Disclosure copy + consent-text hash.

The exact text shown to the user at enroll time is recorded
(by SHA-256) into the enrollment file so a future audit can
confirm the user consented to *the version of the policy that was
current at enrollment time*. If we change the disclosure copy
later, existing enrolled users' record reflects the older copy
they actually saw — not the one the policy page shows today.

When updating ``DISCLOSURE_TEXT``, bump ``DISCLOSURE_VERSION`` so
the audit trail can correlate enrollment record → policy version.
"""

from __future__ import annotations

import hashlib
from typing import Final

DISCLOSURE_VERSION: Final[int] = 1

DISCLOSURE_TEXT: Final[str] = """\
Postrule Insights — opt-in cohort flywheel.

The OSS path of Postrule is telemetry-free by default — no phone-home,
no analytics, nothing leaves your machine. Joining Postrule Insights
opts you in to sharing *anonymized* analyzer-shape and lift-outcome
data so the cohort can tune defaults that benefit everyone.

What Insights captures (when enrolled):
  - The SHAPE of classification sites (AST-shape hash, pattern, regime,
    label cardinality, priority score, lift status).
  - Lift-outcome events (success / refused / reverted-within-24h).
  - Aggregated benchmark phase-advance events (no input/output content).
  - A non-reversible HMAC of your account email + a server-rotating salt.

What Insights NEVER captures:
  - Source code. Ever.
  - Function names, label values, or any string content.
  - LLM prompts or model outputs.
  - File paths beyond a non-reversible repo-shape hash.
  - IP addresses, environment variables, or machine identifiers.

You can leave Insights at any time with `postrule insights leave`.
Leaving stops new uploads immediately and queues a warehouse-side
delete of your hashed-ID slot, completed within 24 hours.

The full data dictionary, retention policy, and DPIA are at
https://postrule.ai/insights/transparency. The OSS classification
path stays telemetry-free regardless of enrollment status.

Continue and enroll? [y/N]
"""


def disclosure_text_sha256() -> str:
    """Return SHA-256 hex digest of the current disclosure text.

    Embedded in the enrollment record so a later audit can
    correlate enrollment record → policy version.
    """
    return hashlib.sha256(DISCLOSURE_TEXT.encode("utf-8")).hexdigest()


__all__ = ["DISCLOSURE_TEXT", "DISCLOSURE_VERSION", "disclosure_text_sha256"]
