"""Fresh lab bootstrap and bounded portable smoke. No elevation or legacy adoption.

Run only from a published immutable source archive; root must be new on prepare.
Secrets never enter argv/stdout. Existing PostgreSQL5432 is never contacted.
"""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

from src.api.lab_install import check, plain, prepare  # noqa: E402

PGBIN = Path("C:/Program Files/PostgreSQL/18/bin")
PORT = 55470


def save(path, value):
    path = plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, indent=2)


def command(root, args, env=None, timeout=90):
    log = plain(root / "runtime" / "commands.log")
    with log.open("ab") as output:
        process = subprocess.run(
            [str(x) for x in args],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            env=env,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    if process.returncode:
        raise RuntimeError("Owned command failed; inspect private lab command log")


def values(root):
    owner = check(root)
    private = json.loads(plain(root / "private/db.json").read_text())
    if private["installation_id"] != owner["installation_id"]:
        raise ValueError("Foreign credentials refused")
    return owner, private


def dsn(secret, db="tester_lab", runtime=False):
    from psycopg.conninfo import make_conninfo

    return make_conninfo(
        host="127.0.0.1",
        port=PORT,
        dbname=db,
        user="tester_lab_app" if runtime else "tester_lab_admin",
        password=secret["app_password"] if runtime else secret["admin_password"],
        connect_timeout=5,
    )


def verify_pg(root):
    from psycopg import connect

    owner, secret = values(root)
    with connect(dsn(secret, "postgres")) as c:
        data = c.execute("SHOW data_directory").fetchone()[0]
        port = c.execute("SHOW port").fetchone()[0]
        if plain(data) != plain(root / "postgres-data") or str(port) != str(PORT):
            raise ValueError("Foreign PostgreSQL endpoint refused")
        ident = str(c.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
    recorded = root / "state/postgres-identity.json"
    if recorded.exists():
        state = json.loads(recorded.read_text())
        if state != {"installation_id": owner["installation_id"], "system_identifier": ident}:
            raise ValueError("PostgreSQL identity mismatch")
    else:
        save(recorded, dict(installation_id=owner["installation_id"], system_identifier=ident))


def start_pg(root, initial=False):
    values(root)
    if not initial:
        pins = json.loads((root / "state/postgres-binaries.json").read_text())["files"]
        for name, digest in pins.items():
            path = plain(PGBIN / name)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Installed PostgreSQL binary changed")
    with socket.socket() as s:
        s.bind(("127.0.0.1", PORT))
    command(
        root,
        [
            PGBIN / "pg_ctl.exe",
            "-D",
            root / "postgres-data",
            "-l",
            root / "runtime/postgres.log",
            "-w",
            "-t",
            "45",
            "start",
        ],
    )
    if not initial:
        verify_pg(root)


def stop_pg(root):
    verify_pg(root)
    command(
        root,
        [
            PGBIN / "pg_ctl.exe",
            "-D",
            root / "postgres-data",
            "-m",
            "fast",
            "-w",
            "-t",
            "45",
            "stop",
        ],
    )


def bootstrap(args):
    root = plain(args.root)
    payload = json.loads(Path(args.payload).read_text())
    if payload["source_sha"] != args.sha:
        raise ValueError("Payload source SHA mismatch")
    for port in (18766, PORT):
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))
    owner = prepare(root, "YehorBabenko88/tester", args.sha, SOURCE, payload["files"])
    save(root / "state/deploy-payload.json", payload)
    (root / "private").mkdir()
    # Scope ACLs before generating passwords. This modifies only our freshly owned paths.
    account = subprocess.check_output(["whoami"], text=True).strip()
    command(
        root,
        [
            "icacls",
            root,
            "/inheritance:r",
            "/grant:r",
            account + ":(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
        ],
    )
    command(root, ["icacls", root, "/setowner", account])
    private = dict(
        installation_id=owner["installation_id"],
        admin_password=secrets.token_urlsafe(48),
        app_password=secrets.token_urlsafe(48),
    )
    save(root / "private/db.json", private)
    save(
        root / "state/postgres-binaries.json",
        {
            "files": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in PGBIN.iterdir()
                if p.is_file()
            }
        },
    )
    # Full private interpreter/stdlib, no user site-packages or global installation change.
    pythonroot = root / "dependencies/python"
    shutil.copytree(
        sys.base_prefix,
        pythonroot,
        ignore=shutil.ignore_patterns(
            "site-packages", "__pycache__", "test", "tests", "Doc", "Tools", "tcl"
        ),
    )
    privatepython = pythonroot / "python.exe"
    command(root, [privatepython, "-m", "venv", root / "dependencies/venv"], timeout=120)
    wheels = root / "dependencies/wheels"
    shutil.copytree(args.wheelhouse, wheels)
    python = root / "dependencies/venv/Scripts/python.exe"
    release = root / "releases" / args.sha
    command(
        root,
        [
            python,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            wheels,
            "--require-hashes",
            "-r",
            release / "requirements-windows-lab.lock",
        ],
        timeout=180,
    )
    command(root, [python, "-m", "pip", "check"])
    from src.api.lab_scm import stage_service_runtime

    stage_service_runtime(root)
    pw = root / "private/initdb-password.txt"
    pw.write_text(private["admin_password"] + "\n", encoding="ascii")
    command(
        root,
        [
            PGBIN / "initdb.exe",
            "-D",
            root / "postgres-data",
            "-U",
            "tester_lab_admin",
            "--pwfile",
            pw,
            "--auth-host=scram-sha-256",
            "--auth-local=scram-sha-256",
            "--encoding=UTF8",
            "--locale=C",
            "--data-checksums",
        ],
        timeout=120,
    )
    config = "listen_addresses='127.0.0.1'\nport=55470\nmax_connections=16\nshared_buffers='32MB'\nwork_mem='4MB'\nmax_parallel_workers=0\nmax_wal_size='256MB'\nmin_wal_size='80MB'\ntimezone='UTC'\npassword_encryption='scram-sha-256'\nlogging_collector=off\nlog_statement='none'\nlog_min_error_statement='panic'\n"
    (root / "postgres-data/postgresql.conf").write_text(config, encoding="ascii")
    (root / "postgres-data/pg_hba.conf").write_text(
        "host all all 127.0.0.1/32 scram-sha-256\n", encoding="ascii"
    )
    start_pg(root, initial=True)
    import psycopg
    from psycopg import sql

    try:
        with psycopg.connect(dsn(private, "postgres"), autocommit=True) as c:
            if plain(c.execute("SHOW data_directory").fetchone()[0]) != root / "postgres-data":
                raise ValueError("Wrong isolated cluster")
            c.execute("CREATE DATABASE tester_lab")
            c.execute(
                sql.SQL(
                    "CREATE ROLE tester_lab_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(sql.Literal(private["app_password"]))
            )
        from src.api.db import migrate as platform_migrate
        from src.storage.migrations import migrate

        with psycopg.connect(dsn(private)) as c:
            migrate(c)
        platform_migrate(dsn(private))
        with psycopg.connect(dsn(private)) as c:
            c.execute(
                "CREATE SCHEMA tester_lab; CREATE TABLE tester_lab.projections(record_key text PRIMARY KEY,record jsonb NOT NULL)"
            )
            c.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            c.execute("GRANT CONNECT ON DATABASE tester_lab TO tester_lab_app")
            c.execute(
                "GRANT USAGE ON SCHEMA tester_storage,tester_platform,tester_lab TO tester_lab_app"
            )
            c.execute(
                "GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA tester_storage,tester_platform,tester_lab TO tester_lab_app"
            )
            c.execute(
                "REVOKE INSERT ON tester_storage.schema_migrations,tester_platform.migrations FROM tester_lab_app"
            )
            c.execute(
                "GRANT UPDATE ON tester_storage.download_jobs,tester_storage.ingestion_checkpoints TO tester_lab_app"
            )
            c.execute(
                "GRANT UPDATE,DELETE ON ALL TABLES IN SCHEMA tester_platform TO tester_lab_app"
            )
            c.execute(
                "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA tester_storage,tester_platform,tester_lab TO tester_lab_app"
            )
        verify_pg(root)
        save(
            root / "config.json",
            dict(
                database_url=dsn(private, runtime=True),
                host="127.0.0.1",
                port=18766,
                data_dir=str(root / "runtime"),
                frontend_dir=str(release / "frontend"),
                provider_factory="src.integration.lab_market:create_reader",
                handler_factory="src.integration.lab_market:create_handler",
                min_free_bytes=1024**3,
            ),
        )
        save(
            root / "state/prepared.json",
            dict(
                source_sha=args.sha,
                tree_sha=payload["tree_sha"],
                installation_id=owner["installation_id"],
                status="PREPARED_NOT_SCM",
            ),
        )
    finally:
        stop_pg(root)
    print(
        json.dumps(
            dict(
                status="PREPARED_NOT_SCM",
                source_sha=args.sha,
                root=str(root),
                services=owner["services"],
            )
        )
    )


def start_api(root):
    owner, secret = values(root)
    release = root / "releases" / owner["active_sha"]
    with socket.socket() as s:
        s.bind(("127.0.0.1", 18766))
    env = os.environ.copy()
    env.update(
        TESTER_CONFIG_FILE=str(root / "config.json"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
    )
    env.pop("PYTHONPATH", None)
    log = (root / "runtime/portable-supervisor.log").open("ab")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.Popen(
        [str(root / "dependencies/venv/Scripts/python.exe"), "-B", "-m", "src.api.supervisor"],
        cwd=release,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        creationflags=flags,
    )
    log.close()
    save(
        root / "state" / ("portable-" + str(p.pid) + ".json"),
        dict(pid=p.pid, source_sha=owner["active_sha"]),
    )
    return p


def http(path, body=None):
    from http.client import HTTPConnection

    if not path.startswith("/api/") or "\r" in path or "\n" in path:
        raise ValueError("Only fixed local API paths allowed")
    connection = HTTPConnection("127.0.0.1", 18766, timeout=15)
    try:
        connection.request(
            "GET" if body is None else "POST",
            path,
            body=None if body is None else json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        result = json.loads(response.read(8 * 1024 * 1024))
        if response.status >= 400:
            raise RuntimeError("Local API request failed: HTTP " + str(response.status))
        return result
    finally:
        connection.close()


def wait_ready(read, timeout=40, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            health = read()
            if health["data"]["status"] == "ok":
                return health
        except Exception:
            pass
        sleep(0.5)
    raise RuntimeError("Portable API readiness failed")


def smoke(root):
    verify_pg(root)
    wait_ready(lambda: http("/api/health"))
    now = int(time.time() * 1000) // 60000 * 60000
    end = now - 60000
    start = end - 30 * 60000
    job = http(
        "/api/downloads",
        dict(
            category="linear",
            symbol="BTCUSDT",
            source="bybit",
            kind="candles",
            interval="1",
            start_ms=start,
            end_ms=end,
        ),
    )["data"]
    deadline = time.monotonic() + 150
    while job["state"] not in ("completed", "failed"):
        if time.monotonic() > deadline:
            raise RuntimeError("Bounded public job timeout")
        time.sleep(1)
        job = http("/api/downloads/" + job["id"])["data"]
    if job["state"] != "completed":
        raise RuntimeError("Bybit job failed; inspect redacted worker log")
    result = http(
        "/api/candles?category=linear&symbol=BTCUSDT&interval=1&start_ms="
        + str(start)
        + "&end_ms="
        + str(end)
        + "&limit=1000"
    )
    assert result["data"] and result["coverage"]["status"] == "UNKNOWN"
    assert all(
        r["kind"] == "official_trade" and r["is_closed"] and r["provenance"] for r in result["data"]
    )
    instruments = http("/api/instruments?category=linear&limit=1000")
    assert any(r["symbol"] == "BTCUSDT" for r in instruments["data"])
    # Only a new test-owned credential is created/read/deleted; no existing vault entries read.
    import keyring

    owner, _ = values(root)
    service = "tester-lab-probe-" + owner["installation_id"]
    probe = secrets.token_urlsafe(24)
    keyring.set_password(service, "selftest", probe)
    try:
        assert keyring.get_password(service, "selftest") == probe
    finally:
        keyring.delete_password(service, "selftest")
    report = dict(
        status="PORTABLE_SMOKE_PASS_NOT_SCM",
        source_sha=owner["active_sha"],
        job_id=job["id"],
        candles=len(result["data"]),
        start_ms=start,
        end_ms=end,
        api="http://127.0.0.1:18766",
        coverage="UNKNOWN",
        keyring="OWN_RANDOM_PROBE_PASS_REMOVED",
        real_trading=False,
    )
    path = root / "state" / ("smoke-" + str(time.time_ns()) + ".json")
    save(path, report)
    print(json.dumps(report))


def backup_restore(root):
    import psycopg
    from psycopg import sql

    verify_pg(root)
    owner, secret = values(root)
    stamp = str(time.time_ns())
    backup = root / "backups" / ("tester-lab-" + stamp + ".dump")
    env = os.environ.copy()
    env["PGPASSWORD"] = secret["admin_password"]
    flags = ["-h", "127.0.0.1", "-p", str(PORT), "-U", "tester_lab_admin"]
    command(root, [PGBIN / "pg_dump.exe", *flags, "-Fc", "-f", backup, "tester_lab"], env=env)
    restore = "tester_lab_restore_" + stamp
    with psycopg.connect(dsn(secret, "postgres"), autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(restore)))
    command(
        root, [PGBIN / "pg_restore.exe", *flags, "--exit-on-error", "-d", restore, backup], env=env
    )

    def snapshot(db):
        with psycopg.connect(dsn(secret, db)) as c:
            result = {}
            for schema, table in c.execute(
                "SELECT table_schema,table_name FROM information_schema.tables WHERE table_schema IN ('tester_storage','tester_lab') AND table_type='BASE TABLE' ORDER BY 1,2"
            ).fetchall():
                rows = c.execute(
                    sql.SQL("SELECT * FROM {}.{}").format(
                        sql.Identifier(schema), sql.Identifier(table)
                    )
                ).fetchall()
                lines = sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
                result[schema + "." + table] = dict(
                    rows=len(rows), sha256=hashlib.sha256("\n".join(lines).encode()).hexdigest()
                )
            return result

    before, after = snapshot("tester_lab"), snapshot(restore)
    assert before == after
    report = dict(
        status="OWNED_BACKUP_RESTORE_PASS",
        source_sha=owner["active_sha"],
        database=restore,
        backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
        snapshot=before,
        retained=True,
    )
    save(root / "state" / ("restore-" + stamp + ".json"), report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "database": restore,
                "tables": len(before),
                "data_retained": True,
            }
        )
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "action",
        choices=[
            "prepare",
            "start-pg",
            "stop-pg",
            "start-api",
            "smoke",
            "backup-restore",
            "status",
        ],
    )
    p.add_argument("--root", type=Path, default=Path("C:/TesterLab"))
    p.add_argument("--payload", type=Path)
    p.add_argument("--sha")
    p.add_argument("--wheelhouse", type=Path)
    args = p.parse_args()
    root = plain(args.root)
    if args.action == "prepare":
        bootstrap(args)
    elif args.action == "start-pg":
        start_pg(root)
    elif args.action == "stop-pg":
        stop_pg(root)
    elif args.action == "start-api":
        print(json.dumps({"pid": start_api(root).pid, "kind": "PORTABLE_NOT_SCM"}))
    elif args.action == "smoke":
        smoke(root)
    elif args.action == "backup-restore":
        backup_restore(root)
    else:
        print(json.dumps({"owner": check(root), "kind": "READ_ONLY_STATUS"}))


if __name__ == "__main__":
    main()
