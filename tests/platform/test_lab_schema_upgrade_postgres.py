"""Real PostgreSQL proof of the owner-run lab schema upgrade (issue #110).

Starts from a database built with exactly the d6059af migration set (storage
001-007, platform 001-007), seeds every dataset kind, then upgrades to the
current source with backup, content verification and fault-injected rollback.
Requires a disposable cluster on a non-5432 loopback port.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADMIN = os.environ.get("TESTER_LAB_UPGRADE_ADMIN_DSN")
PG_BIN = os.environ.get("TESTER_LAB_UPGRADE_PG_BIN")
if not (ADMIN and PG_BIN) and os.environ.get("TESTER_REQUIRE_LAB_UPGRADE_PG") == "1":
    raise RuntimeError("TESTER_LAB_UPGRADE_ADMIN_DSN and TESTER_LAB_UPGRADE_PG_BIN are required")
pytestmark = pytest.mark.skipif(
    not (ADMIN and PG_BIN) or sys.platform == "win32",
    reason="requires disposable PostgreSQL TESTER_LAB_UPGRADE_ADMIN_DSN on a non-5432 port",
)
psycopg = pytest.importorskip("psycopg")
from psycopg import sql  # noqa: E402
from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402

from scripts import lab_schema_upgrade as lab  # noqa: E402

# Exact migration bytes installed by d6059af (git show d6059af:<path> | sha256sum).
D6059AF_MIGRATIONS = {
    "src/storage/migrations/001_initial.sql": "72c83a9524f2ec8631463e2325e4ad245145288a313006c6fbb1be7b8c0e9018",
    "src/storage/migrations/002_precision_and_validation.sql": "8b70fae27fce23e8b0a9e2d52f3b14daade123837d93ccd12e76e77c4822901c",
    "src/storage/migrations/003_retry_schedule.sql": "39e7708f323733de0d8a5a43bfe40c0b11b32e6c6c09ace56eee1d86955fe323",
    "src/storage/migrations/004_trade_interval.sql": "45d555b21b109a5783eac555061bcf1fbcf6ea2effd827526c0f9b7eef1bd254",
    "src/storage/migrations/005_runs_and_recovery_claims.sql": "1eec298593de7bffc4563786d3935fdb2429465e864e3f42410727d6cd0943c3",
    "src/storage/migrations/006_lifecycle_dry_run.sql": "8d35b1993052cfb1ae421528b9221d98b7fb779ddfa36d7e9e05b76b18b0de08",
    "src/storage/migrations/007_visibility_foundation.sql": "8aed96de746823c47bc4e7ef7719741a35a625ddb098890bc56d717472bb914f",
    "src/api/migrations/001_jobs.sql": "0bc442a49feb7fbd3c225822c5b3bf1ba486a84256ad6610d71008481b78daaa",
    "src/api/migrations/002_admission.sql": "263651e0ad5fc00cf4a87126f75c25094c728eea2b9be7153951367866489150",
    "src/api/migrations/003_restart_budget.sql": "f2f8b1e966cbe588d696a9de901938fd3d03ed32d2ec6962392efb377ecdc765",
    "src/api/migrations/004_transport_policy.sql": "9935dc12dbf92981d8ae400ed9d2a2fd8aa5062ffce838fb3c9e7ad37a86945d",
    "src/api/migrations/005_worker_instance.sql": "3375feb32f62577bcbe38dccc037e9a77a6f3f8082566e7eca3a5a7e6f6891e1",
    "src/api/migrations/006_public_dispatch.sql": "250273ca61104fd1b854eaaa97d534e56f3c54b97b253469d1a709bde02dc948",
    "src/api/migrations/007_public_dispatch_grants.sql": "df412301e0e5a5c17bcda827eab1a5fa789d2969215faa133839beb27667f7a0",
}

SEED = """
INSERT INTO tester_storage.instruments(venue,category,symbol) VALUES('bybit','linear','BTCUSDT'),('bybit','inverse','BTCUSD');
INSERT INTO tester_storage.raw_records(record_key,venue,category,symbol,dataset,interval,schema_version,timestamp_ms,timestamp_precision,trade_id,id_origin,payload,content_hash)
SELECT encode(sha256(convert_to(d||i||t::text,'UTF8')),'hex'),'bybit',c,s,d,i,'1.0',t,'ms',tid,CASE WHEN tid IS NULL THEN NULL ELSE 'exchange' END,p::jsonb,encode(sha256(convert_to(p,'UTF8')),'hex')
FROM (VALUES
 ('linear','BTCUSDT','trades','',1700000000123,'t1','{"trade_id":"t1","price":"123.000000000000000001","size":"0.01","side":"Buy"}'),
 ('linear','BTCUSDT','trades','',1700000000124,'t2','{"trade_id":"t2","price":"123.5","size":"0.02","side":"Sell"}'),
 ('inverse','BTCUSD','trades','',1700000000125,'t3','{"trade_id":"t3","price":"100","size":"100","side":"Buy"}'),
 ('linear','BTCUSDT','candles','1',1700000040000,NULL,'{"open":"100","high":"101.5","low":"99","close":"100.25","volume":"12.5","turnover":"1253.1"}'),
 ('linear','BTCUSDT','mark','1',1700000040000,NULL,'{"open":"100","high":"101","low":"99","close":"100"}'),
 ('linear','BTCUSDT','index','1',1700000040000,NULL,'{"open":"100","high":"101","low":"99","close":"100"}'),
 ('linear','BTCUSDT','premium','1',1700000040000,NULL,'{"open":"-0.0001","high":"0.0002","low":"-0.0003","close":"0.0001"}'),
 ('linear','BTCUSDT','open_interest','5min',1700000100000,NULL,'{"value":"12345.678"}'),
 ('linear','BTCUSDT','funding','',1700006400000,NULL,'{"value":"-0.000125"}'),
 ('linear','BTCUSDT','ratio','5min',1700000100000,NULL,'{"value":"0.55"}')
) v(c,s,d,i,t,tid,p);
INSERT INTO tester_storage.record_sources VALUES(repeat('b',64),(SELECT record_key FROM tester_storage.raw_records WHERE trade_id='t1'),'BTCUSDT','{"source_kind":"archive"}','{"raw":1}',now());
INSERT INTO tester_storage.download_jobs(job_key,scope_key,symbol,request,state) VALUES('job-1','scope-1','BTCUSDT','{"dataset":"trades"}','committed');
INSERT INTO tester_storage.ingestion_checkpoints(job_key,checkpoint,commit_hash) VALUES('job-1','{"cursor":"c-9","end_ms":1700000060000}',repeat('a',64));
INSERT INTO tester_platform.jobs(id,request,state,progress,checkpoint,attempts) VALUES('00000000-0000-4000-8000-000000000001','{"symbol":"BTCUSDT"}','completed',1,'{"page":3}',1),('00000000-0000-4000-8000-000000000002','{"symbol":"ETHUSDT"}','paused',0.25,'{"page":1}',2);
CREATE SCHEMA tester_lab;
CREATE TABLE tester_lab.projections(record_key text PRIMARY KEY,record jsonb NOT NULL);
INSERT INTO tester_lab.projections VALUES('research-1','{"kind":"research_result","sharpe":"1.25","trades":42}');
"""

DATASETS = ("trades", "candles", "mark", "index", "premium", "open_interest", "funding", "ratio")


def baseline_dirs(tmp_path):
    dirs = {"src/storage/migrations": tmp_path / "storage", "src/api/migrations": tmp_path / "api"}
    for target in dirs.values():
        target.mkdir()
    for name, digest in D6059AF_MIGRATIONS.items():
        data = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == digest, name
        (dirs[str(Path(name).parent).replace("\\", "/")] / Path(name).name).write_bytes(data)
    return dirs["src/storage/migrations"], dirs["src/api/migrations"]


@pytest.fixture
def legacy(tmp_path):
    from src.storage.migrations import migrate as migrate_storage

    name = "lab_upg_" + uuid.uuid4().hex[:12]
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = make_conninfo(ADMIN, dbname=name)
    storage, platform = baseline_dirs(tmp_path)
    with psycopg.connect(dsn) as c:
        migrate_storage(c, directory=storage)
        with c.transaction():
            lab.apply_platform(c, platform)
        with c.transaction():
            c.execute(SEED)
    try:
        yield dsn
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as c:
            for (db,) in c.execute(
                "SELECT datname FROM pg_database WHERE datname LIKE %s", (name + "%",)
            ).fetchall():
                c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db)))


def state(dsn):
    with psycopg.connect(dsn) as c:
        return lab.snapshot(c), lab.migration_state(c), side_constraints(c)


def side_constraints(conn):
    return dict(
        conn.execute(
            """SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint
               WHERE conrelid='tester_storage.raw_records'::regclass
               AND conname IN ('raw_records_check2','trade_side_required')"""
        ).fetchall()
    )


def test_baseline_is_exact_d6059af_schema(legacy):
    content, migrations, constraints = state(legacy)
    assert sorted(migrations["storage"]) == [str(v) for v in range(1, 8)]
    assert len(migrations["platform"]) == 7
    assert all("Unknown" not in d for d in constraints.values())
    with psycopg.connect(legacy) as c:
        datasets = dict(
            c.execute("SELECT dataset,count(*) FROM tester_storage.raw_records GROUP BY 1")
        )
    assert set(datasets) == set(DATASETS)
    for table in (
        "tester_storage.raw_records",
        "tester_storage.record_sources",
        "tester_storage.download_jobs",
        "tester_storage.ingestion_checkpoints",
        "tester_platform.jobs",
        "tester_lab.projections",
    ):
        assert content[table]["rows"] > 0, table


def test_upgrade_applies_new_migrations_and_preserves_all_data(legacy, tmp_path):
    from src.api.db import migrate as platform_migrate
    from src.storage.migrations import migrate as migrate_storage

    before, migrations_before, _ = state(legacy)
    report = lab.upgrade(legacy, backup_dir=tmp_path / "backup", pg_bin=PG_BIN)
    assert report["status"] == "SCHEMA_UPGRADE_PASS"
    assert report["content"] == before
    assert report["rows_verified"] == sum(v["rows"] for v in before.values())
    after, migrations_after, constraints = state(legacy)
    current_storage = sorted(p.name for p in (ROOT / "src/storage/migrations").glob("*.sql"))
    current_platform = sorted(p.name for p in (ROOT / "src/api/migrations").glob("*.sql"))
    assert sorted(migrations_after["storage"], key=int) == [
        str(int(n.split("_")[0])) for n in current_storage
    ]
    assert sorted(migrations_after["platform"]) == current_platform
    assert "8" in migrations_after["storage"] and "8" not in migrations_before["storage"]
    assert all("Unknown" in d for d in constraints.values())
    # Every pre-existing table/column is byte-for-byte identical.
    with psycopg.connect(legacy) as c:
        assert lab.snapshot(c, {k: v["columns"] for k, v in before.items()}) == before
    # The canonical runners agree that nothing is left to apply.
    with psycopg.connect(legacy) as c:
        migrate_storage(c)
    platform_migrate(legacy)
    assert state(legacy)[1] == migrations_after
    # Backup is verified and restorable at the pre-upgrade state.
    dump = Path(report["backup"]["path"])
    assert lab.file_sha256(dump) == report["backup"]["sha256"]
    assert report["backup"]["restore_verified"] is True
    restored = make_conninfo(legacy, dbname=report["backup"]["restore_database"])
    restored_content, restored_migrations, _ = state(restored)
    assert restored_content == before and restored_migrations == migrations_before
    # Explicit restore never targets an existing database.
    with pytest.raises(lab.UpgradeRefused, match="restore_target_must_be_new"):
        lab.restore(
            legacy,
            dump,
            report["backup"]["restore_database"],
            PG_BIN,
            expected_sha256=report["backup"]["sha256"],
        )
    new_db = conninfo_to_dict(legacy)["dbname"] + "_rs"
    lab.restore(legacy, dump, new_db, PG_BIN, expected_sha256=report["backup"]["sha256"])
    assert state(make_conninfo(legacy, dbname=new_db))[0] == before


@pytest.mark.parametrize(
    "stage", ["before_migrate", "after_storage_migrate", "after_migrate", "before_commit"]
)
def test_fault_injection_rolls_back_schema_and_data(legacy, tmp_path, stage):
    before = state(legacy)

    def fault(at, conn):
        if at == stage:
            raise lab.InjectedFault(at)

    with pytest.raises(lab.InjectedFault) as caught:
        lab.upgrade(legacy, backup_dir=tmp_path / "backup", pg_bin=PG_BIN, fault=fault)
    assert caught.value.upgrade_report["status"] == "ROLLED_BACK"
    assert caught.value.upgrade_report["failure_stage"] == "MIGRATE"
    assert state(legacy) == before


def test_failing_later_migration_rolls_back_earlier_ones(legacy, tmp_path):
    storage = tmp_path / "storage-fail"
    shutil.copytree(ROOT / "src/storage/migrations", storage)
    (storage / "099_injected_failure.sql").write_text("SELECT 1/0;\n", encoding="utf-8")
    before = state(legacy)
    with pytest.raises(psycopg.errors.DivisionByZero) as caught:
        lab.upgrade(
            legacy,
            backup_dir=tmp_path / "backup",
            pg_bin=PG_BIN,
            storage_dir=storage,
            verify_restore=False,
        )
    assert caught.value.upgrade_report["status"] == "ROLLED_BACK"
    assert state(legacy) == before


def test_data_change_during_migration_is_detected_and_rolled_back(legacy, tmp_path):
    before = state(legacy)

    def tamper(at, conn):
        if at == "after_migrate":
            conn.execute("UPDATE tester_platform.jobs SET attempts=attempts+1")

    with pytest.raises(RuntimeError, match="existing_data_changed:tester_platform.jobs"):
        lab.upgrade(
            legacy,
            backup_dir=tmp_path / "backup",
            pg_bin=PG_BIN,
            fault=tamper,
            verify_restore=False,
        )
    assert state(legacy) == before


def test_non_owner_role_is_refused_before_backup(legacy, tmp_path):
    role = "lab_upg_app_" + uuid.uuid4().hex[:8]
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
    try:
        with pytest.raises(lab.UpgradeRefused, match="schema_owner_role_required"):
            lab.upgrade(make_conninfo(legacy, user=role), backup_dir=tmp_path / "b", pg_bin=PG_BIN)
        assert not (tmp_path / "b").exists()
    finally:
        with psycopg.connect(ADMIN, autocommit=True) as c:
            c.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_cli_upgrade_writes_report(legacy, tmp_path):
    env = dict(os.environ, LAB_UPGRADE_TEST_DSN=legacy)
    report = tmp_path / "report.json"
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts/lab_schema_upgrade.py"),
            "upgrade",
            "--dsn-env",
            "LAB_UPGRADE_TEST_DSN",
            "--backup-dir",
            str(tmp_path / "backup"),
            "--pg-bin",
            PG_BIN,
            "--report",
            str(report),
        ],
        env=env,
        capture_output=True,
        timeout=600,
    )
    assert process.returncode == 0, process.stderr.decode()[-2000:]
    import json

    assert json.loads(report.read_text())["status"] == "SCHEMA_UPGRADE_PASS"
    assert legacy.encode() not in process.stdout
