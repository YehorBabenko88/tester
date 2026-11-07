"""Explicit owned-cluster data/restore test. Never installs or impersonates SCM.

Retains source data, database, restore database, backups and evidence. No DELETE,
DROP, cleanup, elevation, real orders or existing PostgreSQL5432 access.
"""

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("C:/ProgramData/TesterPhysicalTest20261107")
SHA = "0ff59c69b71b61f32f05eda5d6c6f1bd81490c8d"
DIGEST = "ec9ce08fa385588db1d7cd3e95c45dfd518a9ef04b694691f8b5dece700656ec"
RELEASE = ROOT / "releases" / SHA
RESTORE_DB = "tester_physical_restore_20261107_hb1738"


def snapshot(connection):
    from psycopg import sql

    result = {}
    tables = connection.execute(
        "SELECT table_schema,table_name FROM information_schema.tables "
        "WHERE table_schema IN ('tester_storage','tester_platform') "
        "AND table_type='BASE TABLE' ORDER BY table_schema,table_name"
    ).fetchall()
    for schema, table in tables:
        rows = connection.execute(
            sql.SQL("SELECT * FROM {} ORDER BY 1").format(sql.Identifier(schema, table))
        ).fetchall()
        lines = sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
        result[schema + "." + table] = {
            "rows": len(rows),
            "sha256": hashlib.sha256("\n".join(lines).encode()).hexdigest(),
        }
    return result


def run():
    sys.path.insert(0, str(RELEASE))
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    from src.api.physical_scope import validate_owner, validate_release
    from src.storage.models import normalize
    from src.storage.store import Store

    owner = validate_owner()
    validate_release(RELEASE, DIGEST)
    spec = importlib.util.spec_from_file_location(
        "owned_pg_preparer", RELEASE / "scripts/windows/prepare_physical_database.py"
    )
    pg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pg)
    _, state = pg.preflight()
    credentials = pg.validate_secrets(pg.read_json(pg.SECRETS), owner)
    source = ROOT / "runtime/source-probe-20261107T171937Z"
    probe = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    assert len(probe["results"]) == 10
    admin = make_conninfo(
        host="127.0.0.1",
        port=55469,
        user=pg.ADMIN,
        password=credentials["admin_password"],
        dbname=pg.DATABASE,
        connect_timeout=5,
    )
    app = make_conninfo(admin, user=pg.APP, password=credentials["app_password"])
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence = ROOT / "runtime" / ("data-restore-" + stamp)
    evidence.mkdir()
    report = {
        "frozen_sha": SHA,
        "scope": "FOREGROUND_DATA_TEST_NOT_SCM_ACCEPTANCE",
        "started_at": stamp,
        "stage": "preflight",
        "status": "IN_PROGRESS",
        "restore_database": RESTORE_DB,
        "all_data_retained": True,
    }
    started = False
    try:
        import socket

        with socket.socket() as port:
            port.bind(("127.0.0.1", 55469))
        assert (
            pg.command(
                [pg.PGBIN / "pg_ctl.exe", "-D", pg.DATA, "status"], allowed=(0, 3)
            ).returncode
            == 3
        )
        report["stage"] = "start_owned_cluster"
        started = True
        pg.command(
            [
                pg.PGBIN / "pg_ctl.exe",
                "-D",
                pg.DATA,
                "-l",
                ROOT / "runtime/database-data-test.log",
                "-w",
                "start",
            ]
        )
        with psycopg.connect(admin) as connection:
            assert pg.same_directory(
                pg.DATA, connection.execute("SHOW data_directory").fetchone()[0]
            )
            assert (
                str(
                    connection.execute(
                        "SELECT system_identifier FROM pg_control_system()"
                    ).fetchone()[0]
                )
                == state["system_identifier"]
            )
        store = Store(app, storage_root=ROOT / "runtime")
        report["stage"] = "ingest_and_replay"

        def ingest(item):
            symbol = item["symbol"]
            bundle = json.loads((source / (symbol + ".handoff.json")).read_text(encoding="utf-8"))
            expected = {
                normalize(r["storage_record"])["record_key"]: normalize(r["storage_record"])
                for r in bundle["records"]
            }
            results = []
            for _ in range(2):
                for batch in bundle["storage_batches"]:
                    results.append(
                        store.ingest(
                            batch["records"],
                            batch["evidence"],
                            batch["batch_id"],
                            batch["checkpoint"],
                            run_id=batch["ingestion_run"],
                        )
                    )
            actual = store.read(
                "candles",
                "bybit",
                "linear",
                symbol,
                probe["start_ms"],
                probe["end_ms"],
                interval="1",
                limit=100,
            )
            assert len(actual) == len(expected) == 5
            for row in actual:
                original = expected[row["record_key"]]
                for key in (
                    "content_hash",
                    "timestamp_ms",
                    "timestamp_ns",
                    "timestamp_us",
                    "timestamp_precision",
                ):
                    assert row.get(key) == original.get(key), key
                assert row["payload"] == original["payload"], "original payload values"
                assert row["provenance"]["authentic"]
                assert row["provenance"]["observations"]
            return {
                "symbol": symbol,
                "records_after_replay": len(actual),
                "timestamp_precision": sorted({r["timestamp_precision"] for r in actual}),
                "raw_and_provenance_exact": True,
                "verified_batch_flags": [r["verified"] for r in results],
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            report["instruments"] = list(pool.map(ingest, probe["results"]))
        with psycopg.connect(app) as connection:
            pg.permission_probe(connection)
        report["application_no_ddl_no_delete"] = True
        with psycopg.connect(admin) as connection:
            before = snapshot(connection)
        report["stage"] = "backup"
        backup = ROOT / "backups" / ("data-" + stamp + ".dump")
        with backup.open("xb"):
            pass
        pg.command(
            [
                pg.PGBIN / "pg_dump.exe",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--host=127.0.0.1",
                "--port=55469",
                "--username=" + pg.ADMIN,
                "--dbname=" + pg.DATABASE,
                "--file=" + str(backup),
            ],
            private_env={"PGPASSWORD": credentials["admin_password"]},
        )
        report["backup"] = {
            "file": str(backup),
            "bytes": backup.stat().st_size,
            "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        }
        report["stage"] = "restore_new_owned_database"
        with psycopg.connect(
            make_conninfo(admin, dbname="postgres"), autocommit=True
        ) as connection:
            assert pg.same_directory(
                pg.DATA, connection.execute("SHOW data_directory").fetchone()[0]
            )
            assert not connection.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (RESTORE_DB,)
            ).fetchone()
            connection.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(RESTORE_DB), sql.Identifier(pg.ADMIN)
                )
            )
            connection.execute(
                sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                    sql.Identifier(RESTORE_DB),
                    sql.Literal("tester-physical:" + owner["installation_id"]),
                )
            )
        pg.command(
            [
                pg.PGBIN / "pg_restore.exe",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--host=127.0.0.1",
                "--port=55469",
                "--username=" + pg.ADMIN,
                "--dbname=" + RESTORE_DB,
                str(backup),
            ],
            private_env={"PGPASSWORD": credentials["admin_password"]},
        )
        with psycopg.connect(make_conninfo(admin, dbname=RESTORE_DB)) as connection:
            after = snapshot(connection)
        assert before == after, "Restored canonical/provenance/audit tables differ"
        report.update(
            status="PASS",
            stage="verified",
            tables=before,
            restored_tables_equal=True,
            records=50,
            full_history="NOT_PROVEN",
            scm_acceptance=False,
        )
    except Exception as error:
        report.update(
            status="FAIL",
            error_type=type(error).__name__,
            sqlstate=getattr(error, "sqlstate", None),
        )
    finally:
        if started:
            try:
                pg.command([pg.PGBIN / "pg_ctl.exe", "-D", pg.DATA, "-m", "fast", "-w", "stop"])
                report["owned_cluster_stopped"] = True
            except Exception as error:
                report.update(status="FAIL", stop_error_type=type(error).__name__)
        report["finished_at"] = datetime.now(UTC).isoformat()
        (evidence / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "stage": report["stage"],
                "evidence": str(evidence),
                "records": report.get("records"),
                "error_type": report.get("error_type"),
            }
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        raise SystemExit(run())
    print(
        json.dumps(
            {
                "mode": "PLAN_ONLY",
                "pg_port": 55469,
                "root": str(ROOT),
                "new_restore_database": RESTORE_DB,
                "scm_actions": False,
                "deletes_data": False,
            }
        )
    )
