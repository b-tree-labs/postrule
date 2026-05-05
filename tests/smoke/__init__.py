# Copyright (c) 2026 B-Tree Ventures, LLC
# SPDX-License-Identifier: Apache-2.0

"""Smoke tests — pingable, low-cost verification of running deployments.

Marked ``@pytest.mark.smoke`` so the default test run skips them. Run
explicitly with ``pytest -m smoke`` after deploys, or add
``--smoke-target=production`` to point at a specific environment.

Three target environments, controlled by ``DENDRA_SMOKE_BASE_URL``:

  - http://localhost:8765 (dev / pytest fixture-served)
  - https://staging.dendra.run (post-merge-to-main verification)
  - https://dendra.run (post-tag-release verification)

Smoke tests against production are READ-ONLY: HEAD requests, GET
healthchecks, JSON parse + schema sniff. POST /v1/events is exercised
only against staging (synthetic events into the staging warehouse).
"""
