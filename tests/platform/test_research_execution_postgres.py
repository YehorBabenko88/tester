"""Hosted-only composed producer/consumer PG gates; no local connection by default."""

import os
from pathlib import Path
from uuid import uuid4

import pytest

from src.api.research_control import Denied, Identity
from src.api.research_execution import ExecutionBusy, ExecutionLedger, ExecutionRequest
from src.api.research_ledger import create_persistent_control, migrate_research


@pytest.fixture
def setup_execution():
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("TESTER_EXECUTION_PG") != "synthetic-disposable"
    ):
        pytest.skip("hosted explicit disposable execution PG gate only")
    dsn = os.environ["TEST_RESEARCH_DATABASE_URL"]
    provider = Path(os.environ["TEST_SNAPSHOT_PROVIDER_ROOT"]).resolve()
    import src.integration

    src.integration.__path__.append(str(provider / "src/integration"))
    from src.integration.snapshot_repository import QualifiedSnapshotRepository

    migrate_research(dsn)
    migrate_research(dsn)
    repository = QualifiedSnapshotRepository(dsn)
    repository.migrate()
    from src.integration.qualified_snapshot import certified_terminal_fixture_strategy

    snapshot = repository.publish_terminal_fixture()
    strategy = certified_terminal_fixture_strategy()
    ledger = ExecutionLedger(dsn)
    digest = ledger.register(strategy, repository, snapshot.snapshot_id)
    actor = "execution-test:" + uuid4().hex
    token = ledger.issue_execution_credential(actor, digest)
    return ledger, repository, strategy, digest, actor, token


@pytest.mark.integration
def test_real_pg_snapshot_to_f_restart_replay_artifact_and_ack(setup_execution):
    ledger, repository, strategy, digest, actor, token = setup_execution
    assert (
        ledger.register(strategy, repository, repository.publish_terminal_fixture().snapshot_id)
        == digest
    )
    request = ExecutionRequest(request_id=str(uuid4()), input_digest=digest)
    job = ledger.submit_execution(actor, request)
    assert ledger.submit_execution(actor, request)["job_id"] == job["job_id"]
    reopened = ExecutionLedger(ledger.dsn)
    completed = reopened.dispatch_execution(actor, job["job_id"])
    assert completed["state"] == "complete"
    summary = reopened.execution_summary(actor, job["job_id"])
    assert summary["summary"]["input_digest"] == digest
    assert (
        summary["summary"]["artifact_digest"]
        == completed["chart_command"]["expected_artifact_digest"]
    )
    assert summary["summary"]["metrics"]["funding_costs"] == "0.0100"
    assert reopened.dispatch_execution(actor, job["job_id"]) == completed
    identity = reopened.authenticate("Bearer " + token)
    assert identity == Identity(actor)
    from src.api.research_control import Command

    command = Command.model_validate(completed["chart_command"])
    control = create_persistent_control(ledger.dsn)
    chart_job = control.submit(identity, command)
    result = control.dispatch(identity, chart_job["job_id"])
    ids = [m["fill_id"] for m in result["artifact"]["markers"]]
    assert ids
    with pytest.raises(ValueError):
        control.ack(identity, chart_job["job_id"], result["artifact_digest"], ids[:-1])
    assert control.ack(identity, chart_job["job_id"], result["artifact_digest"], ids)[
        "acknowledged"
    ]
    assert completed["certification"]["real_market_coverage"] == "NOT_PROVEN"


@pytest.mark.integration
def test_real_pg_artifact_only_credential_cannot_execute_and_capacity_bounded(setup_execution):
    ledger, _, _, digest, actor, _ = setup_execution
    from src.integration.strategy_lab import run_certified_fixture

    artifact_digest = ledger.publish_bundle(run_certified_fixture())
    other = "artifact-only:" + uuid4().hex
    ledger.issue_credential(other, [artifact_digest])
    with pytest.raises(Denied):
        ledger.submit_execution(
            other, ExecutionRequest(request_id=str(uuid4()), input_digest=digest)
        )
    jobs = [
        ledger.submit_execution(
            actor, ExecutionRequest(request_id=str(uuid4()), input_digest=digest)
        )
        for _ in range(4)
    ]
    with pytest.raises(ExecutionBusy):
        ledger.submit_execution(
            actor, ExecutionRequest(request_id=str(uuid4()), input_digest=digest)
        )
    with pytest.raises(Denied):
        ledger.execution_result(other, jobs[0]["job_id"])
    with pytest.raises(Denied):
        ledger.execution_summary(other, jobs[0]["job_id"])
    with pytest.raises(ValueError, match="completed"):
        ledger.execution_summary(actor, jobs[0]["job_id"])
    with ledger.connection() as conn:
        conn.execute("UPDATE tester_research.credentials SET revoked=true WHERE actor=%s", (actor,))
    with pytest.raises(Denied):
        ledger.dispatch_execution(actor, jobs[0]["job_id"])


@pytest.mark.integration
def test_real_pg_lost_lease_cannot_grant_or_complete_then_restart_recovers(
    setup_execution, monkeypatch
):
    ledger, _, _, digest, actor, _ = setup_execution
    job = ledger.submit_execution(
        actor, ExecutionRequest(request_id=str(uuid4()), input_digest=digest)
    )
    original = ledger.publish_bundle

    def publish_then_expire(bundle):
        artifact = original(bundle)
        with ledger.connection() as conn:
            conn.execute(
                "UPDATE tester_research.execution_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                (job["job_id"],),
            )
        return artifact

    monkeypatch.setattr(ledger, "publish_bundle", publish_then_expire)
    with pytest.raises(ValueError, match="lease lost"):
        ledger.dispatch_execution(actor, job["job_id"])
    with ledger.connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM tester_research.grants WHERE actor=%s", (actor,)
            ).fetchone()["n"]
            == 0
        )
        row = conn.execute(
            "SELECT state,chart_command FROM tester_research.execution_jobs WHERE job_id=%s",
            (job["job_id"],),
        ).fetchone()
        assert row["state"] == "running" and row["chart_command"] is None
    reopened = ExecutionLedger(ledger.dsn)
    assert reopened.dispatch_execution(actor, job["job_id"])["state"] == "complete"


@pytest.mark.integration
def test_hosted_actual_g_http_auth_origin_execution_and_chart_ack(setup_execution):
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    from src.api.research_control import Command
    from src.api.research_execution import create_execution_app
    from src.api.research_execution_g import ExecutionGatewayClient

    ledger, _, _, digest, actor, token = setup_execution
    app = create_execution_app(ledger.dsn)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline and thread.is_alive():
            time.sleep(0.02)
        assert server.started
        origin = f"http://127.0.0.1:{port}"
        request = ExecutionRequest(request_id=str(uuid4()), input_digest=digest)
        with httpx.Client(base_url=origin, timeout=3, trust_env=False) as client:
            assert (
                client.post(
                    "/api/control/research-executions", json=request.model_dump()
                ).status_code
                == 401
            )
            assert (
                client.post(
                    "/api/control/research-executions",
                    json=request.model_dump(),
                    headers={
                        "Authorization": "Bearer " + token,
                        "Origin": "http://foreign.invalid",
                    },
                ).status_code
                == 403
            )
        gateway = ExecutionGatewayClient(origin, token)
        job = gateway.execution_submit(request)
        assert gateway.execution_submit(request)["job_id"] == job["job_id"]
        ledger.dispatch_execution(actor, job["job_id"])
        complete = gateway.execution_result(job["job_id"])
        with httpx.Client(base_url=origin, timeout=3, trust_env=False) as client:
            path = "/api/control/research-executions/" + job["job_id"] + "/summary"
            assert client.get(path).status_code == 401
            response = client.get(path, headers={"Authorization": "Bearer " + token})
            assert response.status_code == 200
            summary = response.json()
            from src.api.research_control import canonical_digest

            assert summary["summary_digest"] == canonical_digest(summary["summary"])
            assert summary["summary"]["input_digest"] == digest
        command = Command.model_validate(complete["chart_command"])
        chart_job = gateway.research_submit(command, actor=actor)
        app.state.research_control.dispatch(Identity(actor), chart_job["job_id"])
        result = gateway.research_result(chart_job["job_id"], command)
        ids = [marker["fill_id"] for marker in result["artifact"]["markers"]]
        assert gateway.research_ack(chart_job["job_id"], result["artifact_digest"], ids)[
            "acknowledged"
        ]
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        assert not thread.is_alive(), "hosted test server did not stop"


@pytest.mark.integration
def test_hosted_role_guard_refuses_create_on_unexpected_schema_without_dropping(setup_execution):
    import secrets

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from psycopg.rows import dict_row

    from src.integration.pc4_runtime import assert_runtime_role

    ledger, _, _, _, _, _ = setup_execution
    password = secrets.token_urlsafe(32)
    probe = "tester_guard_probe_" + uuid4().hex
    with ledger.connection() as admin:
        # This workflow owns its fresh service cluster; never adopt an existing role.
        assert not admin.execute("SELECT 1 FROM pg_roles WHERE rolname='research_owner'").fetchone()
        admin.execute(
            sql.SQL(
                "CREATE ROLE research_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Literal(password))
        )
        admin.execute("GRANT pg_read_all_settings TO research_owner")
        db = admin.execute("SELECT current_database() AS db").fetchone()["db"]
        admin.execute(
            sql.SQL("REVOKE CREATE,TEMP ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db))
        )
        admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(probe)))
    dsn = make_conninfo(
        **{**conninfo_to_dict(ledger.dsn), "user": "research_owner", "password": password}
    )
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3) as runtime:
        assert_runtime_role(runtime)  # Prevent a vacuous rejection caused by another flag.
    try:
        with ledger.connection() as admin:
            admin.execute(
                sql.SQL("GRANT CREATE ON SCHEMA {} TO research_owner").format(sql.Identifier(probe))
            )
        with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3) as runtime:
            with pytest.raises(ValueError, match="Limited runtime role"):
                assert_runtime_role(runtime)
    finally:
        with ledger.connection() as admin:
            admin.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA {} FROM research_owner").format(
                    sql.Identifier(probe)
                )
            )
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3) as runtime:
        assert_runtime_role(runtime)
