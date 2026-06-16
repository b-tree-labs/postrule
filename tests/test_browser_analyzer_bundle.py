# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0
#
# Drift guard: the committed browser analyzer bundle
# (landing/wasm/postrule_analyzer.py) must match what
# scripts/build_browser_analyzer.py generates from the current
# src/postrule/analyzer.py. Without this, the paste-analyzer gizmo silently
# ships a stale analyzer (it went ~3 weeks + several feature commits behind).

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_browser_analyzer", ROOT / "scripts" / "build_browser_analyzer.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_browser_bundle_is_not_stale():
    bba = _load_builder()
    expected = bba.render()
    actual = bba.OUTPUT.read_text(encoding="utf-8")
    assert actual == expected, (
        "landing/wasm/postrule_analyzer.py is out of date. Regenerate with "
        "`python scripts/build_browser_analyzer.py` and commit the result."
    )
