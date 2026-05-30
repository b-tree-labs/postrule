# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
TLS posture for the postrule.ai landing site.

Cloudflare Pages serves `landing/` with the response headers declared in
`landing/_headers` (path-pattern blocks, indented header lines per the
Pages convention). The dashboard Worker middleware stamps HSTS on every
response from `app.postrule.ai`, but Pages doesn't add HSTS unless the
operator does it explicitly — so a browser whose first contact with the
zone is the apex `postrule.ai` never receives the HSTS pin, and a
hypothetical first-visit downgrade attack has a window. Pin the fix
here.

Contract:
  - `landing/_headers` exists.
  - The `/*` block contains a `Strict-Transport-Security` header.
  - max-age is at least 6 months (matching the dashboard middleware).
  - `includeSubDomains` is set so the pin covers app.postrule.ai +
    api.postrule.ai under the same zone.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HEADERS_FILE = REPO_ROOT / "landing" / "_headers"

# 6 months ≈ 15552000 seconds — matches `cloud/dashboard/lib/security-headers.ts`.
MIN_HSTS_MAX_AGE = 15_552_000


def _wildcard_block() -> str:
    """Return the indented headers under the `/*` pattern."""
    text = HEADERS_FILE.read_text(encoding="utf-8")
    # Path patterns start at column 0; their headers are indented. Slice
    # from the `/*` line to the next column-0 line.
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "/*")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        s = lines[j]
        if s and not s[0].isspace() and not s.startswith("#"):
            end = j
            break
    return "\n".join(lines[start + 1 : end])


class TestLandingHsts:
    def test_headers_file_exists(self) -> None:
        assert HEADERS_FILE.is_file(), f"missing {HEADERS_FILE}"

    def test_wildcard_block_includes_hsts(self) -> None:
        block = _wildcard_block()
        assert re.search(
            r"^\s*Strict-Transport-Security:",
            block,
            flags=re.MULTILINE | re.IGNORECASE,
        ), "no Strict-Transport-Security header in `/*` block"

    def test_hsts_max_age_is_at_least_six_months(self) -> None:
        block = _wildcard_block()
        m = re.search(
            r"Strict-Transport-Security:.*?max-age=(\d+)",
            block,
            flags=re.IGNORECASE,
        )
        assert m, "max-age missing from HSTS header"
        assert int(m.group(1)) >= MIN_HSTS_MAX_AGE, (
            f"HSTS max-age too short ({m.group(1)}); want >= "
            f"{MIN_HSTS_MAX_AGE} (6 months) to match the dashboard's posture"
        )

    def test_hsts_covers_subdomains(self) -> None:
        # includeSubDomains is what propagates the pin to app.postrule.ai
        # + api.postrule.ai once any one of them is visited under HTTPS.
        block = _wildcard_block()
        m = re.search(r"Strict-Transport-Security:[^\n]+", block, flags=re.IGNORECASE)
        assert m, "no HSTS header line found"
        assert "includeSubDomains" in m.group(0), (
            "HSTS missing includeSubDomains; without it the pin only covers "
            "the exact origin, not app.postrule.ai / api.postrule.ai under "
            "the same zone"
        )
