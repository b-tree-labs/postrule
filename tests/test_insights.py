# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Tests for the dendra.insights package — Phase A pre-launch wire."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from dendra.insights import (
    BAKED_IN_DEFAULTS,
    EnrollmentState,
    InsightsEvent,
    TunedDefaults,
    fetch_tuned_defaults,
    flush_queue,
    is_enrolled,
    load_cached_or_baked_in,
    queue_event,
    read_enrollment,
    read_queue,
    write_enrollment,
    write_unenrollment,
)
from dendra.insights._paths import (
    enrollment_path,
    queue_path,
    tuned_defaults_cache_path,
)
from dendra.insights.tuned_defaults import (
    TUNED_DEFAULTS_URL,
    cache_is_fresh,
    read_cache,
    write_cache,
)


@pytest.fixture(autouse=True)
def _isolated_dendra_home(tmp_path, monkeypatch):
    """Point all insights state at a tmp_path so tests don't touch ~/.dendra/."""
    monkeypatch.setenv("DENDRA_HOME", str(tmp_path))
    yield


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------


class TestEnrollment:
    def test_default_state_is_not_enrolled(self):
        assert is_enrolled() is False
        state = read_enrollment()
        assert state.enrolled is False
        assert state.enrolled_at is None

    def test_write_enrollment_creates_flag(self):
        write_enrollment(consent_text_sha256="abc123")
        assert is_enrolled() is True
        state = read_enrollment()
        assert state.enrolled is True
        assert state.enrolled_at is not None
        assert state.consent_text_sha256 == "abc123"
        assert state.schema_version == 1

    def test_write_unenrollment_removes_flag(self):
        write_enrollment()
        assert is_enrolled() is True
        write_unenrollment()
        assert is_enrolled() is False

    def test_unenroll_when_not_enrolled_is_idempotent(self):
        write_unenrollment()
        write_unenrollment()
        assert is_enrolled() is False

    def test_corrupt_enrollment_file_is_treated_as_not_enrolled(self):
        enrollment_path().parent.mkdir(parents=True, exist_ok=True)
        enrollment_path().write_text("{ this is not json", encoding="utf-8")
        # Fail closed: corrupt file MUST NOT be treated as enrolled.
        assert is_enrolled() is False

    def test_empty_enrollment_file_is_treated_as_not_enrolled(self):
        enrollment_path().parent.mkdir(parents=True, exist_ok=True)
        enrollment_path().write_text("", encoding="utf-8")
        assert is_enrolled() is False

    def test_account_hash_and_consent_round_trip(self):
        write_enrollment(account_hash="hash-of-email", consent_text_sha256="sha-of-copy")
        state = read_enrollment()
        assert state.account_hash == "hash-of-email"
        assert state.consent_text_sha256 == "sha-of-copy"


# ---------------------------------------------------------------------------
# Tuned defaults
# ---------------------------------------------------------------------------


class TestTunedDefaults:
    def test_baked_in_defaults_have_safe_values(self):
        # Baked-in must always be usable — no None, no empty regimes.
        d = BAKED_IN_DEFAULTS
        assert d.cohort_size == 0
        assert d.median_outcomes_to_graduation["narrow"] >= 1
        assert d.suggested_min_outcomes["narrow"] >= 1

    def test_load_cached_or_baked_in_returns_baked_in_when_no_cache(self):
        d = load_cached_or_baked_in()
        assert d is BAKED_IN_DEFAULTS

    def test_load_cached_returns_cache_when_present(self):
        custom = TunedDefaults(
            version=42,
            cohort_size=17,
            median_outcomes_to_graduation={"narrow": 220},
        )
        write_cache(custom)
        loaded = load_cached_or_baked_in()
        assert loaded.version == 42
        assert loaded.cohort_size == 17
        assert loaded.median_outcomes_to_graduation["narrow"] == 220

    def test_corrupt_cache_falls_back_to_baked_in(self):
        cache_path = tuned_defaults_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("{ corrupt", encoding="utf-8")
        loaded = load_cached_or_baked_in()
        assert loaded is BAKED_IN_DEFAULTS

    def test_from_payload_tolerates_unknown_keys(self):
        payload = {
            "version": 5,
            "cohort_size": 3,
            "future_field_we_dont_know": [1, 2, 3],
            "defaults": {
                "median_outcomes_to_graduation": {"narrow": 200},
                "another_unknown": "ignored",
            },
        }
        d = TunedDefaults.from_payload(payload)
        assert d.version == 5
        assert d.cohort_size == 3
        assert d.median_outcomes_to_graduation == {"narrow": 200}

    def test_from_payload_tolerates_type_mismatch(self):
        # version comes as a string instead of int — should fall back.
        payload = {
            "version": "not-an-int",
            "cohort_size": "also-not-an-int",
        }
        d = TunedDefaults.from_payload(payload)
        assert d.version == 0
        assert d.cohort_size == 0

    def test_from_payload_handles_non_dict_input(self):
        d = TunedDefaults.from_payload("not a dict at all")
        assert d is BAKED_IN_DEFAULTS

    def test_signature_field_preserved_for_phase_b(self):
        # Phase A doesn't verify signatures, but the field round-trips.
        payload = {
            "version": 1,
            "signature": "ed25519:abc123def",
        }
        d = TunedDefaults.from_payload(payload)
        assert d.signature == "ed25519:abc123def"

    def test_fetch_tuned_defaults_handles_network_error(self):
        # urllib raises URLError on network failure — must return None.
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            result = fetch_tuned_defaults(url="https://example.invalid/foo.json")
        assert result is None

    def test_fetch_tuned_defaults_parses_valid_payload(self):
        payload = {
            "version": 7,
            "cohort_size": 12,
            "defaults": {"median_outcomes_to_graduation": {"narrow": 200}},
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            d = fetch_tuned_defaults()
        assert d is not None
        assert d.version == 7
        assert d.cohort_size == 12
        assert d.median_outcomes_to_graduation["narrow"] == 200

    def test_cache_freshness(self):
        custom = TunedDefaults(version=1, cohort_size=1)
        write_cache(custom)
        # Just-written cache is fresh.
        assert cache_is_fresh() is True
        # Stat the file backwards 48h to simulate stale cache.
        path = tuned_defaults_cache_path()
        old_time = path.stat().st_mtime - (48 * 3600)
        os.utime(path, (old_time, old_time))
        assert cache_is_fresh() is False

    def test_default_url_is_dendra_run(self):
        # Pin the canonical URL so a typo on launch day doesn't slip.
        assert TUNED_DEFAULTS_URL == "https://dendra.run/insights/tuned-defaults.json"

    def test_get_tuned_defaults_url_honors_env_override(self, monkeypatch):
        from dendra.insights.tuned_defaults import (
            DEFAULT_TUNED_DEFAULTS_URL,
            get_tuned_defaults_url,
        )

        monkeypatch.delenv("DENDRA_INSIGHTS_URL", raising=False)
        assert get_tuned_defaults_url() == DEFAULT_TUNED_DEFAULTS_URL

        monkeypatch.setenv("DENDRA_INSIGHTS_URL", "https://staging.example/foo.json")
        assert get_tuned_defaults_url() == "https://staging.example/foo.json"

    def test_fetch_uses_env_override_url_when_passed_none(self, monkeypatch):
        from dendra.insights.tuned_defaults import fetch_tuned_defaults

        monkeypatch.setenv("DENDRA_INSIGHTS_URL", "https://override.example/x.json")
        captured: dict[str, str] = {}

        def fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"version": 1}'
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            d = fetch_tuned_defaults()  # url=None falls back to env
        assert captured["url"] == "https://override.example/x.json"
        assert d is not None
        assert d.version == 1

    def test_refresh_if_stale_writes_cache_on_success(self):
        from dendra.insights.tuned_defaults import refresh_if_stale

        payload = {
            "version": 99,
            "cohort_size": 7,
            "defaults": {"median_outcomes_to_graduation": {"narrow": 222}},
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            refreshed = refresh_if_stale()
        assert refreshed is not None
        assert refreshed.version == 99
        # Cache reflects the fetched values.
        from dendra.insights import load_cached_or_baked_in

        cached = load_cached_or_baked_in()
        assert cached.version == 99
        assert cached.cohort_size == 7
        assert cached.median_outcomes_to_graduation["narrow"] == 222

    def test_refresh_if_stale_skips_when_cache_is_fresh(self):
        from dendra.insights.tuned_defaults import refresh_if_stale, write_cache

        # Pre-warm a fresh cache.
        write_cache(TunedDefaults(version=42, cohort_size=3))
        # Fetch must NOT be called when cache is fresh.
        with patch("urllib.request.urlopen", side_effect=AssertionError("fetch must not be called")):
            result = refresh_if_stale()
        assert result is None  # signal: skipped, not fetched

    def test_refresh_if_stale_handles_fetch_failure_silently(self):
        import urllib.error

        from dendra.insights.tuned_defaults import refresh_if_stale

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            result = refresh_if_stale()
        assert result is None  # silent; no exception, no cache write


# ---------------------------------------------------------------------------
# Event queue
# ---------------------------------------------------------------------------


class TestEventQueue:
    def test_empty_queue_reads_as_empty_list(self):
        assert read_queue() == []

    def test_queue_event_appends_and_reads_back(self):
        ev = queue_event(
            "analyze",
            payload={
                "files_scanned": 100,
                "total_sites": 12,
                "already_dendrified_count": 3,
                "pattern_histogram": {"P1": 8, "P4": 4},
                "regime_histogram": {"narrow": 10, "medium": 2},
                "lift_status_histogram": {"auto_liftable": 7, "refused": 5},
                "hazard_category_histogram": {},
            },
        )
        assert isinstance(ev, InsightsEvent)
        assert ev.event_type == "analyze"
        events = read_queue()
        assert len(events) == 1
        assert events[0].event_type == "analyze"
        assert events[0].payload["files_scanned"] == 100
        assert events[0].payload["pattern_histogram"]["P1"] == 8

    def test_unknown_event_type_is_rejected(self):
        result = queue_event("totally_made_up", payload={"foo": "bar"})
        assert result is None
        assert read_queue() == []

    def test_payload_unknown_keys_are_stripped(self):
        # Privacy: unknown keys MUST NOT pass through to the queue.
        # If a future schema change adds a field, this test must be
        # updated alongside the whitelist — that's the intentional
        # friction.
        ev = queue_event(
            "analyze",
            payload={
                "files_scanned": 1,
                "total_sites": 1,
                "already_dendrified_count": 0,
                "pattern_histogram": {"P1": 1},
                "regime_histogram": {"narrow": 1},
                "lift_status_histogram": {"auto_liftable": 1},
                "hazard_category_histogram": {},
                # Unknown keys that should be dropped:
                "function_name": "secret_business_logic",
                "labels": ["internal_label_a", "internal_label_b"],
                "source_code": "def thing(): return 'leaked'",
                "pattern": "P1",  # per-site key — not in analyze schema anymore
                "priority_score": 5.0,  # also per-site
            },
        )
        assert ev is not None
        # Every allowed key present, every unknown / per-site key absent.
        assert "files_scanned" in ev.payload
        assert "pattern_histogram" in ev.payload
        assert "function_name" not in ev.payload
        assert "labels" not in ev.payload
        assert "source_code" not in ev.payload
        assert "pattern" not in ev.payload  # moved to init_attempt
        assert "priority_score" not in ev.payload

    def test_queue_corrupt_lines_are_skipped(self):
        # Pre-seed a queue file with one good line + one corrupt line.
        path = queue_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "event_type": "analyze",
                    "timestamp": "2026-04-29T00:00:00+00:00",
                    "schema_version": 1,
                    "site_fingerprint": None,
                    "payload": {"pattern": "P1"},
                }
            )
            + "\n"
            + "{ this line is not valid json\n",
            encoding="utf-8",
        )
        events = read_queue()
        assert len(events) == 1
        assert events[0].event_type == "analyze"

    def test_flush_succeeds_clears_queue(self):
        queue_event("analyze", payload={"pattern": "P1"})
        queue_event("analyze", payload={"pattern": "P2"})
        # Mock a successful HTTP 200.
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            flushed = flush_queue()
        assert flushed == 2
        assert read_queue() == []

    def test_flush_failure_leaves_queue_intact(self):
        queue_event("analyze", payload={"pattern": "P1"})
        # Network failure: queue must persist for next try.
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            flushed = flush_queue()
        assert flushed == 0
        assert len(read_queue()) == 1

    def test_flush_5xx_leaves_queue_intact(self):
        queue_event("analyze", payload={"pattern": "P1"})
        mock_resp = MagicMock()
        mock_resp.status = 502
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            flushed = flush_queue()
        assert flushed == 0
        assert len(read_queue()) == 1

    def test_flush_batch_size_partial_drain(self):
        for i in range(5):
            queue_event("analyze", payload={"pattern": "P1"})
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            flushed = flush_queue(batch_size=3)
        assert flushed == 3
        # Two events remain in the queue for the next flush.
        assert len(read_queue()) == 2
