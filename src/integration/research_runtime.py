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
from src.integration.runtime_identity import require_runtime_argv


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


_OBSERVER_WAIT_SECONDS = 2.0
_OBSERVER_POLL_SECONDS = 0.05
_SAFE_STATUSES = frozenset(
    {
        "ESTABLISHED",
        "LISTEN",
        "NONE",
        "SYN_SENT",
        "SYN_RECV",
        "FIN_WAIT1",
        "FIN_WAIT2",
        "TIME_WAIT",
        "CLOSE",
        "CLOSED",
        "CLOSE_WAIT",
        "LAST_ACK",
        "CLOSING",
    }
)
_OWNERSHIP_REASONS = frozenset(
    {
        "api_record_mismatch",
        "api_process_identity_changed",
        "api_process_arguments_mismatch",
        "api_process_birth_changed",
        "api_process_unavailable",
        "api_observer_unavailable",
        "api_observer_data_invalid",
        "api_connection_deadline",
        "api_probe_unavailable",
    }
)


def _bounded_count(value):
    return min(max(value, 0), 4096) if type(value) is int else 0


def _safe_observer(value):
    value = value if isinstance(value, dict) else {}
    result = {
        key: _bounded_count(value.get(key, 0))
        for key in (
            "polls",
            "rows_seen",
            "matching_tuple_rows",
            "local_endpoint_rows",
            "elapsed_ms",
        )
    }
    result["samples"] = []
    samples = value.get("samples", [])
    for sample in samples[:8] if isinstance(samples, list) else []:
        if not isinstance(sample, dict):
            continue
        status = sample.get("status")
        kind = sample.get("type_kind")
        code = sample.get("type_code")
        result["samples"].append(
            dict(
                scope="exact_tuple" if sample.get("scope") == "exact_tuple" else "local_endpoint",
                status=status
                if isinstance(status, str) and status in _SAFE_STATUSES
                else "UNRECOGNIZED",
                type_kind=kind
                if isinstance(kind, str) and kind in {"SocketKind", "int", "str"}
                else "other",
                type_code=code if type(code) is int and 0 <= code <= 255 else None,
                count=_bounded_count(sample.get("count", 0)),
            )
        )
    return result


class ApiOwnershipError(ValueError):
    """Only finite reason codes and bounded enum/counter metadata leave the CLI."""

    def __init__(self, reason_code, *, observer=None):
        self.reason_code = (
            reason_code
            if isinstance(reason_code, str) and reason_code in _OWNERSHIP_REASONS
            else "api_observer_data_invalid"
        )
        self.observer = _safe_observer(observer)
        super().__init__("Owned API ownership check refused: " + self.reason_code)

    def public(self):
        return dict(reason_code=self.reason_code, observer=_safe_observer(self.observer))


def _assert_api_birth(pid, created):
    import psutil

    try:
        # Use a fresh Process every time: create_time() on one instance can cache
        # its old birth value and must not authorize a reused PID.
        fresh = psutil.Process(pid)
        if fresh.pid != pid or fresh.create_time() != created:
            raise ApiOwnershipError("api_process_birth_changed")
    except psutil.Error:
        raise ApiOwnershipError("api_process_unavailable") from None


def _observe_rows(evidence, rows, client):
    evidence["polls"] += 1
    evidence["rows_seen"] += len(rows)
    for row in rows:
        if tuple(row.laddr) != ("127.0.0.1", 18767):
            continue
        evidence["local_endpoint_rows"] += 1
        exact = tuple(row.raddr) == tuple(client)
        if exact:
            evidence["matching_tuple_rows"] += 1
        raw = row.type
        sample = dict(
            scope="exact_tuple" if exact else "local_endpoint",
            status=row.status
            if isinstance(row.status, str) and row.status in _SAFE_STATUSES
            else "UNRECOGNIZED",
            type_kind="SocketKind"
            if isinstance(raw, socket.SocketKind)
            else "int"
            if type(raw) is int
            else "str"
            if isinstance(raw, str)
            else "other",
            type_code=int(raw)
            if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= 255
            else None,
        )
        for existing in evidence["samples"]:
            if all(existing[key] == value for key, value in sample.items()):
                existing["count"] += 1
                break
        else:
            if len(evidence["samples"]) < 8:
                evidence["samples"].append(dict(sample, count=1))


def _wait_for_owned_connection(process, client, check_birth):
    """Bound observer retries, never weaken the exact accepted-row predicate.

    The caller holds the same harmless TCP probe open for this entire wait.
    Native snapshot calls remain synchronous; the deadline bounds polling and
    sleeps, rather than promising interruption of an OS call that itself stalls.
    """
    import psutil

    started = time.monotonic()
    deadline = started + _OBSERVER_WAIT_SECONDS
    evidence = dict(
        polls=0, rows_seen=0, matching_tuple_rows=0, local_endpoint_rows=0, elapsed_ms=0, samples=[]
    )
    while True:
        try:
            check_birth()
            rows = process.net_connections(kind="tcp")
            check_birth()
            _observe_rows(evidence, rows, client)
            matched = any(_connection_belongs(row, client) for row in rows)
        except ApiOwnershipError as exc:
            evidence["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            raise ApiOwnershipError(exc.reason_code, observer=evidence) from None
        except (psutil.Error, OSError):
            evidence["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            raise ApiOwnershipError("api_observer_unavailable", observer=evidence) from None
        except (TypeError, AttributeError, ValueError):
            raise ApiOwnershipError("api_observer_data_invalid", observer=evidence) from None
        evidence["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        if matched and time.monotonic() <= deadline:
            return _safe_observer(evidence)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ApiOwnershipError("api_connection_deadline", observer=evidence)
        time.sleep(min(_OBSERVER_POLL_SECONDS, remaining))


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
        raise ApiOwnershipError("api_record_mismatch")
    process = psutil.Process(owner["pid"])
    if (
        process.pid != owner["pid"]
        or process.create_time() != owner["created"]
        or plain(process.exe()) != plain(owner["image"])
        or plain(process.cwd()) != plain(source)
    ):
        raise ApiOwnershipError("api_process_identity_changed")
    try:
        require_runtime_argv(process.cmdline(), root, owner["lab_source_sha"], application_sha)
    except ValueError:
        raise ApiOwnershipError("api_process_arguments_mismatch") from None

    # On this Windows host the listening row can have an unmapped state NONE.
    # Never accept that row on faith: establish a harmless no-credential TCP
    # connection and require the exact accepted four-tuple in this owned PID.
    def check_birth():
        _assert_api_birth(owner["pid"], owner["created"])

    try:
        with socket.create_connection(("127.0.0.1", 18767), timeout=3) as probe:
            observation = _wait_for_owned_connection(process, probe.getsockname(), check_birth)
        try:
            check_birth()
        except ApiOwnershipError as exc:
            raise ApiOwnershipError(exc.reason_code, observer=observation) from None
    except OSError:
        raise ApiOwnershipError("api_probe_unavailable") from None
    return dict(
        pid=process.pid,
        created=owner["created"],
        binding="OS process image/creation/cwd/module arguments/accepted TCP four-tuple",
        observer=observation,
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
        fills_without_closed_trade_id=sum(m["closed_trade_id"] is None for m in markers),
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
        refusal = dict(status="REFUSED_OR_FAILED", error_type=type(exc).__name__)
        if isinstance(exc, ApiOwnershipError):
            refusal.update(exc.public())
        print(json.dumps(refusal))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
