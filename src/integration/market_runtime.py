"""Operator-only published market lab composition; never installs or kills processes."""

import argparse
import hashlib
import json
import os
import socket
import ssl
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from src.api.lab_install import plain
from src.integration.research_lab import _save, load_lab
from src.integration.research_runtime import (
    ApiOwnershipError,
    _assert_api_birth,
    _wait_for_owned_connection,
    require_published_origin,
)

MODULE = "src.integration.market_runtime"


def canonical_dsn(raw, marker):
    values = conninfo_to_dict(raw)
    if (
        set(values) != {"host", "port", "user", "password", "dbname"}
        or values["host"] != "127.0.0.1"
        or values["port"] != "55471"
        or values["user"] != "research_owner"
        or values["dbname"] != marker["database"]
    ):
        raise ValueError("Fresh owned research credentials required")
    return make_conninfo(**values, hostaddr="127.0.0.1")


def context(root, lab_sha, application_sha):
    import psutil

    source = require_published_origin(Path(__file__).resolve().parents[2], application_sha)
    root = plain(root)
    marker = load_lab(root, lab_sha)
    private = json.loads(plain(root / "private.json").read_text(encoding="utf-8"))
    if "capability" in private or plain(root / "browser-command.json").exists():
        raise ValueError("Previously seeded synthetic root cannot be adopted")
    dsn = canonical_dsn(private["database_url"], marker)
    pid = marker["postgres_pid"]
    process = psutil.Process(pid)
    image = plain(Path(marker["pg_bin"]) / ("postgres.exe" if os.name == "nt" else "postgres"))
    args = process.cmdline()
    if (
        process.create_time() != marker["postgres_created"]
        or plain(process.exe()) != image
        or "-D" not in args
        or plain(args[args.index("-D") + 1]) != root / "pgdata"
    ):
        raise ValueError("Owned PostgreSQL process identity differs")
    with psycopg.connect(dsn, connect_timeout=3) as conn:
        database, port, directory = conn.execute(
            "SELECT current_database(),inet_server_port(),current_setting('data_directory')"
        ).fetchone()
        if database != marker["database"] or port != 55471 or plain(directory) != root / "pgdata":
            raise ValueError("Actual owned PostgreSQL endpoint differs")
    return source, root, marker, dsn


def export_trust(root):
    """Export public certificates into this new root only; never change OS trust."""
    state = plain(root / "state")
    state.mkdir()
    certificates = set()
    for store in ("ROOT", "CA"):
        for certificate, encoding, trust in ssl.enum_certificates(store):
            if encoding == "x509_asn" and (trust is True or "1.3.6.1.5.5.7.3.1" in trust):
                certificates.add(ssl.DER_cert_to_PEM_cert(certificate))
    if not certificates:
        raise ValueError("Windows server-auth trust certificates unavailable")
    bundle = "".join(sorted(certificates)).encode("ascii")
    with plain(state / "windows-ca.pem").open("xb") as stream:
        stream.write(bundle)
    _save(
        state / "tls.json",
        dict(bundle="windows-ca.pem", sha256=hashlib.sha256(bundle).hexdigest()),
        new=True,
    )


def config_for(source, root, dsn):
    return dict(
        database_url=dsn,
        market_catalog_database_url=dsn,
        market_catalog_enabled=True,
        data_dir=str(root / "runtime"),
        frontend_dir=str(source / "frontend"),
        host="127.0.0.1",
        port=18767,
        provider_factory="src.integration.catalog_market:create_reader",
        handler_factory="src.integration.catalog_market:create_handler",
        max_pending=100,
        max_running=1,
        min_free_bytes=256 * 1024**2,
        storage_quota_bytes=1024**3,
    )


def seed(root, lab_sha, application_sha):
    from src.api.catalog_store import migrate_catalog
    from src.api.db import migrate
    from src.storage.migrations import migrate as migrate_storage

    source, root, marker, dsn = context(root, lab_sha, application_sha)
    for name in (
        "market-config.json",
        "market-seed.json",
        "runtime",
        "state",
        ".market-api.json",
        ".market-worker.json",
    ):
        if plain(root / name).exists():
            raise ValueError("Market root already provisioned; adoption refused")
    with psycopg.connect(dsn, connect_timeout=3) as conn:
        existing = conn.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname IN "
            "('tester_platform','tester_storage','tester_lab','tester_catalog','tester_research')"
        ).fetchall()
        if existing:
            raise ValueError("Fresh unseeded owned database required")
    migrate(dsn)
    with psycopg.connect(dsn, connect_timeout=3) as conn:
        migrate_storage(conn)
        conn.execute("CREATE SCHEMA tester_lab")
        conn.execute(
            "CREATE TABLE tester_lab.projections(record_key text PRIMARY KEY,record jsonb NOT NULL)"
        )
    migrate_catalog(dsn)
    export_trust(root)
    plain(root / "runtime").mkdir()
    path = plain(root / "market-config.json")
    _save(path, config_for(source, root, dsn), new=True)
    receipt = dict(
        schema="tester-market-seed/1",
        status="OWNED_MARKET_LAB_SEEDED",
        application_source_sha=application_sha,
        lab_source_sha=lab_sha,
        root=str(root),
        source_root=str(source),
        database=marker["database"],
        postgres_port=55471,
        api_port=18767,
        config_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        coverage="UNKNOWN",
        seeded_market_records=0,
    )
    _save(root / "market-seed.json", receipt, new=True)
    return receipt


def settings_for(source, root, dsn, lab_sha, application_sha):
    from src.api.config import Settings

    receipt = json.loads(plain(root / "market-seed.json").read_text(encoding="utf-8"))
    path = plain(root / "market-config.json")
    if (
        receipt.get("application_source_sha") != application_sha
        or receipt.get("lab_source_sha") != lab_sha
        or receipt.get("root") != str(root)
        or receipt.get("source_root") != str(source)
        or receipt.get("config_sha256") != hashlib.sha256(path.read_bytes()).hexdigest()
    ):
        raise ValueError("Pinned seed/config identity differs")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config != config_for(source, root, dsn):
        raise ValueError("Owned market configuration differs")
    return Settings(**config)


def _record_process(root, name, process, source, lab_sha, application_sha):
    _save(
        root / name,
        dict(
            schema="tester-market-process/1",
            root=str(root),
            source_root=str(source),
            pid=process.pid,
            created=process.create_time(),
            image=process.exe(),
            application_source_sha=application_sha,
            lab_source_sha=lab_sha,
        ),
        new=True,
    )


def stop_worker(worker, runtime, timeout=45):
    """Only cooperative stop plus bounded wait. A stall is a refusal, never a kill."""
    with plain(runtime / "stop.request").open("x", encoding="ascii") as stream:
        stream.write("owned market runtime shutdown\n")
    try:
        return worker.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError(
            "Owned worker did not stop cooperatively; manual operator action required"
        ) from None


def serve(root, lab_sha, application_sha):
    import psutil
    import uvicorn

    from src.api.main import create_app

    source, root, _, dsn = context(root, lab_sha, application_sha)
    settings = settings_for(source, root, dsn, lab_sha, application_sha)
    if plain(Path.cwd()) != source:
        raise ValueError("Serve must start in the exact published source directory")
    for name in (".market-api.json", ".market-worker.json", "runtime/stop.request"):
        if plain(root / name).exists():
            raise ValueError("Runtime process record already exists; restart/adoption refused")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18767))
    os.environ["TESTER_CONFIG_FILE"] = str(root / "market-config.json")
    from src.integration.lab_market import verified_session

    verified_session(root).close()
    os.environ["REQUESTS_CA_BUNDLE"] = str(root / "state/windows-ca.pem")
    app = create_app(settings)
    with plain(root / "market-worker.log").open("x", encoding="utf-8") as log:
        worker = subprocess.Popen(
            [sys.executable, "-m", "src.api.worker"],
            cwd=source,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            _record_process(
                root,
                ".market-worker.json",
                psutil.Process(worker.pid),
                source,
                lab_sha,
                application_sha,
            )
            _record_process(
                root,
                ".market-api.json",
                psutil.Process(os.getpid()),
                source,
                lab_sha,
                application_sha,
            )
            uvicorn.run(app, host="127.0.0.1", port=18767, access_log=False, log_level="warning")
        finally:
            stop_worker(worker, settings.data_dir)


def assert_api_process(root, application_sha, source, lab_sha):
    """Reuse the strict accepted-tuple observer with a fixed market module guard."""
    import psutil

    owner = json.loads(plain(root / ".market-api.json").read_text(encoding="utf-8"))
    if any(
        owner.get(key) != value
        for key, value in dict(
            schema="tester-market-process/1",
            root=str(root),
            source_root=str(source),
            application_source_sha=application_sha,
            lab_source_sha=lab_sha,
        ).items()
    ):
        raise ApiOwnershipError("api_record_mismatch")
    process = psutil.Process(owner["pid"])
    if (
        process.create_time() != owner["created"]
        or plain(process.exe()) != plain(owner["image"])
        or plain(process.cwd()) != source
    ):
        raise ApiOwnershipError("api_process_identity_changed")
    args = process.cmdline()
    expected = {
        "--root": str(root),
        "--lab-source-sha": lab_sha,
        "--application-source-sha": application_sha,
    }
    if "-m" not in args or args[args.index("-m") + 1] != MODULE or "serve" not in args:
        raise ApiOwnershipError("api_process_arguments_mismatch")
    for flag, value in expected.items():
        if flag not in args or args[args.index(flag) + 1] != value:
            raise ApiOwnershipError("api_process_arguments_mismatch")

    def check_birth():
        _assert_api_birth(owner["pid"], owner["created"])

    with socket.create_connection(("127.0.0.1", 18767), timeout=3) as probe:
        observation = _wait_for_owned_connection(process, probe.getsockname(), check_birth)
        check_birth()
    return dict(
        pid=process.pid,
        observer=observation,
        binding="OS image/birth/cwd/fixed-module/accepted-TCP-four-tuple",
    )


def verify(root, lab_sha, application_sha):
    source, root, marker, dsn = context(root, lab_sha, application_sha)
    settings_for(source, root, dsn, lab_sha, application_sha)
    api = assert_api_process(root, application_sha, source, lab_sha)
    worker = assert_worker_process(root, application_sha, source, lab_sha)
    return dict(
        status="OWNED_MARKET_API_VERIFIED",
        application_source_sha=application_sha,
        database=marker["database"],
        postgres_port=55471,
        api_port=18767,
        api=api,
        worker=worker,
    )


def assert_worker_process(root, application_sha, source, lab_sha):
    import psutil

    owner = json.loads(plain(root / ".market-worker.json").read_text(encoding="utf-8"))
    expected = dict(
        schema="tester-market-process/1",
        root=str(root),
        source_root=str(source),
        application_source_sha=application_sha,
        lab_source_sha=lab_sha,
    )
    if any(owner.get(key) != value for key, value in expected.items()):
        raise ValueError("Owned worker record differs")
    process = psutil.Process(owner["pid"])
    if (
        process.create_time() != owner["created"]
        or plain(process.exe()) != plain(owner["image"])
        or plain(process.cwd()) != source
    ):
        raise ValueError("Owned worker process identity differs")
    args = process.cmdline()
    if args[1:] != ["-m", "src.api.worker"] or process.environ().get("TESTER_CONFIG_FILE") != str(
        root / "market-config.json"
    ):
        raise ValueError("Owned worker module/config differs")
    _assert_api_birth(owner["pid"], owner["created"])
    return dict(pid=process.pid, binding="OS image/birth/cwd/fixed-worker-module/config-path")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "serve", "verify"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--lab-source-sha", required=True)
    parser.add_argument("--application-source-sha", required=True)
    args = parser.parse_args()
    try:
        result = globals()[args.action](args.root, args.lab_source_sha, args.application_source_sha)
        if result is not None:
            print(json.dumps(result))
    except Exception as exc:
        result = dict(status="REFUSED_OR_FAILED", error_type=type(exc).__name__)
        if isinstance(exc, ApiOwnershipError):
            result.update(exc.public())
        print(json.dumps(result))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
