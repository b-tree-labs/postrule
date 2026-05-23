# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Package version must match pyproject.toml and CLI --version."""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

import postrule


def test_version_matches_pyproject() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    assert match, "pyproject.toml missing [project].version"
    assert postrule.__version__ == match.group(1)


def test_version_matches_distribution_metadata() -> None:
    assert postrule.__version__ == importlib.metadata.version("postrule")
