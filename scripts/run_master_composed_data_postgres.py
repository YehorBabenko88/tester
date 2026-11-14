"""Isolated PostgreSQL suite runner for a disposable MASTER source tree.

Each independent DATA/RESEARCH fixture suite gets its own newly created
database. This prevents cross-suite hardcoded fixture IDs from contaminating
trade counts or research metrics. No source HTTP calls, physical DB or DDL on
installed PostgreSQL is permitted.
"""

import os
import subprocess
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

SUITES = (
    ("tester_master_execution_ci", ("tests.test_execution_contract_postgres",)),
    ("tester_master_auxiliary_ci", ("tests.test_public_auxiliary_postgres",)),
    ("tester_master_research_ci", ("tests.test_composed_data_research_postgres",)),
)


def validate_disposable(dsn):
    values = conninfo_to_dict(dsn)
    if (
        values.get("host") != "127.0.0.1"
        or values.get("port") != "55479"
        or values.get("dbname") != "tester_aux_ci"
        or values.get("user") != "tester_aux_ci"
        or values.get("hostaddr")
        or values.get("service")
        or values.get("options")
    ):
        raise ValueError("non-production isolated PostgreSQL 55479 DSN required")
    return values


def main():
    if os.getenv("CI") != "true" or os.getenv("MASTER_COMPOSED_DATA_TEST") != "true":
        raise SystemExit("disposable MASTER CI only")
    if os.getenv("TESTER_AUX_LIVE_BYBIT") == "yes":
        raise SystemExit("live Bybit disabled for composed DATA acceptance")
    source_dsn = os.environ["TESTER_AUX_TEST_DSN"]
    admin_dsn = os.environ["TESTER_TEST_ADMIN_DSN"]
    validate_disposable(source_dsn)
    validate_disposable(admin_dsn)

    # The storage migration test creates its own fresh v1-format DB. It must
    # not share a fixture DB with public execution or auxiliary test modules.
    subprocess.run(
        (
            sys.executable,
            "-m",
            "unittest",
            "-v",
            "tests.storage.test_postgres.PostgresTests.test_upgrade_preserves_version_one_raw_data",
            "tests.storage.test_postgres.PostgresTests.test_migrations_repeat_and_checksum",
        ),
        check=True,
    )

    databases = []
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        for database, _ in SUITES:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            databases.append(database)

    try:
        for database, modules in SUITES:
            environment = {
                **os.environ,
                "TESTER_AUX_TEST_DSN": make_conninfo(source_dsn, dbname=database),
            }
            print(
                "COMPOSED_DATA_ISOLATED_SUITE",
                database,
                ",".join(modules),
                flush=True,
            )
            subprocess.run(
                (sys.executable, "-m", "unittest", "-v", *modules),
                check=True,
                env=environment,
            )
    finally:
        # All DBs were created on the disposable 55479 runner; never touch
        # the caller's existing base DB or any system PostgreSQL instance.
        with psycopg.connect(admin_dsn, autocommit=True) as connection:
            for database in reversed(databases):
                connection.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database))
                )

    print("COMPOSED_DATA_PG_ALL_SUITES_PASS", len(SUITES), flush=True)


if __name__ == "__main__":
    main()
