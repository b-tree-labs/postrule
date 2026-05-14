# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Postrule Insights — opt-in cohort flywheel.

Insights is the opt-in side of Postrule: a small machine-readable program
that lets contributing users improve the experience for all users via
aggregate-tuned defaults, without sharing source, labels, or any
identifiable content.

The package has three responsibilities, each gated on explicit user
action:

1. **Tuned-defaults fetch** (default-on, opt-out via ``--no-insights``).
   Pulls a public, signed cohort-defaults JSON document on first call
   per day and falls back to baked-in defaults if the fetch fails.
   Receiving the cohort wisdom does NOT require sharing data.

2. **Enrollment** (default-off, opt-in via ``postrule insights enroll``).
   A single-byte flag at ``~/.postrule/insights-enroll`` opts the user
   in to contributing their analyzer-shape and lift-outcome telemetry
   to the cohort. The OSS path is telemetry-free unless the flag is
   present.

3. **Event queue + best-effort flush**. When enrolled, every
   ``postrule analyze`` / ``postrule init`` / ``postrule bench`` invocation
   appends an event to ``~/.postrule/insights-queue.jsonl``. The queue
   flushes on the next CLI invocation; failures are swallowed and the
   queue persists for the next attempt.

What we capture is documented in
``docs/working/telemetry-program-design-2026-04-28.md`` and at
``https://postrule.ai/insights/transparency``. The short version:
fingerprint and shape, never content; HMAC of email + rotating salt
for account ID; no IP, no machine ID, no source, no labels.

The Phase A (pre-launch) implementation skips Ed25519 signature
verification on the tuned-defaults JSON; HTTPS to postrule.ai provides
integrity guarantees through the public CA system. Signature
verification lands in Phase B (post-v1.1) when ``cryptography`` becomes
an optional extra. The ``signature`` field is preserved in the JSON
schema so adding verification later is a non-breaking change.
"""

from __future__ import annotations

from postrule.insights.disclosure import (
    DISCLOSURE_TEXT,
    DISCLOSURE_VERSION,
    disclosure_text_sha256,
)
from postrule.insights.enrollment import (
    EnrollmentState,
    is_enrolled,
    read_enrollment,
    write_enrollment,
    write_unenrollment,
)
from postrule.insights.events import (
    InsightsEvent,
    flush_queue,
    flush_queue_async,
    queue_event,
    read_queue,
)
from postrule.insights.fingerprint import (
    fingerprint_function,
    fingerprint_repo_files,
)
from postrule.insights.tuned_defaults import (
    BAKED_IN_DEFAULTS,
    TunedDefaults,
    fetch_tuned_defaults,
    get_tuned_defaults_url,
    load_cached_or_baked_in,
    refresh_if_stale,
    refresh_if_stale_async,
)

__all__ = [
    "BAKED_IN_DEFAULTS",
    "DISCLOSURE_TEXT",
    "DISCLOSURE_VERSION",
    "EnrollmentState",
    "InsightsEvent",
    "TunedDefaults",
    "disclosure_text_sha256",
    "fetch_tuned_defaults",
    "fingerprint_function",
    "fingerprint_repo_files",
    "flush_queue",
    "flush_queue_async",
    "get_tuned_defaults_url",
    "is_enrolled",
    "load_cached_or_baked_in",
    "refresh_if_stale",
    "refresh_if_stale_async",
    "queue_event",
    "read_enrollment",
    "read_queue",
    "write_enrollment",
    "write_unenrollment",
]
