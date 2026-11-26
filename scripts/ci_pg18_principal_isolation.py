"""Mandatory, executable PostgreSQL 18 principal-isolation acceptance.

Only disposable GitHub Actions service at 127.0.0.1:55820 is permitted.
Creates distinct owner and two restricted SCRAM LOGINs, upgrades migration
012 and runs the actual integration test cases. Never touches port 5432.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from src.api.db import migrate

ROOT = Path(__file__).resolve().parents[1]
TEST_FILE = "tests/platform/test_transport_principal_isolation_round8.py"
EXTRA_CASE = ("tests/platform/test_public_transport_policy.py"
              "::test_actual_pg_foreign_installation_scope_same_principal_is_not_authorized")
TESTS = [
    "test_pg_runtime_principal_unique_and_scope_forgery_denied",
    "test_pg_runtime_cannot_delete_enrollment_or_reset_foreign_owner",
    "test_pg_concurrent_same_login_enrollment_serialized",
    "test_pg_foreign_uuid_rejected_with_two_distinct_valid_logins",
    "test_pg_principal_revocation_denies_outstanding_leases",
    "test_pg_post_migration_role_membership_revokes_policy",
    "test_pg_post_migration_login_revocation_denies_policy",
    "test_pg_policy_halt_persists_and_dispatch_remains_fenced",
    "test_pg_private_guard_and_runtime_table_privileges",
    "test_pg_duplicate_enrollment_rolls_back_without_mutating_existing_scope",
]
USER_A = "tester_ci_runtime_a"
USER_B = "tester_ci_runtime_b"
OWNER = "tester_ci_policy_owner"
DB = "tester_ci_principal"
OWNER_SECRET = "disposable-pg18-owner-only"
A_SECRET = "disposable-pg18-runtime-a-only"
B_SECRET = "disposable-pg18-runtime-b-only"
ENDPOINT_FUNCTIONS = (
    "tester_platform.read_public_transport(uuid,text), "
    "tester_platform.reserve_public_transport(uuid,text), "
    "tester_platform.finish_public_transport(uuid,text,uuid,text), "
    "tester_platform.reserve_public_dispatch(uuid), "
    "tester_platform.finish_public_dispatch(uuid,uuid,double precision)"
)


def check_legacy_upgrades(admin_dsn, owner_dsn, app_dsn):
    """Actually run 004..011 -> 012 on disposable legacy singleton/ambiguous DBs."""
    import hashlib
    from uuid import uuid4

    migration_dir = ROOT / "src" / "api" / "migrations"
    old_files = sorted(p for p in migration_dir.glob("*.sql") if p.name < "012_")
    assert len(old_files) == 11, "Expected exact historic 001..011 migration sequence"
    for ambiguous in (False, True):
        dbname = "tester_ci_legacy_bad" if ambiguous else "tester_ci_legacy_clean"
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            assert not admin.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (dbname,)
            ).fetchone()
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(dbname), sql.Identifier(OWNER)
            ))
            admin.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(dbname)
            ))
        legacy = make_conninfo(owner_dsn, dbname=dbname)
        with psycopg.connect(legacy) as conn:
            conn.execute("CREATE SCHEMA tester_platform")
            conn.execute(
                "CREATE TABLE tester_platform.migrations("
                "version text PRIMARY KEY, checksum text NOT NULL, "
                "applied_at timestamptz DEFAULT now())"
            )
            for migration in old_files:
                body = migration.read_text(encoding="utf-8")
                conn.execute(body)
                conn.execute(
                    "INSERT INTO tester_platform.migrations(version,checksum) VALUES(%s,%s)",
                    (migration.name, hashlib.sha256(body.encode()).hexdigest()),
                )
            ids = [uuid4(), uuid4()]
            for scope in ids[:2 if ambiguous else 1]:
                conn.execute(
                    "INSERT INTO tester_platform.transport_policy_scopes"
                    "(installation_id,runtime_role,owner_role) VALUES(%s,%s,current_user)",
                    (scope, USER_A),
                )
            conn.execute(
                "UPDATE tester_platform.transport_policy_scopes "
                "SET globally_halted=true,dispatch_token=%s WHERE installation_id=%s",
                (uuid4(), ids[0]),
            )
            conn.execute(
                "INSERT INTO tester_platform.transport_policy_entries"
                "(installation_id,endpoint_key,request_token,state,reason)"
                " VALUES(%s,%s,%s,'HALTED','tls_failure')",
                (ids[0], "api.bybit.com/v5/market/kline", uuid4()),
            )
        with psycopg.connect(legacy) as conn:
            before_scope = conn.execute(
                "SELECT installation_id,runtime_role,globally_halted,dispatch_token,"
                "dispatch_not_before FROM tester_platform.transport_policy_scopes "
                "ORDER BY installation_id"
            ).fetchall()
            before_entries = conn.execute(
                "SELECT installation_id,endpoint_key,request_token,state,reason "
                "FROM tester_platform.transport_policy_entries ORDER BY installation_id"
            ).fetchall()
            before_guard = conn.execute(
                "SELECT md5(pg_get_functiondef("
                "'tester_platform._check_public_transport(uuid,text)'::regprocedure))"
            ).fetchone()[0]
            before_versions = conn.execute(
                "SELECT count(*) FROM tester_platform.migrations"
            ).fetchone()[0]
            before_acl = conn.execute(
                "SELECT oid::regprocedure::text,proacl FROM pg_proc "
                "WHERE oid IN ("
                "'tester_platform._check_public_transport(uuid,text)'::regprocedure,"
                "'tester_platform.read_public_transport(uuid,text)'::regprocedure,"
                "'tester_platform.reserve_public_transport(uuid,text)'::regprocedure,"
                "'tester_platform.finish_public_transport(uuid,text,uuid,text)'::regprocedure,"
                "'tester_platform.reserve_public_dispatch(uuid)'::regprocedure,"
                "'tester_platform.finish_public_dispatch(uuid,uuid,double precision)'::regprocedure"
                ") ORDER BY oid::regprocedure::text"
            ).fetchall()
            assert len(before_acl) == 6
            assert before_versions == 11
        if ambiguous:
            try:
                migrate(legacy)
            except psycopg.Error as exc:
                # Must be the explicit fail-closed duplicate-enrollment guard,
                # not a silent partial migration or an unrelated syntax error.
                assert "enrolled by multiple installations" in str(exc), str(exc)
            else:
                raise AssertionError("Ambiguous legacy DB unexpectedly upgraded")
        else:
            migrate(legacy)
        with psycopg.connect(legacy) as conn:
            assert conn.execute(
                "SELECT installation_id,runtime_role,globally_halted,dispatch_token,"
                "dispatch_not_before FROM tester_platform.transport_policy_scopes "
                "ORDER BY installation_id"
            ).fetchall() == before_scope
            assert conn.execute(
                "SELECT installation_id,endpoint_key,request_token,state,reason "
                "FROM tester_platform.transport_policy_entries ORDER BY installation_id"
            ).fetchall() == before_entries
            n = conn.execute("SELECT count(*) FROM tester_platform.migrations").fetchone()[0]
            assert n == (11 if ambiguous else 12), (ambiguous, n)
            idx = conn.execute(
                "SELECT to_regclass('tester_platform.transport_policy_one_installation_per_runtime_role')"
            ).fetchone()[0]
            assert (idx is None) == ambiguous
            changed_guard = conn.execute(
                "SELECT md5(pg_get_functiondef("
                "'tester_platform._check_public_transport(uuid,text)'::regprocedure))"
            ).fetchone()[0]
            assert (changed_guard == before_guard) == ambiguous
            assert conn.execute(
                "SELECT oid::regprocedure::text,proacl FROM pg_proc "
                "WHERE oid IN ("
                "'tester_platform._check_public_transport(uuid,text)'::regprocedure,"
                "'tester_platform.read_public_transport(uuid,text)'::regprocedure,"
                "'tester_platform.reserve_public_transport(uuid,text)'::regprocedure,"
                "'tester_platform.finish_public_transport(uuid,text,uuid,text)'::regprocedure,"
                "'tester_platform.reserve_public_dispatch(uuid)'::regprocedure,"
                "'tester_platform.finish_public_dispatch(uuid,uuid,double precision)'::regprocedure"
                ") ORDER BY oid::regprocedure::text"
            ).fetchall() == before_acl, "012 modified existing policy function grants"
        # The two legacy databases deny cross-install logins and cannot be
        # mistaken for a fresh runtime route or cloned DSN.
        with psycopg.connect(legacy) as conn:
            assert conn.execute("SELECT current_user").fetchone() == (OWNER,)
        try:
            psycopg.connect(make_conninfo(app_dsn, dbname=dbname), connect_timeout=3).close()
        except psycopg.OperationalError:
            pass
        else:
            raise AssertionError("Foreign runtime CONNECT unexpectedly accepted")
        print(
            "PASS: real PG18 legacy {} upgrade; unchanged durable halt/lease; "
            "cross-database CONNECT denied".format(
                "ambiguous rollback" if ambiguous else "singleton"
            ),
            flush=True,
        )


def main():
    raw = os.environ.get("TESTER_PG18_SECURITY_ADMIN_DSN", "")
    parts = conninfo_to_dict(raw)
    if (
        set(parts) - {"host", "hostaddr", "port", "dbname", "user", "password", "require_auth"}
        or parts.get("host") != "127.0.0.1"
        or parts.get("hostaddr", "127.0.0.1") != "127.0.0.1"
        or parts.get("password") != "disposable-admin-only"
        or parts.get("port") != "55820"
        or parts.get("dbname") != "postgres"
        or parts.get("user") != "tester_ci_admin"
        or parts.get("require_auth") != "scram-sha-256"
        or os.environ.get("GITHUB_ACTIONS") != "true"
    ):
        raise SystemExit("Refusing non-disposable GitHub Actions PostgreSQL18 endpoint")
    with psycopg.connect(raw, autocommit=True) as admin:
        version = admin.execute("SHOW server_version_num").fetchone()[0]
        assert 180000 <= int(version) < 190000, "PostgreSQL18 actual server major required"
        assert admin.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone() == (True,)
        assert not admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DB,)).fetchone()
        for role in (OWNER, USER_A, USER_B):
            assert not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOREPLICATION "
                    "NOBYPASSRLS PASSWORD {}").format(sql.Identifier(OWNER), sql.Literal(OWNER_SECRET))
        )
        for name, secret in ((USER_A, A_SECRET), (USER_B, B_SECRET)):
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS PASSWORD {}").format(
                    sql.Identifier(name), sql.Literal(secret)
                )
            )
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(DB), sql.Identifier(OWNER)))
    base = {k: v for k, v in parts.items() if k not in ("dbname", "user", "password")}
    owner_dsn = make_conninfo(**base, dbname=DB, user=OWNER, password=OWNER_SECRET)
    app_dsn = make_conninfo(**base, dbname=DB, user=USER_A, password=A_SECRET)
    second_dsn = make_conninfo(**base, dbname=DB, user=USER_B, password=B_SECRET)
    with psycopg.connect(owner_dsn) as owner:
        owner.execute("REVOKE ALL ON DATABASE tester_ci_principal FROM PUBLIC")
        owner.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        for name in (USER_A, USER_B):
            owner.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(DB), sql.Identifier(name)))
    migrate(owner_dsn)
    with psycopg.connect(owner_dsn) as owner:
        for name in (USER_A, USER_B):
            owner.execute(
                sql.SQL("GRANT USAGE ON SCHEMA tester_platform TO {}").format(sql.Identifier(name))
            )
            owner.execute(sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                sql.SQL(ENDPOINT_FUNCTIONS), sql.Identifier(name)
            ))
            owner.execute(sql.SQL("REVOKE ALL ON tester_platform.transport_policy_scopes,"
                                  " tester_platform.transport_policy_entries FROM {}").format(sql.Identifier(name)))
        assert owner.execute(
            "SELECT checksum FROM tester_platform.migrations WHERE version=%s",
            ("012_transport_installation_principal.sql",),
        ).fetchone() is not None
    check_legacy_upgrades(raw, owner_dsn, app_dsn)
    for dsn, role in ((app_dsn, USER_A), (second_dsn, USER_B)):
        with psycopg.connect(dsn) as app:
            assert app.execute("SELECT session_user,current_user").fetchone() == (role, role)
            assert app.execute("SELECT version()").fetchone()[0].startswith("PostgreSQL 18")
    env = os.environ.copy()
    env.update(
        TEST_PUBLIC_POLICY_OWNER_DSN=owner_dsn,
        TEST_PUBLIC_POLICY_DSN=app_dsn,
        TEST_PUBLIC_POLICY_SECOND_APP_ROLE=USER_B,
    )
    nodes = [TEST_FILE + "::" + test for test in TESTS] + [EXTRA_CASE]
    command = [
        sys.executable, "-m", "pytest", "-vv", "-rs", "--strict-markers",
        "--timeout=40", "-p", "no:cacheprovider", *nodes,
    ]
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=330)
    print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, file=sys.stderr, flush=True)
    if result.returncode != 0:
        raise SystemExit(f"Mandatory PostgreSQL18 security pytest FAILED with code {result.returncode}")
    passed = set(re.findall(r"::(test_[A-Za-z0-9_]+)\s+PASSED", result.stdout))
    # Explicit nodes are selected, and a skipped/xfail result cannot pass.
    expected = set(TESTS) | {EXTRA_CASE.rsplit("::", 1)[1]}
    if passed != expected:
        raise SystemExit(f"Mandatory PG18 cases did not all PASS: expected={sorted(expected)}, got={sorted(passed)}")
    if re.search(r"\b(?:SKIPPED|XFAIL|XPASS|ERROR|FAILED)\b", result.stdout):
        raise SystemExit("Non-PASS status in mandatory PostgreSQL18 security suite")
    print(
        f"PASS: PostgreSQL {version} / {len(expected)} mandatory executed principal-isolation checks",
        flush=True,
    )


if __name__ == "__main__":
    main()
