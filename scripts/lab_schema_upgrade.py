"""Owner-run, data-preserving schema upgrade for an existing lab database (issue #110).

This is deliberately separate from the app-only updater. It never elevates,
never contacts PostgreSQL on port 5432 or a non-loopback host, never drops or
overwrites a database, and never runs as the App role.

Sequence (``upgrade``):

1. Preflight: loopback DSN on a non-5432 port, the connected role owns
   ``tester_storage.raw_records``, existing applied migrations are known and
   unmodified (the runners' checksum checks).
2. Backup: ``pg_dump -Fc`` to a new file in a new backup directory, SHA-256
   recorded, archive listed with ``pg_restore --list`` and, by default, restored
   into a NEW owner-only scratch database (CONNECT revoked from PUBLIC before the
   restore) whose content snapshot and migration state are recorded as a digest
   in the report. The scratch database is then dropped: it is a full copy that
   would double the disk use and must not stay reachable.
3. One transaction: content snapshot (row count and SHA-256 of every row of
   every base table in every non-system schema), which must equal the restored
   backup's snapshot (so the backup is proven to be exactly the pre-migration
   state), forward storage and platform migrations as owner, second snapshot of
   the pre-existing columns of every pre-existing table, and (issue #114) the
   lab App role ``tester_lab_app``, when it exists, loses DELETE/TRUNCATE on the
   platform/storage/lab schemas. Any difference, missing table/column, error or
   injected fault rolls the whole transaction back, so no partial schema is
   committed.
4. After commit the snapshot, migration state and App-role privileges are
   verified again. If that post-commit check fails the database is left as
   committed and the report names the verified backup to restore with
   ``restore`` into a NEW, owner-only database.

Each content snapshot reads every row once; there are four passes (restored
copy, before, after migration, post-commit) instead of a separate source pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

STORAGE_MIGRATIONS = SOURCE / "src/storage/migrations"
PLATFORM_MIGRATIONS = SOURCE / "src/api/migrations"
BOOKKEEPING = {"tester_storage.schema_migrations", "tester_platform.migrations"}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SCHEMA = "tester-lab-schema-upgrade/v1"
LAB_PORT = 55470
LAB_PGBIN = Path("C:/Program Files/PostgreSQL/18/bin")
LAB_APP_ROLE = "tester_lab_app"
# Same runtime write model as scripts/lab_portable.py: storage UPDATE only on the
# job claim/checkpoint tables; platform UPDATE stays as granted; never DELETE.
LAB_APP_STORAGE_UPDATE = ("download_jobs", "ingestion_checkpoints")
LAB_APP_SCHEMAS = ("tester_platform", "tester_storage", "tester_lab")


class UpgradeRefused(RuntimeError):
    pass


class InjectedFault(RuntimeError):
    pass


def safe_conninfo(dsn):
    from psycopg.conninfo import conninfo_to_dict

    values = conninfo_to_dict(dsn)
    hosts = [h for h in str(values.get("host", "")).split(",") if h]
    addrs = [h for h in str(values.get("hostaddr", "")).split(",") if h]
    port = str(values.get("port", ""))
    if not hosts or any(h not in LOOPBACK for h in hosts):
        raise UpgradeRefused("loopback_host_required")
    if any(a not in {"127.0.0.1", "::1"} for a in addrs):
        raise UpgradeRefused("loopback_hostaddr_required")
    if not port.isdigit() or int(port) == 5432 or "," in port:
        raise UpgradeRefused("explicit_non_5432_port_required")
    if values.get("service") or values.get("passfile"):
        raise UpgradeRefused("explicit_connection_parameters_required")
    if not values.get("dbname") or values["dbname"] in {"postgres", "template0", "template1"}:
        raise UpgradeRefused("dedicated_database_required")
    if not values.get("user"):
        raise UpgradeRefused("explicit_owner_user_required")
    return values


def tables(conn):
    rows = conn.execute(
        """SELECT n.nspname, c.relname, a.attname
           FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
           WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema')
             AND n.nspname NOT LIKE 'pg\\_%%'
           ORDER BY n.nspname, c.relname, a.attnum"""
    ).fetchall()
    result = {}
    for schema, table, column in rows:
        result.setdefault(schema + "." + table, []).append(column)
    return result


def table_digest(conn, name, columns):
    from psycopg import sql

    schema, table = name.split(".", 1)
    query = sql.SQL(
        'SELECT to_jsonb(x)::text AS line FROM (SELECT {} FROM {}.{}) x ORDER BY to_jsonb(x)::text COLLATE "C"'
    ).format(
        sql.SQL(",").join(sql.Identifier(c) for c in columns),
        sql.Identifier(schema),
        sql.Identifier(table),
    )
    h, count = hashlib.sha256(), 0
    with conn.cursor(name="lab_upgrade_" + hashlib.sha256(name.encode()).hexdigest()[:16]) as cur:
        cur.itersize = 2000
        cur.execute(query)
        for (line,) in cur:
            h.update(line.encode("utf-8") + b"\n")
            count += 1
    return {"columns": list(columns), "rows": count, "sha256": h.hexdigest()}


def snapshot(conn, inventory=None):
    """Exact content inventory; ``inventory`` restricts to earlier tables/columns."""
    current = tables(conn)
    selected = inventory if inventory is not None else current
    result = {}
    for name, columns in sorted(selected.items()):
        if name in BOOKKEEPING:
            continue
        if name not in current or any(c not in current[name] for c in columns):
            raise UpgradeRefused("table_or_column_removed:" + name)
        result[name] = table_digest(conn, name, columns)
    return result


def migration_state(conn):
    names = tables(conn)
    state = {"storage": {}, "platform": {}}
    if "tester_storage.schema_migrations" in names:
        state["storage"] = {
            str(v): c
            for v, c in conn.execute(
                "SELECT version,checksum FROM tester_storage.schema_migrations ORDER BY version"
            ).fetchall()
        }
    if "tester_platform.migrations" in names:
        state["platform"] = dict(
            conn.execute(
                "SELECT version,checksum FROM tester_platform.migrations ORDER BY version"
            ).fetchall()
        )
    return state


def content_digest(content, state):
    """Stable digest of a snapshot plus migration state (recorded in the report)."""
    data = json.dumps({"content": content, "migrations": state}, sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def lab_app_destructive_tables(conn, role=LAB_APP_ROLE):
    """Tables in the lab schemas on which ``role`` may still DELETE or TRUNCATE."""
    return [
        name
        for (name,) in conn.execute(
            """SELECT n.nspname||'.'||c.relname FROM pg_class c
               JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE c.relkind IN ('r','p') AND n.nspname::text = ANY(%s::text[])
                 AND (has_table_privilege(%s::name, c.oid, 'DELETE')
                      OR has_table_privilege(%s::name, c.oid, 'TRUNCATE'))
               ORDER BY 1""",
            (list(LAB_APP_SCHEMAS), role, role),
        ).fetchall()
    ]


def restrict_lab_app(conn, role=LAB_APP_ROLE):
    """Issue #114 for labs, on the caller's (migration) transaction.

    Migration 009 only reaches roles enrolled in transport_policy_scopes; the lab
    App role was granted UPDATE,DELETE ON ALL TABLES IN SCHEMA tester_platform by
    the d6059af lab installer. Revoke every destructive table privilege; keep only
    the UPDATE grants the current lab installer issues. Fail closed if any remain
    (e.g. granted by another grantor), which rolls the whole upgrade back.
    """
    from psycopg import sql

    if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone() is None:
        return None
    for schema in LAB_APP_SCHEMAS:
        if conn.execute("SELECT to_regnamespace(%s::text)", (schema,)).fetchone()[0] is None:
            continue
        conn.execute(
            sql.SQL(
                "REVOKE DELETE,TRUNCATE,REFERENCES,TRIGGER ON ALL TABLES IN SCHEMA {} FROM {}"
            ).format(sql.Identifier(schema), sql.Identifier(role))
        )
    if conn.execute("SELECT to_regnamespace('tester_storage')").fetchone()[0] is not None:
        conn.execute(
            sql.SQL("REVOKE UPDATE ON ALL TABLES IN SCHEMA tester_storage FROM {}").format(
                sql.Identifier(role)
            )
        )
        for table in LAB_APP_STORAGE_UPDATE:
            qualified = "tester_storage." + table
            if conn.execute(
                "SELECT CASE WHEN to_regclass(%s::text) IS NULL THEN false "
                "ELSE has_table_privilege(%s::name,%s::text,'INSERT') END",
                (qualified, role, qualified),
            ).fetchone()[0]:
                conn.execute(
                    sql.SQL("GRANT UPDATE ON {} TO {}").format(
                        sql.Identifier("tester_storage", table), sql.Identifier(role)
                    )
                )
    remaining = lab_app_destructive_tables(conn, role)
    if remaining:
        raise RuntimeError("lab_app_destructive_privilege_remains:" + ",".join(remaining))
    return {"role": role, "delete_truncate_revoked": True}


def apply_platform(conn, directory=PLATFORM_MIGRATIONS):
    """Same versions/checksums as ``src.api.db.migrate`` but on the caller's transaction."""
    conn.execute("SELECT pg_advisory_xact_lock(725281346)")
    conn.execute("CREATE SCHEMA IF NOT EXISTS tester_platform")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tester_platform.migrations (
        version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz DEFAULT now())"""
    )
    known = {p.name for p in Path(directory).glob("*.sql")}
    applied = dict(conn.execute("SELECT version,checksum FROM tester_platform.migrations"))
    if set(applied) - known:
        raise UpgradeRefused("platform_database_newer_than_source")
    for path in sorted(Path(directory).glob("*.sql")):
        content = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(content.encode()).hexdigest()
        if path.name in applied:
            if applied[path.name] != checksum:
                raise UpgradeRefused("platform_migration_checksum_mismatch:" + path.name)
            continue
        conn.execute(content)
        conn.execute(
            "INSERT INTO tester_platform.migrations(version,checksum) VALUES(%s,%s)",
            (path.name, checksum),
        )


def _pg_env(values):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    if values.get("password"):
        env["PGPASSWORD"] = values["password"]
    return env


def _pg_flags(values):
    return ["-h", values["host"], "-p", str(values["port"]), "-U", values["user"]]


def _tool(pg_bin, name):
    path = Path(pg_bin) / (name + (".exe" if os.name == "nt" else ""))
    if not path.is_file():
        raise UpgradeRefused("postgres_tool_missing:" + name)
    return str(path)


def _run(args, env, timeout):
    process = subprocess.run(
        args, stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=timeout
    )
    if process.returncode:
        raise RuntimeError("postgres_tool_failed:" + Path(args[0]).stem)
    return process.stdout


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _create_owner_only_database(dsn, name):
    """CREATE DATABASE grants CONNECT/TEMP to PUBLIC; revoke before any data arrives."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    with psycopg.connect(make_conninfo(dsn, dbname="postgres"), autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))


def _drop_database(dsn, name):
    """Drop the verification copy; ``False`` (never an exception) if it remains."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    try:
        with psycopg.connect(make_conninfo(dsn, dbname="postgres"), autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
            return (
                c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone() is None
            )
    except psycopg.Error:
        return False


def backup(dsn, backup_dir, pg_bin, *, verify_restore=True, timeout=3600):
    """Dump, list and (by default) restore into a dropped owner-only scratch database.

    Returns ``(report, restored)``; ``restored`` is ``(snapshot, migration_state)``
    of the restored copy, or ``None`` without restore verification. The caller
    must compare it with the source inside the migration transaction.
    """
    import psycopg
    from psycopg.conninfo import make_conninfo

    values = safe_conninfo(dsn)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=False)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "_" + os.urandom(4).hex()
    dump = backup_dir / (values["dbname"] + "-" + stamp + ".dump")
    env = _pg_env(values)
    _run(
        [_tool(pg_bin, "pg_dump"), *_pg_flags(values), "-Fc", "-f", str(dump), values["dbname"]],
        env,
        timeout,
    )
    listing = _run([_tool(pg_bin, "pg_restore"), "--list", str(dump)], env, timeout)
    if not listing.strip():
        raise RuntimeError("empty_backup_archive")
    report = {"path": str(dump), "sha256": file_sha256(dump), "restore_verified": False}
    restored = None
    if verify_restore:
        scratch = (values["dbname"][:30] + "_upgrade_verify_" + stamp.lower())[:63]
        _create_owner_only_database(dsn, scratch)
        report["restore_database"] = scratch
        try:
            _run(
                [
                    _tool(pg_bin, "pg_restore"),
                    *_pg_flags(values),
                    "--exit-on-error",
                    "-d",
                    scratch,
                    str(dump),
                ],
                env,
                timeout,
            )
            with psycopg.connect(make_conninfo(dsn, dbname=scratch)) as copy:
                restored = (snapshot(copy), migration_state(copy))
        finally:
            report["restore_database_dropped"] = _drop_database(dsn, scratch)
        if not report["restore_database_dropped"]:
            raise RuntimeError("restore_verify_database_not_dropped")
        report["restore_content_sha256"] = content_digest(*restored)
    if file_sha256(dump) != report["sha256"]:
        raise RuntimeError("backup_changed_during_verification")
    return report, restored


def _require_owner(conn):
    owner = conn.execute(
        "SELECT tableowner FROM pg_tables WHERE schemaname='tester_storage' AND tablename='raw_records'"
    ).fetchone()
    if owner is None:
        raise UpgradeRefused("existing_storage_schema_required")
    if owner[0] != conn.execute("SELECT current_user").fetchone()[0]:
        raise UpgradeRefused("schema_owner_role_required")


def upgrade(
    dsn,
    *,
    backup_dir,
    pg_bin,
    storage_dir=STORAGE_MIGRATIONS,
    platform_dir=PLATFORM_MIGRATIONS,
    verify_restore=True,
    fault=None,
):
    """Back up, migrate in one transaction, verify content; roll back on any failure."""
    import psycopg

    from src.storage.migrations import migrate as migrate_storage

    safe_conninfo(dsn)
    fault = fault or (lambda stage, conn: None)
    report = {"schema": SCHEMA, "status": "PENDING", "stage": "PREFLIGHT"}
    with psycopg.connect(dsn) as conn:
        _require_owner(conn)
        state_before = migration_state(conn)
        conn.rollback()
    report["migrations_before"] = state_before
    report["stage"] = "BACKUP"
    report["backup"], restored = backup(dsn, backup_dir, pg_bin, verify_restore=verify_restore)
    conn = psycopg.connect(dsn)
    committed = False
    try:
        report["stage"] = "MIGRATE"
        with conn.transaction():
            conn.execute("SET LOCAL lock_timeout = '30s'")
            inventory = tables(conn)
            before = snapshot(conn)
            if restored is not None:
                # One pass over the source: the restored backup must be exactly the
                # state being migrated (no write between backup and migration).
                if restored != (before, migration_state(conn)):
                    raise RuntimeError("backup_restore_content_mismatch")
                report["backup"]["restore_verified"] = True
            fault("before_migrate", conn)
            migrate_storage(conn, directory=storage_dir)
            fault("after_storage_migrate", conn)
            apply_platform(conn, platform_dir)
            fault("after_migrate", conn)
            after = snapshot(conn, inventory)
            if after != before:
                changed = sorted(k for k in before if before[k] != after.get(k))
                raise RuntimeError("existing_data_changed:" + ",".join(changed))
            report["lab_app_privileges"] = restrict_lab_app(conn)
            fault("after_privileges", conn)
            state_after = migration_state(conn)
            fault("before_commit", conn)
        committed = True
        report["stage"] = "POST_COMMIT_VERIFY"
        with conn.transaction():
            if (
                snapshot(conn, inventory) != before
                or migration_state(conn) != state_after
                or (report["lab_app_privileges"] and lab_app_destructive_tables(conn))
            ):
                report["status"] = "POST_COMMIT_VERIFY_FAILED_RESTORE_FROM_BACKUP"
                raise RuntimeError("post_commit_verification_failed")
            report["new_tables"] = sorted(set(tables(conn)) - set(inventory) - BOOKKEEPING)
        report.update(
            status="SCHEMA_UPGRADE_PASS",
            stage="COMPLETE",
            migrations_after=state_after,
            tables_verified=len(before),
            rows_verified=sum(v["rows"] for v in before.values()),
            content=before,
        )
        return report
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        report["failure_stage"] = report["stage"]
        if not committed:
            conn.rollback()
            with conn.transaction():
                if migration_state(conn) != state_before:
                    report["status"] = "ROLLBACK_STATE_MISMATCH"
                else:
                    report["status"] = "ROLLED_BACK"
        exc.upgrade_report = report
        raise
    finally:
        conn.close()


def restore(dsn, dump, target_db, pg_bin, *, expected_sha256, timeout=3600):
    """Restore a verified backup into a NEW, owner-only database; never over an existing one.

    CONNECT is revoked from PUBLIC before the restore; ``pg_restore`` of a single
    database archive carries no database-level ACL, so App roles cannot connect
    until the owner grants it explicitly.
    """
    import psycopg
    from psycopg.conninfo import make_conninfo

    values = safe_conninfo(dsn)
    if file_sha256(dump) != expected_sha256:
        raise UpgradeRefused("backup_hash_mismatch")
    with psycopg.connect(make_conninfo(dsn, dbname="postgres"), autocommit=True) as c:
        if c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (target_db,)).fetchone():
            raise UpgradeRefused("restore_target_must_be_new")
    # Owner-only until the owner deliberately grants CONNECT to an App role.
    _create_owner_only_database(dsn, target_db)
    _run(
        [
            _tool(pg_bin, "pg_restore"),
            *_pg_flags(values),
            "--exit-on-error",
            "-d",
            target_db,
            str(dump),
        ],
        _pg_env(values),
        timeout,
    )
    return {"status": "RESTORED_INTO_NEW_DATABASE", "database": target_db, "access": "owner-only"}


def lab_dsn(root):
    """Owner DSN of the dedicated lab cluster from its protected private config."""
    from psycopg.conninfo import make_conninfo

    from src.api.lab_install import check, plain

    owner = check(root)
    private = json.loads(plain(Path(root) / "private/db.json").read_text())
    if private["installation_id"] != owner["installation_id"]:
        raise UpgradeRefused("foreign_credentials")
    return make_conninfo(
        host="127.0.0.1",
        port=LAB_PORT,
        dbname="tester_lab",
        user="tester_lab_admin",
        password=private["admin_password"],
        sslmode="disable",
    )


def _write_report(path, report):
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2, sort_keys=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    up = sub.add_parser("upgrade", help="disposable/explicit DSN read from an environment variable")
    up.add_argument("--dsn-env", required=True)
    up.add_argument("--backup-dir", required=True)
    up.add_argument("--pg-bin", required=True)
    up.add_argument("--report", required=True)
    up.add_argument("--no-verify-restore", action="store_true")
    lab = sub.add_parser("lab-upgrade", help="owner-run upgrade of the dedicated lab cluster")
    lab.add_argument("--root", required=True)
    lab.add_argument("--backup-dir", required=True)
    lab.add_argument("--report", required=True)
    rs = sub.add_parser("restore", help="restore a verified backup into a NEW database")
    rs.add_argument("--dsn-env", required=True)
    rs.add_argument("--backup", required=True)
    rs.add_argument("--sha256", required=True)
    rs.add_argument("--target-db", required=True)
    rs.add_argument("--pg-bin", required=True)
    args = parser.parse_args(argv)
    if args.action == "restore":
        print(
            json.dumps(
                restore(
                    os.environ[args.dsn_env],
                    args.backup,
                    args.target_db,
                    args.pg_bin,
                    expected_sha256=args.sha256,
                )
            )
        )
        return 0
    if args.action == "lab-upgrade":
        dsn, pg_bin, verify = lab_dsn(args.root), LAB_PGBIN, True
    else:
        dsn, pg_bin, verify = os.environ[args.dsn_env], args.pg_bin, not args.no_verify_restore
    try:
        report = upgrade(dsn, backup_dir=args.backup_dir, pg_bin=pg_bin, verify_restore=verify)
    except BaseException as exc:
        report = getattr(exc, "upgrade_report", None) or {
            "schema": SCHEMA,
            "status": "FAILED",
            "error_type": type(exc).__name__,
        }
        _write_report(args.report, report)
        print(json.dumps({"status": report["status"], "report": args.report}))
        return 1
    _write_report(args.report, report)
    print(json.dumps({"status": report["status"], "report": args.report}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
