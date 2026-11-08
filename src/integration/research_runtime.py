"""Explicit published-only synthetic F/PostgreSQL/G/E/D portable composition.

Not imported by the native service. Does not install, elevate, stop workers or
create a database. The separately published research_lab owns that lifecycle.
"""

import argparse
import asyncio
import json
import os
import re
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from src.api.lab_install import plain
from src.api.research_control import Binding, Command, Denied, Identity
from src.api.research_g_client import ResearchGatewayClient
from src.api.research_ledger import (
    PersistentResearchLedger,
    create_persistent_control_app,
    migrate_research,
    validate_research_dsn,
)
from src.integration.research_lab import _save, load_lab


def require_published_origin(source_root, expected_sha):
    source_root = plain(source_root)
    if not re.fullmatch("[0-9a-f]{40}", expected_sha) or source_root.name != expected_sha:
        raise ValueError("Verified published SHA directory required")
    return source_root


def context(root, lab_sha, application_sha):
    source = require_published_origin(Path(__file__).resolve().parents[2], application_sha)
    root = plain(root)
    marker = load_lab(root, lab_sha)
    private = json.loads(plain(root / "private.json").read_text(encoding="utf-8"))
    endpoint = validate_research_dsn(private["database_url"])
    if (
        endpoint.get("host") != "127.0.0.1"
        or endpoint.get("port") != "55471"
        or endpoint.get("dbname") != marker["database"]
    ):
        raise ValueError("Owned research DSN differs from marker")
    store = PersistentResearchLedger(private["database_url"])
    with store.connection() as conn:
        row = conn.execute(
            "SELECT current_database() AS db,inet_server_port() AS port,"
            "current_setting('data_directory') AS data_directory"
        ).fetchone()
        if (
            row["db"] != marker["database"]
            or row["port"] != 55471
            or plain(row["data_directory"]) != root / "pgdata"
        ):
            raise ValueError("Actual owned PostgreSQL endpoint differs")
    return source, root, marker, private, store


def _connection_belongs(row, client):
    return (
        row.type == socket.SOCK_STREAM
        and row.status == "ESTABLISHED"
        and tuple(row.laddr) == ("127.0.0.1", 18767)
        and tuple(row.raddr) == tuple(client)
    )


def assert_api_process(root, application_sha, source):
    """Prove the owned loopback listener before disclosing its capability.

    API self-reported SHA is checked separately; it cannot authorize credential
    delivery to a different process that merely echoes a public build identity.
    """
    import psutil

    root = plain(root)
    owner = json.loads(plain(root / ".research-api.json").read_text(encoding="utf-8"))
    if (
        owner.get("schema") != "tester-research-api/1"
        or owner.get("root") != str(root)
        or owner.get("application_source_sha") != application_sha
    ):
        raise ValueError("Owned API record differs")
    process = psutil.Process(owner["pid"])
    if (
        process.create_time() != owner["created"]
        or plain(process.exe()) != plain(owner["image"])
        or plain(process.cwd()) != plain(source)
    ):
        raise ValueError("Owned API process changed")
    args = process.cmdline()
    if "-m" not in args or args[args.index("-m") + 1] != "src.integration.research_runtime":
        raise ValueError("Owned API module differs")
    for flag, expected in (
        ("--application-source-sha", application_sha),
        ("--lab-source-sha", owner["lab_source_sha"]),
        ("--root", str(root)),
    ):
        if flag not in args or args[args.index(flag) + 1] != expected:
            raise ValueError("Owned API process arguments differ")
    # On this Windows host the listening row can have an unmapped state NONE.
    # Never accept that row on faith: establish a harmless no-credential TCP
    # connection and require the exact accepted four-tuple in this owned PID.
    with socket.create_connection(("127.0.0.1", 18767), timeout=3) as probe:
        client = probe.getsockname()
        matched = False
        for _ in range(5):
            if any(_connection_belongs(c, client) for c in process.net_connections(kind="tcp")):
                matched = True
                break
            time.sleep(0.02)
        if not matched:
            raise ValueError("Owned API accepted TCP connection differs")
    return dict(
        pid=process.pid,
        created=owner["created"],
        binding="OS process image/creation/cwd/module arguments/accepted TCP four-tuple",
    )


def seed(root, lab_sha, application_sha):
    from src.integration.strategy_lab import run_certified_fixture

    source, root, marker, private, store = context(root, lab_sha, application_sha)
    if plain(root / "browser-command.json").exists() or "capability" in private:
        raise ValueError("Synthetic seed already provisioned; credential adoption refused")
    migrate_research(private["database_url"])
    bundle = run_certified_fixture()  # Actual pinned F, not a successful canned result.
    digest = store.publish_bundle(bundle)  # Independently replays F before persistence.
    actor = "research-" + marker["installation_id"]
    token = store.issue_credential(actor, [digest], ttl_seconds=3600)
    chart = bundle["chart"]
    command = Command(
        **{key: chart[key] for key in Binding.model_fields},
        request_id=str(uuid4()),
        action="chart.overlay",
        expected_artifact_digest=digest,
    )
    private.update(actor=actor, capability=token)
    _save(root / "private.json", private)
    _save(root / "browser-command.json", command.model_dump(mode="json"), new=True)
    receipt = dict(
        status="ACTUAL_F_BUNDLE_PERSISTED",
        application_source_sha=application_sha,
        lab_source_sha=lab_sha,
        engine_source_sha=bundle["source_sha"],
        run_id=chart["run_id"],
        snapshot_id=chart["snapshot_id"],
        chart_digest=digest,
        result_digest=bundle["result_digest"],
        snapshot_digest=bundle["snapshot_digest"],
        candles=len(chart["candles"]),
        fills=len(chart["markers"]),
        synthetic=True,
        execution_mode="SIMULATED",
        real_market_coverage="NOT_PROVEN",
        database=marker["database"],
        postgres_port=55471,
        source_root=str(source),
    )
    _save(root / "seed-receipt.json", receipt, new=True)
    return receipt


def create_app(root, lab_sha, application_sha):
    from fastapi.staticfiles import StaticFiles

    source, root, _, private, store = context(root, lab_sha, application_sha)
    actor = store.authenticate("Bearer " + private["capability"])
    if actor != Identity(private["actor"]):
        raise ValueError("Provisioned actor differs")
    app = create_persistent_control_app(private["database_url"])
    app.title = "Tester isolated synthetic research vertical"
    app.state.worker_state = "STARTING"
    app.state.worker_errors = 0

    async def worker():
        failures = 0
        app.state.worker_state = "RUNNING"
        while True:
            try:
                ids = await asyncio.to_thread(store.pending_job_ids, actor.actor)
                for job_id in ids:
                    await asyncio.to_thread(app.state.research_control.dispatch, actor, job_id)
                failures = 0
            except Denied:
                app.state.worker_state = "AUTHORIZATION_REVOKED_OR_EXPIRED"
                return
            except Exception:
                failures += 1
                app.state.worker_errors += 1
                if failures >= 3:
                    app.state.worker_state = "FAILED"
                    return
            await asyncio.sleep(0.1)

    @asynccontextmanager
    async def lifespan(_):
        task = asyncio.create_task(worker())
        try:
            yield
        finally:
            # Graceful shutdown of this in-process polling task, never a native
            # process kill or installed worker-recovery action.
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.router.lifespan_context = lifespan

    @app.get("/api/research/identity")
    def identity():
        return dict(
            application_source_sha=application_sha,
            lab_source_sha=lab_sha,
            synthetic=True,
            execution_mode="SIMULATED",
            canonical_store="PostgreSQL",
            api_port=18767,
            postgres_port=55471,
            native_service=False,
            worker_scope="one_in_process_polling_task_host_reported",
            worker_state=app.state.worker_state,
            worker_errors=app.state.worker_errors,
        )

    app.mount("/", StaticFiles(directory=source / "frontend", html=True), name="research-terminal")
    return app


def verify(root, lab_sha, application_sha):
    import http.client

    source, root, _, private, store = context(root, lab_sha, application_sha)
    ownership = assert_api_process(root, application_sha, source)
    command = Command.model_validate_json(plain(root / "browser-command.json").read_text())
    connection = http.client.HTTPConnection("127.0.0.1", 18767, timeout=10)
    connection.request("GET", "/api/research/identity")
    response = connection.getresponse()
    identity = json.loads(response.read())
    connection.close()
    if response.status != 200 or identity["application_source_sha"] != application_sha:
        raise ValueError("Running source differs")
    if identity["worker_state"] != "RUNNING":
        raise ValueError("Research worker unavailable")
    denied = ResearchGatewayClient("http://127.0.0.1:18767", "invalid" * 8)
    try:
        denied.research_submit(command, actor=private["actor"])
    except RuntimeError:
        pass
    else:
        raise ValueError("Unauthenticated G command accepted")
    client = ResearchGatewayClient("http://127.0.0.1:18767", private["capability"])
    job = client.research_submit(command, actor=private["actor"])
    replay = client.research_submit(command, actor=private["actor"])
    if job["job_id"] != replay["job_id"]:
        raise ValueError("Idempotency replay differs")
    deadline = time.monotonic() + 30
    while True:
        result = client.research_result(job["job_id"], command)
        if result["state"] == "complete":
            break
        if time.monotonic() >= deadline:
            raise ValueError("Bounded research completion timeout")
        time.sleep(0.1)
    markers = result["artifact"]["markers"]
    fills = [marker["fill_id"] for marker in markers]
    try:
        client.research_ack(job["job_id"], "0" * 64, fills)
    except RuntimeError:
        pass
    else:
        raise ValueError("Wrong digest ACK accepted")
    ack = client.research_ack(job["job_id"], result["artifact_digest"], fills)
    if ack.get("acknowledged") is not True:
        raise ValueError("Exact ACK unavailable")
    events = [event["event"] for event in store.audit(private["actor"])]
    if not {"submitted", "claimed", "completed", "render_ack"}.issubset(events):
        raise ValueError("Canonical persisted audit incomplete")
    return dict(
        status="REAL_G_HTTP_POSTGRES_F_RESULT_PASS",
        process_ownership=ownership,
        identity=identity,
        job_id=job["job_id"],
        artifact_digest=result["artifact_digest"],
        fills=len(fills),
        candles=len(result["artifact"]["candles"]),
        open_entries_without_closed_trade=sum(m["closed_trade_id"] is None for m in markers),
        actor_scoped_audit_events=events,
        invalid_auth_denied=True,
        replay_same_job=True,
        wrong_digest_ack_denied=True,
        exact_fill_ack=True,
        synthetic=True,
        real_market_coverage="NOT_PROVEN",
        browser_rendering="NOT_TESTED_HERE",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "serve", "verify"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--lab-source-sha", required=True)
    parser.add_argument("--application-source-sha", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        parameters = (args.root, args.lab_source_sha, args.application_source_sha)
        if args.action == "serve":
            import psutil
            import uvicorn

            source, root, _, _, _ = context(*parameters)
            app = create_app(*parameters)
            process = psutil.Process(os.getpid())
            _save(
                root / ".research-api.json",
                dict(
                    schema="tester-research-api/1",
                    root=str(root),
                    application_source_sha=args.application_source_sha,
                    lab_source_sha=args.lab_source_sha,
                    pid=process.pid,
                    created=process.create_time(),
                    image=process.exe(),
                ),
            )
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=18767,
                access_log=False,
                log_level="warning",
            )
            return
        receipt = seed(*parameters) if args.action == "seed" else verify(*parameters)
        if args.out:
            _save(args.out, receipt, new=True)
        print(json.dumps(receipt))
    except Exception as exc:
        print(json.dumps(dict(status="REFUSED_OR_FAILED", error_type=type(exc).__name__)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
