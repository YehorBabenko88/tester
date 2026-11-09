"""Operator-only immutable PC4 market + qualified SYNTHETIC research runtime.

Fresh owned lab only. No native service, process adoption, force kill or elevation.
prepare is explicit DDL; factory/serve never migrate or grant privileges.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from src.api.lab_install import plain
from src.api.research_control import Identity
from src.api.research_execution import ExecutionLedger, compose_execution, create_execution_app
from src.integration import market_runtime
from src.integration.research_lab import _save
from src.integration.research_runtime import (
    ApiOwnershipError,
    _assert_api_birth,
    _wait_for_owned_connection,
)
from src.integration.runtime_identity import require_runtime_argv

MODULE = "src.integration.pc4_runtime"
DML_SCHEMAS = (
    "tester_platform",
    "tester_storage",
    "tester_lab",
    "tester_catalog",
    "tester_research",
)


def research_dsn(canonical_market_dsn):
    """PC4 composition boundary only; retain public research DSN safety policy.

    Market context has already proved this fixed literal endpoint against the
    owned PostgreSQL process/directory. Research deliberately forbids supplied
    hostaddr and re-pins it internally on every connection; remove only that
    redundant field after exact endpoint/scope checks, never from market config.
    """
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from src.api.research_ledger import validate_research_dsn

    values = conninfo_to_dict(canonical_market_dsn)
    if (
        set(values) != {"dbname", "host", "hostaddr", "password", "port", "user"}
        or values["host"] != "127.0.0.1"
        or values["hostaddr"] != values["host"]
        or values["port"] != "55471"
        or values["user"] != "research_owner"
    ):
        raise ValueError("Exact verified PC4 market DSN required")
    del values["hostaddr"]
    result = make_conninfo(**values)
    validate_research_dsn(result)
    return result


def validate_runtime_role(row):
    expected = dict(
        current_user="research_owner",
        session_user="research_owner",
        rolsuper=False,
        rolcreatedb=False,
        rolcreaterole=False,
        rolreplication=False,
        rolbypassrls=False,
        rolcanlogin=True,
        owns_database=False,
        owns_objects=False,
        has_create=False,
        other_membership=False,
        read_settings=True,
    )
    if any(
        row.get(k) != v or (isinstance(v, bool) and row.get(k) is not v)
        for k, v in expected.items()
    ):
        raise ValueError("Limited runtime role required")


def assert_runtime_role(conn):
    row = conn.execute("""SELECT current_user,session_user,r.rolsuper,r.rolcreatedb,
        r.rolcreaterole,r.rolreplication,r.rolbypassrls,r.rolcanlogin,
        (SELECT datdba=r.oid FROM pg_database WHERE datname=current_database()) AS owns_database,
        (EXISTS(SELECT 1 FROM pg_namespace WHERE nspowner=r.oid)
         OR EXISTS(SELECT 1 FROM pg_class WHERE relowner=r.oid)
         OR EXISTS(SELECT 1 FROM pg_proc WHERE proowner=r.oid)
         OR EXISTS(SELECT 1 FROM pg_type WHERE typowner=r.oid)) AS owns_objects,
        (has_database_privilege(current_user,current_database(),'CREATE')
         OR has_database_privilege(current_user,current_database(),'TEMP')
         OR EXISTS(SELECT 1 FROM pg_namespace
            WHERE has_schema_privilege(current_user,oid,'CREATE'))) AS has_create,
        EXISTS(SELECT 1 FROM pg_roles x WHERE x.oid<>r.oid AND x.rolname<>'pg_read_all_settings'
            AND pg_has_role(current_user,x.oid,'MEMBER')) AS other_membership,
        pg_has_role(current_user,'pg_read_all_settings','USAGE') AS read_settings
        FROM pg_roles r WHERE r.rolname=current_user""").fetchone()
    validate_runtime_role(row or {})


def _cluster_identity(conn, root, marker):
    row = conn.execute("""SELECT current_database() AS db,inet_server_port() AS port,
        current_setting('data_directory') AS directory,
        (pg_control_system()).system_identifier::text AS system_identifier""").fetchone()
    if (
        row["db"] != marker["database"]
        or row["port"] != 55471
        or plain(row["directory"]) != root / "pgdata"
    ):
        raise ValueError("Owned PostgreSQL identity differs")
    return row["system_identifier"]


def limit_role(root, lab_sha, app_sha):
    """Explicit owned-cluster operator handoff after all prepare DDL, before serve.

    Config bytes stay unchanged. Retain the old owner OID, but never leave its
    same-password superuser login usable by the new limited runtime principal.
    """
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from psycopg.rows import dict_row

    source, root, marker, settings, _, _ = context(root, lab_sha, app_sha, authenticate=False)
    secret_path = plain(root / "operator-admin-private.json")
    progress_path = plain(root / "pc4-role-progress.json")
    if secret_path.exists() or progress_path.exists() or plain(root / "pc4-role.json").exists():
        raise ValueError("Role handoff already attempted; unsafe resume refused")
    for name in (".pc4-api.json", ".market-api.json", ".market-worker.json"):
        if plain(root / name).exists():
            raise ValueError("Role handoff must precede runtime activation")
    with psycopg.connect(settings.dsn, row_factory=dict_row, connect_timeout=3) as bootstrap:
        system_id = _cluster_identity(bootstrap, root, marker)
        original = bootstrap.execute(
            "SELECT oid,rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if not original or not original["rolsuper"]:
            raise ValueError("Original bootstrap superuser required")
        if bootstrap.execute(
            "SELECT 1 FROM pg_roles WHERE rolname IN ('tester_pc4_admin','tester_pc4_bootstrap')"
        ).fetchone():
            raise ValueError("Reserved operator roles already exist")
        credentials = conninfo_to_dict(settings.dsn)
        admin_dsn = make_conninfo(
            **{**credentials, "user": "tester_pc4_admin", "password": secrets.token_urlsafe(48)}
        )
        state = dict(
            schema="tester-pc4-admin-private/1",
            root=str(root),
            source_root=str(source),
            application_source_sha=app_sha,
            lab_source_sha=lab_sha,
            system_identifier=system_id,
            bootstrap_oid=original["oid"],
            database_url=admin_dsn,
        )
        _save(secret_path, state, new=True)
        progress = dict(
            schema="tester-pc4-role-progress/1",
            root=str(root),
            application_source_sha=app_sha,
            stage="creating_admin",
        )
        _save(progress_path, progress, new=True)
        admin_password = conninfo_to_dict(admin_dsn)["password"]
        bootstrap.execute(
            sql.SQL(
                "CREATE ROLE tester_pc4_admin LOGIN SUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Literal(admin_password))
        )
    progress["stage"] = "handoff"
    _save(progress_path, progress)
    with psycopg.connect(admin_dsn, row_factory=dict_row, connect_timeout=3) as admin:
        if _cluster_identity(admin, root, marker) != system_id:
            raise ValueError("Operator connected to different cluster")
        if admin.execute("SELECT current_user AS name").fetchone()["name"] != "tester_pc4_admin":
            raise ValueError("Operator role identity differs")
        admin.execute("ALTER ROLE research_owner RENAME TO tester_pc4_bootstrap")
        admin.execute("ALTER ROLE tester_pc4_bootstrap NOLOGIN")
        admin.execute(
            sql.SQL(
                "CREATE ROLE research_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Literal(credentials["password"]))
        )
        admin.execute("GRANT pg_read_all_settings TO research_owner")
        db = sql.Identifier(marker["database"])
        admin.execute(sql.SQL("REVOKE CREATE,TEMP ON DATABASE {} FROM PUBLIC").format(db))
        admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO research_owner").format(db))
        admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        for schema in (*DML_SCHEMAS, "tester_snapshots"):
            name = sql.Identifier(schema)
            admin.execute(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM PUBLIC").format(name))
            admin.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO research_owner").format(name))
            permissions = (
                sql.SQL("SELECT")
                if schema == "tester_snapshots"
                else sql.SQL("SELECT,INSERT,UPDATE,DELETE")
            )
            admin.execute(
                sql.SQL("GRANT {} ON ALL TABLES IN SCHEMA {} TO research_owner").format(
                    permissions, name
                )
            )
            if schema != "tester_snapshots":
                admin.execute(
                    sql.SQL(
                        "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {} TO research_owner"
                    ).format(name)
                )
        old = admin.execute(
            "SELECT oid,rolsuper,rolcanlogin FROM pg_roles WHERE rolname='tester_pc4_bootstrap'"
        ).fetchone()
        if old["oid"] != original["oid"] or not old["rolsuper"] or old["rolcanlogin"]:
            raise ValueError("Bootstrap owner handoff differs")
    # Verify using the byte-identical runtime DSN, not the privileged connection.
    with ExecutionLedger(research_dsn(settings.dsn)).connection() as runtime:
        assert_runtime_role(runtime)
        if (
            plain(runtime.execute("SHOW data_directory").fetchone()["data_directory"])
            != root / "pgdata"
        ):
            raise ValueError("Limited connection directory differs")
    progress["stage"] = "ready"
    _save(progress_path, progress)
    receipt = dict(
        schema="tester-pc4-role/1",
        application_source_sha=app_sha,
        root=str(root),
        source_root=str(source),
        system_identifier=system_id,
        bootstrap_login=False,
        runtime_role="research_owner",
        runtime_privileged=False,
        private_sha256=hashlib.sha256(secret_path.read_bytes()).hexdigest(),
    )
    _save(root / "pc4-role.json", receipt, new=True)
    return dict(
        status="LIMITED_RUNTIME_ROLE_VERIFIED", runtime_privileged=False, bootstrap_login=False
    )


def worker_state(state, failures):
    state = state.upper() if isinstance(state, str) else "FAILED"
    return dict(
        state=state if state in {"STARTING", "RUNNING", "STOPPED", "FAILED"} else "FAILED",
        failures=min(max(failures, 0), 3) if type(failures) is int else 3,
    )


def validate_receipt(value, root, source, lab_sha, app_sha, private_hash):
    expected = dict(
        schema="tester-pc4-combined/1",
        application_source_sha=app_sha,
        lab_source_sha=lab_sha,
        root=str(root),
        source_root=str(source),
        private_sha256=private_hash,
        mode="SYNTHETIC_ONLY",
        real_market_coverage="NOT_PROVEN",
        api_port=18767,
        postgres_port=55471,
    )
    if any(value.get(k) != v for k, v in expected.items()) or not re.fullmatch(
        "[a-f0-9]{64}", value.get("input_digest", "")
    ):
        raise ValueError("Pinned combined receipt differs")


def prepare(root, lab_sha, app_sha):
    """Fresh root operator setup only; partial failure remains retained and refused."""
    source, root, _, dsn = market_runtime.context(root, lab_sha, app_sha)
    research = research_dsn(dsn)
    for name in ("pc4-preparing.json", "pc4-seed.json", "execution-private.json"):
        if plain(root / name).exists():
            raise ValueError("Combined preparation already attempted; adoption/resume refused")
    _save(
        root / "pc4-preparing.json",
        dict(schema="tester-pc4-preparing/1", application_source_sha=app_sha),
        new=True,
    )
    # Preserve market_runtime's previous synthetic-root refusal and fresh-DB seed.
    market_runtime.seed(root, lab_sha, app_sha)
    from src.api.research_ledger import migrate_research
    from src.integration.snapshot_repository import QualifiedSnapshotRepository

    migrate_research(research)
    repository = QualifiedSnapshotRepository(research)
    repository.migrate()
    snapshot, strategy = terminal_inputs(repository)
    ledger = ExecutionLedger(research)
    digest = ledger.register(strategy, repository, snapshot.snapshot_id)
    actor = "pc4-synthetic:" + root.name[9:]
    token = ledger.issue_execution_credential(actor, digest, ttl_seconds=3600)
    private = dict(
        schema="tester-pc4-execution-private/1",
        actor=actor,
        token=token,
        input_digest=digest,
        snapshot_id=snapshot.snapshot_id,
        application_source_sha=app_sha,
    )
    # The new lab root already has owner-only ACLs established by research_lab.
    _save(root / "execution-private.json", private, new=True)
    receipt = dict(
        schema="tester-pc4-combined/1",
        application_source_sha=app_sha,
        lab_source_sha=lab_sha,
        root=str(root),
        source_root=str(source),
        private_sha256=hashlib.sha256(
            plain(root / "execution-private.json").read_bytes()
        ).hexdigest(),
        input_digest=digest,
        mode="SYNTHETIC_ONLY",
        real_market_coverage="NOT_PROVEN",
        api_port=18767,
        postgres_port=55471,
    )
    _save(root / "pc4-seed.json", receipt, new=True)
    return {**receipt, "status": "PREPARED_NOT_STARTED", "native_service": False}


def terminal_inputs(repository):
    """Exact separately authored terminal SYNTHETIC source, never relabel spot."""
    from src.integration.qualified_snapshot import certified_terminal_fixture_strategy

    return repository.publish_terminal_fixture(), certified_terminal_fixture_strategy()


def context(root, lab_sha, app_sha, *, authenticate=True):
    source, root, marker, dsn = market_runtime.context(root, lab_sha, app_sha)
    settings = market_runtime.settings_for(source, root, dsn, lab_sha, app_sha)
    path = plain(root / "execution-private.json")
    private = json.loads(path.read_text())
    receipt = json.loads(plain(root / "pc4-seed.json").read_text())
    validate_receipt(
        receipt, root, source, lab_sha, app_sha, hashlib.sha256(path.read_bytes()).hexdigest()
    )
    if (
        private.get("schema") != "tester-pc4-execution-private/1"
        or private.get("application_source_sha") != app_sha
        or private.get("input_digest") != receipt["input_digest"]
        or private.get("actor") != "pc4-synthetic:" + root.name[9:]
    ):
        raise ValueError("Combined private identity differs")
    ledger = ExecutionLedger(research_dsn(dsn))
    if authenticate:
        with ledger.connection() as conn:
            assert_runtime_role(conn)
        actor = ledger.authenticate("Bearer " + private["token"])
        if actor != Identity(private["actor"]):
            raise ValueError("Combined principal differs")
        with ledger.connection() as conn:
            ledger._allowed(conn, actor.actor, private["input_digest"])
    return source, root, marker, settings, private, ledger


def application(settings, root, private, ledger):
    """Explicit combined factory; no setup/DDL, native worker or OS actions here."""
    from src.api.main import create_app

    child = create_execution_app(research_dsn(settings.dsn))
    child.state.pc4_research = worker_state("starting", 0)
    stop = asyncio.Event()

    async def poll():
        errors = 0
        try:
            while not stop.is_set():
                try:
                    jobs = await asyncio.to_thread(ledger.pending_executions, private["actor"])
                    for job in jobs:
                        if stop.is_set():
                            break
                        await asyncio.to_thread(ledger.dispatch_execution, private["actor"], job)
                    # Existing chart jobs still use their reviewed leased dispatch.
                    chart_jobs = await asyncio.to_thread(ledger.pending_job_ids, private["actor"])
                    for job in chart_jobs:
                        if stop.is_set():
                            break
                        await asyncio.to_thread(
                            child.state.research_control.dispatch, Identity(private["actor"]), job
                        )
                    errors = 0
                    child.state.pc4_research = worker_state("running", 0)
                except Exception:
                    errors += 1
                    child.state.pc4_research = worker_state("failed", errors)
                    if errors >= 3:
                        return
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.2)
                except TimeoutError:
                    pass
        finally:
            if errors < 3:
                child.state.pc4_research = worker_state("stopped", errors)

    @asynccontextmanager
    async def lifespan(_):
        task = asyncio.create_task(poll())
        try:
            yield
        finally:
            # Drain the actual synchronous F call; cancelling to_thread would abandon it.
            stop.set()
            done, _ = await asyncio.wait({task}, timeout=70)
            if not done:
                raise ValueError("Research worker did not drain; operator action required")
            await task

    child.router.lifespan_context = lifespan
    app = compose_execution(create_app(settings), child)

    # Insert before main static catch-all, without exposing a capability or DSN.
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def identity(_):
        return JSONResponse(
            dict(
                status="EXPERIMENTAL_PORTABLE",
                native_service=False,
                application_source_sha=private["application_source_sha"],
                mode="SYNTHETIC_ONLY",
                real_market_coverage="NOT_PROVEN",
                research_worker=child.state.pc4_research,
            )
        )

    app.router.routes.insert(0, Route("/api/pc4/identity", identity))
    return app


def assert_api_process(root, source, lab_sha, app_sha):
    import psutil

    owner = json.loads(plain(root / ".pc4-api.json").read_text())
    expected = dict(
        schema="tester-market-process/1",
        root=str(root),
        source_root=str(source),
        application_source_sha=app_sha,
        lab_source_sha=lab_sha,
    )
    if any(owner.get(k) != v for k, v in expected.items()):
        raise ApiOwnershipError("api_record_mismatch")
    process = psutil.Process(owner["pid"])
    if (
        process.create_time() != owner["created"]
        or plain(process.exe()) != plain(owner["image"])
        or plain(process.cwd()) != source
    ):
        raise ApiOwnershipError("api_process_identity_changed")
    try:
        require_runtime_argv(process.cmdline(), root, lab_sha, app_sha, module=MODULE)
    except ValueError:
        raise ApiOwnershipError("api_process_arguments_mismatch") from None
    if process.environ().get("TESTER_CONFIG_FILE") != str(root / "market-config.json"):
        raise ApiOwnershipError("api_process_arguments_mismatch")

    def birth():
        _assert_api_birth(owner["pid"], owner["created"])

    with socket.create_connection(("127.0.0.1", 18767), timeout=3) as probe:
        observation = _wait_for_owned_connection(process, probe.getsockname(), birth)
        birth()
    return dict(
        pid=process.pid,
        observer=observation,
        binding="OS image/birth/cwd/exact-pc4-module/accepted-TCP-four-tuple",
    )


def verify(root, lab_sha, app_sha):
    # Physical ownership remains verifiable after the research credential expires,
    # so expiry cannot prevent an authorized cooperative shutdown.
    source, root, _, _, _, _ = context(root, lab_sha, app_sha, authenticate=False)
    api = assert_api_process(root, source, lab_sha, app_sha)
    worker = market_runtime.assert_worker_process(root, app_sha, source, lab_sha)
    return dict(
        status="OWNED_COMBINED_PROCESSES_VERIFIED",
        api=api,
        worker=worker,
        native_service=False,
        real_market_coverage="NOT_PROVEN",
    )


def shutdown(root, lab_sha, app_sha):
    # An existing marker alone never authorizes a stop signal to a foreign process.
    verify(root, lab_sha, app_sha)
    root = plain(root)
    with plain(root / "pc4-shutdown.request").open("x", encoding="ascii") as stream:
        stream.write("cooperative owned combined shutdown\n")
    return dict(status="COOPERATIVE_SHUTDOWN_REQUESTED", forced=False)


def serve(root, lab_sha, app_sha):
    import psutil
    import uvicorn

    from src.integration.lab_market import verified_session

    source, root, _, settings, private, ledger = context(root, lab_sha, app_sha)
    if plain(Path.cwd()) != source:
        raise ValueError("Serve must start in exact published source")
    for name in (
        ".pc4-api.json",
        ".market-api.json",
        ".market-worker.json",
        "runtime/stop.request",
        "pc4-shutdown.request",
    ):
        if plain(root / name).exists():
            raise ValueError("Process/stop record exists; restart/adoption refused")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18767))
    verified_session(root).close()
    os.environ["TESTER_CONFIG_FILE"] = str(root / "market-config.json")
    os.environ["REQUESTS_CA_BUNDLE"] = str(root / "state/windows-ca.pem")
    app = application(settings, root, private, ledger)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=18767, access_log=False, log_level="warning")
    )
    original_life = app.router.lifespan_context
    worker_failed = False

    @asynccontextmanager
    async def lifespan(app):
        async def watch():
            nonlocal worker_failed
            while not plain(root / "pc4-shutdown.request").exists():
                if worker.poll() is not None:
                    worker_failed = True
                    break
                await asyncio.sleep(0.2)
            server.should_exit = True

        async with original_life(app):
            watcher = asyncio.create_task(watch())
            try:
                yield
            finally:
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass

    app.router.lifespan_context = lifespan
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
            market_runtime._record_process(
                root, ".market-worker.json", psutil.Process(worker.pid), source, lab_sha, app_sha
            )
            market_runtime._record_process(
                root, ".pc4-api.json", psutil.Process(os.getpid()), source, lab_sha, app_sha
            )
            server.run()
        finally:
            market_runtime.stop_worker(worker, settings.data_dir)
        if worker_failed:
            raise ValueError("Owned download worker exited unexpectedly")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "limit-role", "serve", "verify", "shutdown"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--lab-source-sha", required=True)
    parser.add_argument("--application-source-sha", required=True)
    args = parser.parse_args()
    try:
        operation = limit_role if args.action == "limit-role" else globals()[args.action]
        result = operation(args.root, args.lab_source_sha, args.application_source_sha)
        if result is not None:
            print(json.dumps(result))
    except Exception as error:
        print(json.dumps(dict(status="REFUSED_OR_FAILED", error_type=type(error).__name__)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
