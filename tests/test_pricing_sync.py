# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# #134 — guard the single source of truth for LLM pricing.
#
# Canonical pricing lives in shared/pricing/llm-prices.json. Surfaces that
# can't import across package boundaries read committed copies (landing is
# static; the dashboard is a separate Next package). scripts/update_llm_prices.py
# writes the canonical file and syncs the copies; this test fails if any copy
# drifts from canonical, so a stale copy can never ship a wrong number.

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL = REPO_ROOT / "shared" / "pricing" / "llm-prices.json"
COPIES = [
    REPO_ROOT / "landing" / "data" / "llm-prices.json",
    REPO_ROOT / "cloud" / "dashboard" / "lib" / "llm-prices.json",
]


def test_canonical_pricing_exists() -> None:
    assert CANONICAL.is_file(), f"canonical pricing missing at {CANONICAL}"


def test_synced_copies_match_canonical() -> None:
    want = CANONICAL.read_text()
    drifted = [str(c) for c in COPIES if not c.is_file() or c.read_text() != want]
    assert not drifted, (
        "llm-prices.json copies drifted from canonical "
        f"({CANONICAL}); re-run scripts/update_llm_prices.py: {drifted}"
    )
