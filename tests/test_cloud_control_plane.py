# Copyright (c) 2026 B-Tree Labs
# SPDX-License-Identifier: LicenseRef-BSL-1.1

"""Control-plane tenant provisioning (#161 slice 1).

Idempotently provisions a schema-per-tenant + a role scoped to ONLY that schema,
turning the manual dogfood steps into one correct call. PG-backed tests skip
without $POSTRULE_TEST_PG_DSN (and a role with CREATEROLE); the pure naming/DSN
helpers always run.
"""

from __future__ import annotations

import os

import pytest

from postrule.cloud.control_plane import (
    build_tenant_dsn,
    deprovision_tenant,
    provision_tenant,
    tenant_role_name,
    tenant_schema_name,
)

DSN = os.environ.get("POSTRULE_TEST_PG_DSN")


# -- pure helpers (no PG) ----------------------------------------------------
def test_schema_name_deterministic_and_valid():
    a = tenant_schema_name("E19FACC7-212dc", "prod")
    b = tenant_schema_name("E19FACC7-212dc", "prod")
    assert a == b
    assert a.replace("_", "").isalnum() and a[0].isalpha()  # valid pg identifier


def test_schema_name_separates_environments():
    assert tenant_schema_name("acct1", "prod") != tenant_schema_name("acct1", "staging")
    assert tenant_schema_name("acct1", "prod") != tenant_schema_name("acct2", "prod")


def test_role_name_derived_from_schema():
    s = tenant_schema_name("acct1", "prod")
    assert tenant_role_name(s).startswith(s)


def test_build_tenant_dsn_socket_and_tcp():
    socket_dsn = build_tenant_dsn(
        role="r",
        password="p",
        database="postrule_control",
        connection_name="proj:us-central1:inst",
    )
    assert "host=/cloudsql/proj:us-central1:inst" in socket_dsn and "r:p@" in socket_dsn
    tcp_dsn = build_tenant_dsn(
        role="r",
        password="p",
        database="postrule_control",
        host="127.0.0.1",
        port=5433,
    )
    assert "127.0.0.1:5433" in tcp_dsn


# -- PG-backed provisioning --------------------------------------------------
def _can_create_roles() -> bool:
    if not DSN:
        return False
    try:
        import psycopg2

        c = psycopg2.connect(DSN, connect_timeout=3)
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute(
                "SELECT rolcreaterole OR rolsuper FROM pg_roles WHERE rolname = current_user"
            )
            ok = bool(cur.fetchone()[0])
        c.close()
        return ok
    except Exception:
        return False


pg = pytest.mark.skipif(not _can_create_roles(), reason="no Postgres with CREATEROLE")


@pg
def test_provision_then_storage_works_and_is_isolated():
    from postrule import PostgresStorage

    acct, env = f"itacct{os.getpid()}", "prod"
    try:
        cred = provision_tenant(DSN, acct, env)
        assert cred.schema == tenant_schema_name(acct, env)

        scoped = build_tenant_dsn(
            role=cred.role,
            password=cred.password,
            database=cred.database,
            dsn=DSN,
        )
        s = PostgresStorage(scoped, schema=cred.schema)
        s.put_state("sw", "k", b"v")
        assert s.get_state("sw", "k") == b"v"

        # isolation: scoped role cannot reach another schema
        import psycopg2

        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            PostgresStorage(scoped, schema="some_other_tenant")
    finally:
        deprovision_tenant(DSN, acct, env)


@pg
def test_provision_is_idempotent():
    acct, env = f"idem{os.getpid()}", "staging"
    try:
        c1 = provision_tenant(DSN, acct, env)
        c2 = provision_tenant(DSN, acct, env)  # must not raise
        assert c1.schema == c2.schema and c1.role == c2.role
    finally:
        deprovision_tenant(DSN, acct, env)


@pg
def test_deprovision_removes_schema_and_role():
    import psycopg2

    acct, env = f"deprov{os.getpid()}", "prod"
    cred = provision_tenant(DSN, acct, env)
    deprovision_tenant(DSN, acct, env)
    c = psycopg2.connect(DSN)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (cred.schema,)
        )
        assert cur.fetchone() is None
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (cred.role,))
        assert cur.fetchone() is None
    c.close()
