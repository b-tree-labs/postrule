# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: Apache-2.0

"""PostgresStorage — durable, shared switch state on Postgres (#148 G5).

The built-in FileStorage/SqliteStorage are per-process/per-host; on a stateless
multi-replica deployment (e.g. Cloud Run) switch state is lost every restart and
diverges across replicas. PostgresStorage keeps the verdict log + per-switch
state in one Postgres so state persists across deploys and is shared across
replicas. Per-method writes are atomic (upsert / append); cross-replica
coordination of phase *transitions* is a switch-level concern, out of scope here.

Skips unless a Postgres is reachable at $POSTRULE_TEST_PG_DSN (CI provides one).
"""

from __future__ import annotations

import os
import time

import pytest

from postrule import PostgresStorage
from postrule.core import ClassificationRecord

DSN = os.environ.get(
    "POSTRULE_TEST_PG_DSN",
    "postgresql://postgres:postgres@localhost:5432/postgres",  # pragma: allowlist secret
)


def _pg_reachable() -> bool:
    try:
        import psycopg2

        c = psycopg2.connect(DSN, connect_timeout=3)
        c.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_reachable(), reason="no Postgres reachable at $POSTRULE_TEST_PG_DSN"
)


@pytest.fixture
def schema():
    """A throwaway schema so the test never touches real tables."""
    import psycopg2

    name = f"pr_test_{os.getpid()}_{int(time.time() * 1000) % 1_000_000}"
    yield name
    c = psycopg2.connect(DSN)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    c.close()


def _rec(label: str = "a") -> ClassificationRecord:
    return ClassificationRecord(
        timestamp=1.0,
        input={},
        label=label,
        outcome="correct",
        source="x",
        confidence=1.0,
        rule_output=label,
        model_output=label,
        ml_output=label,
    )


def test_put_get_delete_state(schema):
    s = PostgresStorage(DSN, schema=schema)
    assert s.get_state("sw", "k") is None
    s.put_state("sw", "k", b"v1")
    assert s.get_state("sw", "k") == b"v1"
    s.delete_state("sw", "k")
    assert s.get_state("sw", "k") is None


def test_put_state_is_an_atomic_upsert(schema):
    s = PostgresStorage(DSN, schema=schema)
    s.put_state("sw", "k", b"v1")
    s.put_state("sw", "k", b"v2")
    assert s.get_state("sw", "k") == b"v2"  # one row, latest wins


def test_append_load_records_round_trip_in_order(schema):
    s = PostgresStorage(DSN, schema=schema)
    for i in range(5):
        s.append_record("sw", _rec(label=str(i)))
    recs = s.load_records("sw")
    assert [r.label for r in recs] == ["0", "1", "2", "3", "4"]
    assert all(isinstance(r, ClassificationRecord) for r in recs)


def test_state_isolated_by_switch_and_key(schema):
    s = PostgresStorage(DSN, schema=schema)
    s.put_state("sw1", "k", b"a")
    s.put_state("sw2", "k", b"b")
    s.put_state("sw1", "k2", b"c")
    assert s.get_state("sw1", "k") == b"a"
    assert s.get_state("sw2", "k") == b"b"
    assert s.get_state("sw1", "k2") == b"c"


def test_shared_across_instances(schema):
    """The keystone: a second instance (another replica) sees the first's writes."""
    s1 = PostgresStorage(DSN, schema=schema)
    s1.put_state("sw", "head", b"weights")
    s1.append_record("sw", _rec("z"))

    s2 = PostgresStorage(DSN, schema=schema)
    assert s2.get_state("sw", "head") == b"weights"
    assert [r.label for r in s2.load_records("sw")] == ["z"]
