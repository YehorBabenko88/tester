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
TESTS = [
    "test_pg_runtime_principal_unique_and_scope_forgery_denied",
    "test_pg_runtime_cannot_delete_enrollment_or_reset_foreign_owner",
    "test_pg_concurrent_same_login_enrollment_serialized",
    "test_pg_foreign_uuid_rejected_with_two_distinct_valid_logins",
    "test_pg_principal_revocation_denies_outstanding_leases",
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


def main():
    raw = os.environ.get("TESTER_PG18_SECURITY_ADMIN_DSN", "")
    parts = conninfo_to_dict(raw)
    if (
        parts.get("host") != "127.0.0.1"
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
    nodes = [TEST_FILE + "::" + test for test in TESTS]
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
    passed = set(re.findall(r"test_(pg_[A-Za-z0-9_]+)\s+PASSED", result.stdout))
    # Explicit nodes are selected, and a skipped/xfail result cannot pass.
    expected = {name.removeprefix("test_") for name in TESTS}
    if passed != expected:
        raise SystemExit(f"Mandatory PG18 cases did not all PASS: expected={sorted(expected)}, got={sorted(passed)}")
    if re.search(r"\b(?:SKIPPED|XFAIL|XPASS|ERROR|FAILED)\b", result.stdout):
        raise SystemExit("Non-PASS status in mandatory PostgreSQL18 security suite")
    print(f"PASS: PostgreSQL {version} / {len(TESTS)} mandatory executed principal-isolation checks", flush=True)


if __name__ == "__main__":
    main()
