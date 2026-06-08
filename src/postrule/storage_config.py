# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Config-driven state-storage resolution.

Lets operators choose where switch state lives via configuration instead of
code — the SDK side of a cloud-console ``state_storage`` setting. A switch
constructed without an explicit ``storage=`` consults this resolver first.

Resolved from environment (works on any host, no network needed):

- ``POSTRULE_STATE_BACKEND`` — ``memory`` | ``file`` | ``postgres`` (unset ⇒ the
  caller keeps its own default).
- ``POSTRULE_STATE_DSN`` — libpq DSN for ``postgres``.
- ``POSTRULE_STATE_SCHEMA`` — Postgres schema (default ``public``).
- ``POSTRULE_STATE_PATH`` — base dir for ``file`` (default ``runtime/postrule``).

Offline-first: a missing/unknown backend, a ``postgres`` backend with no DSN, or
a configured-but-unreachable Postgres all **fall back to the caller's local
default** (returning ``None``) with a one-time stderr note — a disconnected box
still runs; durability resumes when the DB is reachable and the process
restarts. (A cloud console can later write these as a cached account/env setting;
this module is the read/resolve seam.)
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from postrule.storage import Storage

_warned = False


def _warn(msg: str) -> None:
    global _warned
    if _warned:
        return
    _warned = True
    print(f"[postrule] state-storage: {msg}", file=sys.stderr)


def resolve_state_storage() -> Storage | None:
    """Return the configured state-storage backend, or ``None`` to defer to the
    caller's default. Never raises — see the module docstring's offline contract.
    """
    backend = os.environ.get("POSTRULE_STATE_BACKEND", "").strip().lower()
    if not backend:
        return None

    if backend in ("memory", "inmemory"):
        from postrule.storage import BoundedInMemoryStorage

        return BoundedInMemoryStorage()

    if backend == "file":
        from postrule.storage import FileStorage, ResilientStorage

        base = os.environ.get("POSTRULE_STATE_PATH", "").strip() or "runtime/postrule"
        return ResilientStorage(FileStorage(base, batching=True))

    if backend in ("postgres", "postgresql", "pg"):
        dsn = os.environ.get("POSTRULE_STATE_DSN", "").strip()
        if not dsn:
            _warn("POSTRULE_STATE_BACKEND=postgres but POSTRULE_STATE_DSN unset; local default")
            return None
        schema = os.environ.get("POSTRULE_STATE_SCHEMA", "").strip() or "public"
        try:
            from postrule.storage_postgres import PostgresStorage

            return PostgresStorage(dsn, schema=schema)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as e:  # noqa: BLE001 — offline-first: never break the app
            _warn(f"Postgres state storage unavailable ({e}); using local default")
            return None

    _warn(f"unknown POSTRULE_STATE_BACKEND={backend!r}; using local default")
    return None
