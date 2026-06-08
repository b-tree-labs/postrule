# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""Postgres-backed :class:`~postrule.storage.Storage` — durable, shared state.

The built-in FileStorage/SqliteStorage are per-process/per-host. On a stateless,
multi-replica deployment (e.g. Cloud Run) that means switch state is lost on
every restart and diverges across replicas, so nothing ever graduates.
``PostgresStorage`` keeps the per-switch verdict log and state KV in one
Postgres, so state **persists across deploys and is shared across replicas**.

Two tables under ``schema`` (created idempotently on construction):

- ``postrule_records(id BIGSERIAL, switch_name, line)`` — the append-only verdict
  log; ``line`` is the same JSON serialization FileStorage uses.
- ``postrule_state(switch_name, key, blob, PK(switch_name,key))`` — the head /
  breaker / signature / ledger blobs.

Per-method writes are atomic (append = one INSERT; ``put_state`` = an
``ON CONFLICT`` upsert), so concurrent replicas never corrupt each other.
Coordinating phase *transitions* across replicas (e.g. two replicas calling
``advance`` at once) is a switch-level concern and is **not** handled here —
run advancement from a single writer, or rely on the cloud failure-detector to
surface divergence.

Optional dependency: requires ``psycopg2`` — ``pip install postrule[postgres]``.
Importing this module never imports psycopg2; only constructing the class does.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from postrule.storage import StorageBase, deserialize_record, serialize_record

if TYPE_CHECKING:
    from postrule.core import ClassificationRecord

_MISSING_DRIVER = (
    "PostgresStorage requires psycopg2 — install it with "
    "`pip install postrule[postgres]` (or `pip install psycopg2-binary`)."
)


class PostgresStorage(StorageBase):
    """Durable, replica-shared :class:`Storage` backed by Postgres.

    Args:
        dsn: libpq connection string, e.g. ``postgresql://user@host:5432/db``.
        schema: Postgres schema to hold the two tables (created if absent).
            Useful for isolating environments/tenants. Default ``"public"``.
    """

    def __init__(self, dsn: str, *, schema: str = "public") -> None:
        try:
            import psycopg2  # noqa: F401
        except ImportError as e:  # pragma: no cover - exercised when driver absent
            raise ImportError(_MISSING_DRIVER) from e
        # `schema` is interpolated into DDL (identifiers can't be parameterized),
        # so constrain it hard. switch_name/key/blob are always parameterized.
        if not schema or not schema.replace("_", "").isalnum():
            raise ValueError(f"invalid schema name: {schema!r}")
        self._dsn = dsn
        self._schema = schema
        self._records = f'"{schema}".postrule_records'
        self._state = f'"{schema}".postrule_state'
        self._lock = threading.Lock()
        self._conn = None
        self._ensure_schema()

    # -- connection -------------------------------------------------------
    def _connection(self):
        """A lazily-(re)opened autocommit connection. Caller holds ``_lock``."""
        if self._conn is None or self._conn.closed:
            import psycopg2

            self._conn = psycopg2.connect(self._dsn)
            self._conn.autocommit = True
        return self._conn

    def _execute(self, sql: str, params: tuple = ()):
        with self._lock:
            try:
                cur = self._connection().cursor()
            except Exception:
                self._conn = None  # force reconnect next call
                raise
            with cur:
                cur.execute(sql, params)
                return cur.fetchall() if cur.description is not None else None

    def _ensure_schema(self) -> None:
        self._execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
        self._execute(
            f"CREATE TABLE IF NOT EXISTS {self._records} "
            "(id BIGSERIAL PRIMARY KEY, switch_name TEXT NOT NULL, line TEXT NOT NULL)"
        )
        self._execute(
            f"CREATE INDEX IF NOT EXISTS postrule_records_switch_id "
            f"ON {self._records} (switch_name, id)"
        )
        self._execute(
            f"CREATE TABLE IF NOT EXISTS {self._state} "
            "(switch_name TEXT NOT NULL, key TEXT NOT NULL, blob BYTEA NOT NULL, "
            "PRIMARY KEY (switch_name, key))"
        )

    # -- Storage interface ------------------------------------------------
    def append_record(self, switch_name: str, record: ClassificationRecord) -> None:
        self._execute(
            f"INSERT INTO {self._records} (switch_name, line) VALUES (%s, %s)",
            (switch_name, serialize_record(record)),
        )

    def load_records(self, switch_name: str) -> list[ClassificationRecord]:
        rows = self._execute(
            f"SELECT line FROM {self._records} WHERE switch_name = %s ORDER BY id",
            (switch_name,),
        )
        return [deserialize_record(r[0]) for r in (rows or [])]

    def put_state(self, switch_name: str, key: str, blob: bytes) -> None:
        self._execute(
            f"INSERT INTO {self._state} (switch_name, key, blob) VALUES (%s, %s, %s) "
            "ON CONFLICT (switch_name, key) DO UPDATE SET blob = EXCLUDED.blob",
            (switch_name, key, bytes(blob)),
        )

    def get_state(self, switch_name: str, key: str) -> bytes | None:
        rows = self._execute(
            f"SELECT blob FROM {self._state} WHERE switch_name = %s AND key = %s",
            (switch_name, key),
        )
        if not rows:
            return None
        return bytes(rows[0][0])

    def delete_state(self, switch_name: str, key: str) -> None:
        self._execute(
            f"DELETE FROM {self._state} WHERE switch_name = %s AND key = %s",
            (switch_name, key),
        )
