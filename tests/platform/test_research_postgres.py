"""Optional real PostgreSQL research tests; never use the installed application DB."""

import os
from uuid import uuid4

import pytest

from src.api.research_control import Command, Denied, Identity, canonical_digest
from src.api.research_ledger import (
    PersistentResearchLedger,
    migrate_research,
    validate_research_dsn,
)


def test_research_dsn_refuses_default_or_foreign_databases():
    for dsn in [
        "dbname=postgres",
        "host=127.0.0.1 port=5432 dbname=tester_research_test",
        "host=foreign port=55433 dbname=tester_research_test",
        "host=127.0.0.1 port=55433 dbname=tester_native",
    ]:
        with pytest.raises(ValueError):
            validate_research_dsn(dsn)
    validate_research_dsn("host=127.0.0.1 port=55433 dbname=tester_research_test")


@pytest.fixture
def persistent():
    dsn = os.environ.get("TEST_RESEARCH_DATABASE_URL")
    if not dsn:
        pytest.skip("explicit disposable TEST_RESEARCH_DATABASE_URL required (non-5432)")
    migrate_research(dsn)
    migrate_research(dsn)
    return PersistentResearchLedger(dsn)


@pytest.mark.integration
def test_postgres_publish_auth_lease_restart_result_ack(persistent):
    # Integration runner supplies real J F bundle via JSON file, never mock artifacts.
    import json
    from pathlib import Path

    from src.api.research_ledger import create_persistent_control

    path = os.environ.get("TEST_RESEARCH_BUNDLE_FILE")
    if path:
        bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    else:
        from src.integration.strategy_lab import run_certified_fixture

        bundle = run_certified_fixture()
    digest = persistent.publish_bundle(bundle)
    actor = "test:" + uuid4().hex
    token = persistent.issue_credential(actor, [digest], ttl_seconds=300)
    identity = persistent.authenticate("Bearer " + token)
    chart = bundle["chart"]
    command = Command.model_validate(
        {
            **{
                k: chart[k]
                for k in (
                    "run_id",
                    "snapshot_id",
                    "revision",
                    "venue",
                    "category",
                    "symbol",
                    "interval",
                    "start_ms",
                    "end_ms",
                    "cutoff_ms",
                )
            },
            "request_id": str(uuid4()),
            "action": "chart.overlay",
            "expected_artifact_digest": digest,
        }
    )
    control = create_persistent_control(persistent.dsn)
    job = control.submit(identity, command)
    assert control.submit(identity, command)["job_id"] == job["job_id"]
    with pytest.raises(ValueError):
        control.submit(identity, command.model_copy(update={"symbol": "ETHUSDT"}))
    lease = persistent.claim(actor, job["job_id"])
    assert lease is not None
    assert persistent.claim(actor, job["job_id"]) is None
    # Simulate a killed worker by expiring its lease via controlled test SQL.
    with persistent.connection() as conn:
        conn.execute(
            "UPDATE tester_research.jobs SET lease_until=now()-interval '1 second' WHERE job_id=%s",
            (job["job_id"],),
        )
    restarted = create_persistent_control(persistent.dsn)
    import subprocess
    import sys

    process_env = {
        **os.environ,
        "TEST_RESEARCH_ACTOR": actor,
        "TEST_RESEARCH_JOB_ID": job["job_id"],
    }
    subprocess.run(
        [sys.executable, "-m", "src.api.research_worker", job["job_id"], "--actor", actor],
        env=process_env,
        check=True,
        capture_output=True,
        text=True,
    )
    readback = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,json;from src.api.research_ledger import create_persistent_control;"
            "from src.api.research_control import Identity;"
            "print(json.dumps(create_persistent_control(os.environ['TEST_RESEARCH_DATABASE_URL']).result("
            "Identity(os.environ['TEST_RESEARCH_ACTOR']),os.environ['TEST_RESEARCH_JOB_ID'])))",
        ],
        env=process_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(readback.stdout)["artifact"] == chart
    with pytest.raises(ValueError):
        persistent.complete_leased(actor, job["job_id"], lease["lease_token"])
    result = restarted.result(identity, job["job_id"])
    assert result["artifact"] == chart
    assert canonical_digest(chart) == result["artifact_digest"]
    fills = [m["fill_id"] for m in chart["markers"]]
    restarted.ack(identity, job["job_id"], digest, fills)
    restarted.ack(identity, job["job_id"], digest, fills)
    with pytest.raises(ValueError):
        restarted.ack(identity, job["job_id"], digest, [])
    with pytest.raises(Denied):
        restarted.result(Identity(actor="other"), job["job_id"])
    assert [e["event"] for e in persistent.audit(actor)].count("render_ack") == 1
    persistent.revoke(actor)
    with pytest.raises(Denied):
        persistent.authenticate("Bearer " + token)
    with pytest.raises(Denied):
        restarted.result(identity, job["job_id"])


def test_g_sources_are_exact_declared_pin():
    import hashlib
    import subprocess
    from pathlib import Path

    pin = "5edb722fc936a56e81a174cfe654839de9a19a9d"
    for name in ("commands.py", "contracts.py", "types.py", "bridge.py"):
        path = "src/control/" + name
        expected = subprocess.check_output(["git", "show", pin + ":" + path])
        assert hashlib.sha256(Path(path).read_bytes()).digest() == hashlib.sha256(expected).digest()


def test_g_research_binding_rejects_foreign_transport():
    from src.api.research_g_client import ResearchGatewayClient

    for url in (
        "https://127.0.0.1:18767",
        "http://example.com:18767",
        "http://127.0.0.1:18767/path",
    ):
        with pytest.raises(ValueError):
            ResearchGatewayClient(url, "x" * 43)


@pytest.mark.integration
def test_actual_g_http_postgres_roundtrip(persistent):
    import socket
    import threading
    import time

    import uvicorn
    from src.integration.strategy_lab import run_certified_fixture

    from src.api.research_g_client import ResearchGatewayClient
    from src.api.research_ledger import create_persistent_control_app

    bundle = run_certified_fixture()
    digest = persistent.publish_bundle(bundle)
    actor = "http:" + uuid4().hex
    token = persistent.issue_credential(actor, [digest], ttl_seconds=300)
    app = create_persistent_control_app(persistent.dsn)
    command = Command.model_validate(
        {
            **{
                k: bundle["chart"][k]
                for k in (
                    "run_id",
                    "snapshot_id",
                    "revision",
                    "venue",
                    "category",
                    "symbol",
                    "interval",
                    "start_ms",
                    "end_ms",
                    "cutoff_ms",
                )
            },
            "request_id": str(uuid4()),
            "action": "chart.overlay",
            "expected_artifact_digest": digest,
        }
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        client = ResearchGatewayClient(f"http://127.0.0.1:{port}", token)
        job = client.research_submit(command, actor=actor)
        app.state.research_control.dispatch(Identity(actor), job["job_id"])
        result = client.research_result(job["job_id"], command)
        assert result["artifact"] == bundle["chart"]
        ids = [m["fill_id"] for m in result["artifact"]["markers"]]
        assert client.research_ack(job["job_id"], digest, ids)["acknowledged"]
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
