# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Filesystem paths shared across the insights package.

All insights state lives under ``~/.postrule/``. The directory is created
lazily on first write; reads against missing files return safe defaults
(no enrollment, empty queue, no cached defaults).
"""

from __future__ import annotations

import os
from pathlib import Path


def postrule_home() -> Path:
    """Return the postrule state directory.

    Honors ``$POSTRULE_HOME`` for tests and CI. Defaults to ``~/.postrule``.
    """
    override = os.environ.get("POSTRULE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".postrule"


def enrollment_path() -> Path:
    return postrule_home() / "insights-enroll"


def queue_path() -> Path:
    return postrule_home() / "insights-queue.jsonl"


def tuned_defaults_cache_path() -> Path:
    return postrule_home() / "tuned-defaults.json"


def ensure_postrule_home() -> Path:
    """Create ``~/.postrule/`` if missing. Safe to call repeatedly."""
    home = postrule_home()
    home.mkdir(parents=True, exist_ok=True)
    return home
