# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Tuned defaults — fetch + cache + parse + fallback.

The cohort-tuned defaults JSON document lives at
``https://postrule.ai/insights/tuned-defaults.json`` and is refreshed
nightly by the aggregator. Every Postrule install fetches it on first
analyzer/init/bench call per day and caches the result at
``~/.postrule/tuned-defaults.json``. Receiving the cohort wisdom does
NOT require enrollment — fetching public aggregate data is just
configuration update, not telemetry.

Failure modes are silent on purpose:

- Fetch error → fall back to the cached copy if any.
- Cached copy missing or corrupt → fall back to the baked-in defaults.
- Baked-in defaults exist for every parameter the analyzer reads.

The defaults block carries cohort-size and timestamp so callers can
display "tuned from N deployments as of Y" in surfaces like
``postrule status --insights``.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from postrule.insights._paths import (
    ensure_postrule_home,
    tuned_defaults_cache_path,
)

_log = logging.getLogger(__name__)

#: Public URL for the signed cohort-defaults document. Override with
#: ``POSTRULE_INSIGHTS_URL`` for staging, dev, or air-gapped deployments
#: that need to point at a private mirror of the same JSON shape.
DEFAULT_TUNED_DEFAULTS_URL: Final[str] = "https://postrule.ai/insights/tuned-defaults.json"


def get_tuned_defaults_url() -> str:
    """Resolve the tuned-defaults URL, honoring POSTRULE_INSIGHTS_URL."""
    return os.environ.get("POSTRULE_INSIGHTS_URL", DEFAULT_TUNED_DEFAULTS_URL)


# Back-compat alias — early code reads ``TUNED_DEFAULTS_URL`` directly.
# The value is captured at import time, so callers that need the
# env-var-honoring resolution should call :func:`get_tuned_defaults_url`.
TUNED_DEFAULTS_URL: Final[str] = DEFAULT_TUNED_DEFAULTS_URL

#: How long a cached copy is fresh enough to skip the network fetch.
#: 24h matches the nightly aggregator cadence; longer would mean
#: install lag, shorter would hammer the endpoint.
CACHE_FRESHNESS_HOURS: Final[int] = 24

#: Network timeout. Generous enough for slow links; bounded so a stuck
#: collector can't stall every CLI invocation.
FETCH_TIMEOUT_SECONDS: Final[float] = 5.0


@dataclass(frozen=True)
class TunedDefaults:
    """Cohort-tuned parameters fetched from postrule.ai.

    All fields have safe baked-in defaults so a missing or stale file
    never breaks the analyzer. ``cohort_size`` of 0 means "no cohort
    data yet, baked-in values in effect."
    """

    version: int = 0
    generated_at: str | None = None
    cohort_size: int = 0
    #: Per-regime (narrow / medium / high / unknown) median outcomes
    #: to graduation. Used in CLI hints and `postrule status --insights`.
    median_outcomes_to_graduation: dict[str, int] = field(default_factory=dict)
    #: Per-regime suggested ``min_outcomes`` for the gate.
    suggested_min_outcomes: dict[str, int] = field(default_factory=dict)
    #: Optional gate alpha override. None means "use baked-in 0.01".
    suggested_alpha: float | None = None
    #: Top-N pattern frequencies (P1..P6) across the cohort.
    pattern_frequencies: dict[str, float] = field(default_factory=dict)
    #: Top-N refusal categories ranked by prevalence.
    top_refusal_categories: list[str] = field(default_factory=list)
    #: Cohort-median fraction of detected sites with priority_score
    #: >= 4.0. Populated by the aggregator once cohort_size >= 10.
    #: ``None`` means "not yet enough signal" — CLI emitter suppresses
    #: the comparison line in that state.
    median_high_priority_density: float | None = None
    #: Reserved for Phase B Ed25519 verification. Phase A ignores it.
    signature: str | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> TunedDefaults:
        """Parse a JSON payload into a TunedDefaults.

        Tolerant: unknown keys are ignored; missing keys take defaults;
        type mismatches per-field fall back to the baked-in default
        for that field. We never raise on a malformed cohort document
        — falling back to baked-in defaults is always safe.
        """
        if not isinstance(payload, dict):
            return BAKED_IN_DEFAULTS
        defaults_block = payload.get("defaults", {})
        if not isinstance(defaults_block, dict):
            defaults_block = {}
        return cls(
            version=_int_or(payload.get("version"), 0),
            generated_at=_str_or(payload.get("generated_at"), None),
            cohort_size=_int_or(payload.get("cohort_size"), 0),
            median_outcomes_to_graduation=_dict_int_or(
                defaults_block.get("median_outcomes_to_graduation"), {}
            ),
            suggested_min_outcomes=_dict_int_or(defaults_block.get("suggested_min_outcomes"), {}),
            suggested_alpha=_float_or(defaults_block.get("suggested_alpha"), None),
            pattern_frequencies=_dict_float_or(defaults_block.get("pattern_frequencies"), {}),
            top_refusal_categories=_list_str_or(defaults_block.get("top_refusal_categories"), []),
            median_high_priority_density=_float_or(
                defaults_block.get("median_high_priority_density"), None
            ),
            signature=_str_or(payload.get("signature"), None),
        )


#: The fallback used when no fetch and no cache are available.
#: Baseline values match what the analyzer hard-codes today, so this
#: is observably equivalent to current behavior on a fresh install
#: with no network. The cohort can ONLY make the analyzer smarter
#: from here, never less.
BAKED_IN_DEFAULTS: Final[TunedDefaults] = TunedDefaults(
    version=0,
    cohort_size=0,
    median_outcomes_to_graduation={
        "narrow": 250,
        "medium": 500,
        "high": 1000,
    },
    suggested_min_outcomes={
        "narrow": 250,
        "medium": 500,
        "high": 1000,
    },
)


def fetch_tuned_defaults(
    *,
    url: str | None = None,
    timeout: float = FETCH_TIMEOUT_SECONDS,
) -> TunedDefaults | None:
    """Fetch the latest tuned-defaults JSON. Return None on any failure.

    ``url`` defaults to :func:`get_tuned_defaults_url`, which honors
    the ``POSTRULE_INSIGHTS_URL`` environment variable. Pass an explicit
    URL to force a specific endpoint (testing, staging mirror).

    Caller should fall back to ``load_cached_or_baked_in()`` on None.
    """
    target_url = url if url is not None else get_tuned_defaults_url()
    try:
        req = urllib.request.Request(
            target_url,
            headers={"User-Agent": "postrule-insights/1.0 (+https://postrule.ai)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — HTTPS
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        _log.debug("tuned-defaults fetch failed: %s", e)
        return None
    return TunedDefaults.from_payload(payload)


def refresh_if_stale(*, timeout: float = FETCH_TIMEOUT_SECONDS) -> TunedDefaults | None:
    """Fetch + cache when the local cache is missing or older than fresh-window.

    Synchronous; caller decides whether to wrap in a thread. Returns the
    newly-fetched defaults on success, ``None`` on failure or fresh-cache
    skip. Callers can ignore the return value — the side effect (cache
    write) is what matters for subsequent ``load_cached_or_baked_in()``
    calls in the same process.
    """
    if cache_is_fresh():
        return None
    fetched = fetch_tuned_defaults(timeout=timeout)
    if fetched is None:
        return None
    write_cache(fetched)
    return fetched


def refresh_if_stale_async(*, timeout: float = FETCH_TIMEOUT_SECONDS):
    """Spawn a daemon thread to run :func:`refresh_if_stale`.

    Non-blocking. The CLI can call this on every invocation; if the
    cache is fresh the thread no-ops in microseconds. Returns the
    started ``threading.Thread`` so callers can join with a timeout
    if they want to wait for completion.
    """
    import threading

    thread = threading.Thread(
        target=refresh_if_stale,
        kwargs={"timeout": timeout},
        name="postrule-insights-defaults-refresh",
        daemon=True,
    )
    thread.start()
    return thread


def write_cache(defaults: TunedDefaults) -> None:
    """Persist the tuned defaults to ``~/.postrule/tuned-defaults.json``.

    Best-effort; cache failures are not surfaced to the caller.
    """
    try:
        ensure_postrule_home()
        path = tuned_defaults_cache_path()
        # Round-trip through the same JSON shape we accept on read so the
        # cached file can be inspected with ``jq`` and re-loaded by the
        # next CLI invocation.
        payload = {
            "version": defaults.version,
            "generated_at": defaults.generated_at,
            "cohort_size": defaults.cohort_size,
            "defaults": {
                "median_outcomes_to_graduation": defaults.median_outcomes_to_graduation,
                "suggested_min_outcomes": defaults.suggested_min_outcomes,
                "suggested_alpha": defaults.suggested_alpha,
                "pattern_frequencies": defaults.pattern_frequencies,
                "top_refusal_categories": defaults.top_refusal_categories,
            },
            "signature": defaults.signature,
            "_cached_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        _log.debug("tuned-defaults cache write failed: %s", e)


def read_cache() -> TunedDefaults | None:
    """Read the cached tuned defaults; return None if missing or corrupt."""
    path = tuned_defaults_cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return TunedDefaults.from_payload(payload)


def cache_is_fresh(path: Path | None = None) -> bool:
    """Return True if the cache file is younger than CACHE_FRESHNESS_HOURS."""
    p = path or tuned_defaults_cache_path()
    if not p.exists():
        return False
    try:
        age = _dt.datetime.now(_dt.timezone.utc).timestamp() - p.stat().st_mtime
    except OSError:
        return False
    return age < CACHE_FRESHNESS_HOURS * 3600


def load_cached_or_baked_in() -> TunedDefaults:
    """Single entry point: return whatever defaults are available now.

    Order of preference:
      1. Fresh cache (younger than CACHE_FRESHNESS_HOURS).
      2. Stale cache (any age, better than baked-in if cohort exists).
      3. Baked-in defaults (always succeeds).

    This function never fetches over the network. Use
    :func:`fetch_tuned_defaults` separately and write the result to
    cache via :func:`write_cache` to refresh.
    """
    cached = read_cache()
    if cached is not None:
        return cached
    return BAKED_IN_DEFAULTS


# ---------------------------------------------------------------------------
# Parsing helpers — tolerant; unknown / malformed values fall through.
# ---------------------------------------------------------------------------


def _int_or(value: Any, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _float_or(value: Any, fallback: float | None) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return fallback


def _str_or(value: Any, fallback: str | None) -> str | None:
    return value if isinstance(value, str) else fallback


def _dict_int_or(value: Any, fallback: dict[str, int]) -> dict[str, int]:
    if not isinstance(value, dict):
        return fallback
    out: dict[str, int] = {}
    for k, v in value.items():
        if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool):
            out[k] = v
    return out


def _dict_float_or(value: Any, fallback: dict[str, float]) -> dict[str, float]:
    if not isinstance(value, dict):
        return fallback
    out: dict[str, float] = {}
    for k, v in value.items():
        if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = float(v)
    return out


def _list_str_or(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return fallback
    return [v for v in value if isinstance(v, str)]
