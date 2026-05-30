# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1
"""
Project-slug derivation for the SDK side of #107.

A "project" names *where a switch lives* — the per-codebase grouping
unit one level above an individual switch (Account → Project → Switch).
This module decides what project slug a freshly-decorated `@ml_switch`
belongs to, without forcing the operator to spell it out 95% of the
time.

Priority chain when ``@ml_switch(project=...)`` is not supplied:

  1. ``git remote get-url origin`` → ``<owner>/<repo>`` slug.
     Covers the common case (a checkout with a known remote). Both HTTPS
     and SSH forms are accepted; we take the last two path segments to
     normalize across hosts that expose group hierarchy (GitLab, Gitea).

  2. ``pyproject.toml [project] name`` → the project name field.
     Covers pre-push local development before a remote has been added.

  3. literal ``"default"`` → so no switch ever silently disappears from
     a future per-project dashboard view.

**Privacy boundary.** Project slugs derived from a remote URL identify
the operator's codebase. They live inside the account-scoped event
surface only — the cohort-shared corpus already strips ``repo_url`` and
similar identifying keys (see ``cloud/registry.py::_IDENTIFYING_KEYS``).
A follow-on PR will add ``project`` to that allowlist when the verdict
wire payload starts carrying it; for now this module is SDK-internal.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

__all__ = [
    "derive_project_slug",
    "project_from_git_remote",
    "project_from_pyproject",
]


# https://github.com/owner/repo(.git)?  ssh://git@host/owner/repo(.git)?
# git@host:owner/repo(.git)?
_REMOTE_PATH_RE = re.compile(
    r"(?:[A-Za-z]+@[^/:]+[:/]|[A-Za-z]+://[^/]+/)(?P<path>[^?#]+?)(?:\.git)?$"
)


def project_from_git_remote(start: Path | None = None) -> str | None:
    """Return the ``<owner>/<repo>`` slug from ``origin``, or None.

    Quietly returns None for any failure (not a repo, no ``origin``,
    git missing) so the priority chain in :func:`derive_project_slug`
    can fall through without surfacing subprocess plumbing to the
    operator's code.
    """
    if start is None:
        start = Path.cwd()
    try:
        proc = subprocess.run(
            ["git", "-C", str(start), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    url = proc.stdout.strip()
    if not url:
        return None
    m = _REMOTE_PATH_RE.search(url)
    if not m:
        return None
    parts = [p for p in m.group("path").split("/") if p]
    if len(parts) < 2:
        return None
    # Take the last two segments — handles plain ``owner/repo`` and
    # GitLab-style ``group/subgroup/repo`` (we use ``subgroup/repo``
    # because the deepest scope is the most stable identifier of "the
    # codebase").
    return f"{parts[-2]}/{parts[-1]}"


def project_from_pyproject(start: Path | None = None) -> str | None:
    """Return the ``[project] name`` from ``pyproject.toml``, or None."""
    if start is None:
        start = Path.cwd()
    pyproject = start / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        # tomllib is stdlib on 3.11+; we support 3.10 too so try both.
        if sys.version_info >= (3, 11):
            import tomllib  # type: ignore[import-not-found]

            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        else:  # pragma: no cover - 3.10 path
            import tomli  # type: ignore[import-not-found]

            data = tomli.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        # Any parse failure — quietly fall through. We must NEVER crash
        # user code at decorator import time.
        return None
    name = data.get("project", {}).get("name") if isinstance(data, dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def derive_project_slug(*, start: Path | None = None) -> str:
    """Pick the project slug for a switch via the documented priority chain.

    Always returns a non-empty string. Falls back to ``"default"`` when
    nothing in the environment identifies the codebase, so a future
    per-project dashboard view never has switches with no home.
    """
    if start is None:
        start = Path.cwd()
    return project_from_git_remote(start) or project_from_pyproject(start) or "default"
