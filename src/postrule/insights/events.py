# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Event queue + best-effort flush for opted-in users.

The queue is a JSON-lines file at ``~/.postrule/insights-queue.jsonl``.
Each line is one event. Append-only by design so concurrent CLI
invocations don't lose events (we use ``fcntl.flock`` on POSIX where
available; Windows takes the small race window).

The flush is best-effort, on a separate thread, with a hard timeout.
A failed flush leaves the queue in place for the next CLI invocation
to retry. The CLI never blocks waiting for a flush to complete.

What we capture is documented in the 2026-04-28 design doc and
mirrored in the schema constraints of :class:`InsightsEvent`. Any
fields not on this whitelist are dropped at the collector boundary.
"""

from __future__ import annotations

import datetime as _dt
import errno
import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from postrule.insights._paths import (
    ensure_postrule_home,
    queue_path,
)

_log = logging.getLogger(__name__)

#: Public collector endpoint. Phase A is single-region (US); Phase B
#: introduces region-aware routing.
COLLECTOR_URL: Final[str] = "https://collector.postrule.ai/v1/events"

#: Cap on the number of events flushed in one POST. Keeps the request
#: bounded so a slow CLI session doesn't accumulate a multi-MB body.
FLUSH_BATCH_SIZE: Final[int] = 64

#: Hard timeout on the flush thread. The CLI joins for at most this long
#: before continuing without waiting for the network.
FLUSH_TIMEOUT_SECONDS: Final[float] = 2.0

#: Schema version embedded in every event for forward compatibility.
EVENT_SCHEMA_VERSION: Final[int] = 1

#: Allowed event types — anything else is rejected at queue time.
EVENT_TYPES: Final[frozenset[str]] = frozenset({"analyze", "init_attempt", "bench_phase_advance"})


@dataclass(frozen=True)
class InsightsEvent:
    """One queued event. Shape-only; never carries content.

    Field constraints (enforced by ``queue_event``):

    - ``event_type``: one of EVENT_TYPES.
    - ``schema_version``: monotonic int; readers tolerate higher.
    - ``site_fingerprint``: blake2b hash of normalized AST-shape, or
      None if the event isn't site-bound.
    - ``payload``: dict whose keys are listed below; unknown keys are
      stripped at queue time so a future schema bump can't regress
      privacy by smuggling unintended data.

    Allowed payload keys per event_type:

    - ``analyze``: files_scanned, total_sites, already_dendrified_count,
      pattern_histogram, regime_histogram, lift_status_histogram,
      hazard_category_histogram. Per-site granularity (pattern,
      priority_score, etc.) is intentionally *not* emitted from this
      event; it would tie back to specific code locations.
    - ``init_attempt``: lifter, outcome, time_to_action_seconds,
      reverted_within_24h.
    - ``bench_phase_advance``: phase_before, phase_after, verdict_count,
      cost_per_call_micros, latency_p50_us, latency_p95_us.
    """

    event_type: str
    timestamp: str  # ISO-8601 UTC
    schema_version: int = EVENT_SCHEMA_VERSION
    site_fingerprint: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


_PAYLOAD_KEY_WHITELIST: Final[dict[str, frozenset[str]]] = {
    # One event per `postrule analyze` run with run-level aggregates only.
    # Per-site granularity is emitted by ``init_attempt`` instead, which
    # only fires when the user actually acts on a specific site —
    # privacy-friendlier than per-site analyze emission.
    "analyze": frozenset(
        {
            "files_scanned",
            "total_sites",
            "already_dendrified_count",
            "pattern_histogram",  # {"P1": 30, "P4": 5, ...}
            "regime_histogram",  # {"narrow": 50, "medium": 8, ...}
            "lift_status_histogram",  # {"auto_liftable": 7, "refused": 55}
            "hazard_category_histogram",  # {"side_effect_evidence": 10, ...}
        }
    ),
    "init_attempt": frozenset(
        {
            "lifter",
            "outcome",
            "pattern",  # P1..P6 of the targeted site
            "regime",
            "label_cardinality",
            "time_to_action_seconds",
            "reverted_within_24h",
        }
    ),
    "bench_phase_advance": frozenset(
        {
            "phase_before",
            "phase_after",
            "verdict_count",
            "cost_per_call_micros",
            "latency_p50_us",
            "latency_p95_us",
        }
    ),
}


def _strip_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Drop unknown keys; protects privacy against well-meaning misuse.

    A future schema bump that adds a new field requires also updating
    this whitelist; that's intentional friction so privacy review is
    invoked at the same moment as the schema change.
    """
    allowed = _PAYLOAD_KEY_WHITELIST.get(event_type, frozenset())
    return {k: v for k, v in payload.items() if k in allowed}


def queue_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    site_fingerprint: str | None = None,
) -> InsightsEvent | None:
    """Append an event to the queue. Return None if rejected.

    Rejected if ``event_type`` isn't in the whitelist. Caller is
    expected to check :func:`is_enrolled` first; this function does
    NOT gate on enrollment because the test suite needs to exercise
    the queue directly.
    """
    if event_type not in EVENT_TYPES:
        return None
    payload = _strip_payload(event_type, payload or {})
    event = InsightsEvent(
        event_type=event_type,
        timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        schema_version=EVENT_SCHEMA_VERSION,
        site_fingerprint=site_fingerprint,
        payload=payload,
    )
    try:
        ensure_postrule_home()
        path = queue_path()
        line = json.dumps(asdict(event), separators=(",", ":")) + "\n"
        with _locked_append(path) as fh:
            fh.write(line)
    except OSError as e:
        _log.debug("insights queue append failed: %s", e)
        return None
    return event


def read_queue() -> list[InsightsEvent]:
    """Read all queued events. Returns ``[]`` if the queue is missing.

    Tolerates: missing file, empty file, lines that fail JSON parse
    (skipped). A corrupt line never aborts the read.
    """
    path = queue_path()
    if not path.exists():
        return []
    out: list[InsightsEvent] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        try:
            out.append(
                InsightsEvent(
                    event_type=str(obj.get("event_type", "")),
                    timestamp=str(obj.get("timestamp", "")),
                    schema_version=int(obj.get("schema_version", EVENT_SCHEMA_VERSION)),
                    site_fingerprint=obj.get("site_fingerprint"),
                    payload=dict(obj.get("payload") or {}),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def flush_queue(
    *,
    url: str = COLLECTOR_URL,
    timeout: float = FLUSH_TIMEOUT_SECONDS,
    batch_size: int = FLUSH_BATCH_SIZE,
) -> int:
    """Best-effort flush. Returns the count of events successfully posted.

    Behavior:
      - Reads the current queue.
      - Posts up to ``batch_size`` events in one HTTPS request.
      - On 2xx response, atomically rewrites the queue with the
        unsent remainder.
      - On any failure, leaves the queue untouched for the next try.

    Runs synchronously; callers wanting non-blocking behavior should
    invoke this on a daemon thread with a join timeout.
    """
    events = read_queue()
    if not events:
        return 0
    head, tail = events[:batch_size], events[batch_size:]
    body = json.dumps(
        {
            "schema_version": EVENT_SCHEMA_VERSION,
            "events": [asdict(e) for e in head],
        }
    ).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "postrule-insights/1.0 (+https://postrule.ai)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — HTTPS
            if not (200 <= resp.status < 300):
                return 0
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _log.debug("insights flush failed: %s", e)
        return 0
    # Persist the remaining tail; truncate before write so a partial
    # write can never duplicate already-flushed events on the next read.
    try:
        path = queue_path()
        if tail:
            text = "".join(json.dumps(asdict(e), separators=(",", ":")) + "\n" for e in tail)
            path.write_text(text, encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError as e:
        _log.warning("insights queue rewrite failed after successful flush: %s", e)
    return len(head)


def flush_queue_async(*, timeout: float = FLUSH_TIMEOUT_SECONDS) -> threading.Thread:
    """Spawn a daemon thread to run :func:`flush_queue`.

    Returns the thread so callers can ``thread.join(timeout)`` if they
    want to wait for completion. The CLI typically does NOT join,
    relying on the daemon-thread cleanup at process exit.
    """
    thread = threading.Thread(
        target=flush_queue,
        kwargs={"timeout": timeout},
        name="postrule-insights-flush",
        daemon=True,
    )
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# File-locking append helper
#
# fcntl.flock on POSIX prevents concurrent writers from interleaving partial
# JSON lines. Windows lacks fcntl; the small race window is acceptable for
# a JSON-line append (worst case: one corrupt line that read_queue() drops).
# ---------------------------------------------------------------------------


@contextmanager
def _locked_append(path) -> Iterator[Any]:
    try:
        import fcntl

        with open(path, "a", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except OSError as e:
                # Filesystem doesn't support flock (NFS without lockd, some
                # Docker overlay setups). Fall through to lock-free append.
                if e.errno not in (errno.ENOLCK, errno.ENOSYS):
                    raise
            try:
                yield fh
            finally:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
    except ImportError:
        # Windows: no fcntl. Best-effort append without locking.
        with open(path, "a", encoding="utf-8") as fh:
            yield fh


__all__ = [
    "COLLECTOR_URL",
    "EVENT_SCHEMA_VERSION",
    "EVENT_TYPES",
    "FLUSH_BATCH_SIZE",
    "FLUSH_TIMEOUT_SECONDS",
    "InsightsEvent",
    "flush_queue",
    "flush_queue_async",
    "queue_event",
    "read_queue",
]
