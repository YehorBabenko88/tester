"""Native optional projection wiring; no producer, trading or startup fixture."""

import logging
import os
import threading
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api import worker
from src.api.config import Settings
from src.api.main import create_app
from src.api.research_control import Identity

DSN = "host=127.0.0.1 port=55827 dbname=tester_research_test user=tester_release"


def test_projection_configuration_is_off_by_default(tmp_path):
    settings = Settings(data_dir=tmp_path)
    assert settings.research_projection_enabled is False and settings.research_dsn is None


def test_enabled_projection_requires_explicit_research_database(tmp_path):
    with pytest.raises(
        ValueError, match="Enabled research projections require explicit research_database_url"
    ):
        Settings(data_dir=tmp_path, research_projection_enabled=True)


@pytest.mark.parametrize(
    "dsn",
    [
        "host=127.0.0.1 port=5432 dbname=tester_research_test",
        "host=foreign port=55827 dbname=tester_research_test",
        "host=127.0.0.1 port=55827 dbname=postgres",
    ],
)
def test_native_projection_refuses_foreign_default_or_unscoped_database(tmp_path, dsn):
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, research_projection_enabled=True, research_database_url=dsn)


def test_native_app_composes_auth_guards_before_static_without_startup_side_effects(
    tmp_path, monkeypatch
):
    from src.api import research_ledger

    def forbidden(*args, **kwargs):
        raise AssertionError("Startup must not connect, migrate, publish or issue credentials")

    monkeypatch.setattr(research_ledger.PersistentResearchLedger, "connection", forbidden)
    monkeypatch.setattr(research_ledger, "migrate_research", forbidden)
    monkeypatch.setattr(research_ledger.PersistentResearchLedger, "publish_bundle", forbidden)
    monkeypatch.setattr(research_ledger.PersistentResearchLedger, "issue_credential", forbidden)
    settings = Settings(
        data_dir=tmp_path,
        research_projection_enabled=True,
        research_database_url=DSN,
        frontend_dir=Path(__file__).resolve().parents[2] / "frontend",
    )
    app = create_app(settings)
    assert app.state.research_composed is True
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        path = "/api/control/research/" + str(uuid4())
        assert client.get("/research.html").status_code == 200
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Origin": "http://foreign.invalid"}).status_code == 403
        assert client.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.post("/api/control/research", json={}).status_code == 401


def test_research_database_unavailable_is_safe_http_503(tmp_path, monkeypatch):
    import psycopg

    from src.api.research_ledger import PersistentResearchLedger

    def unavailable(*args):
        raise psycopg.OperationalError("connection secret must never reach HTTP")

    monkeypatch.setattr(PersistentResearchLedger, "connection", unavailable)
    settings = Settings(
        data_dir=tmp_path, research_projection_enabled=True, research_database_url=DSN
    )
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        reply = client.get(
            "/api/control/research/" + str(uuid4()), headers={"Authorization": "Bearer " + "a" * 40}
        )
        assert reply.status_code == 503
        assert "tester_release" not in reply.text and "55827" not in reply.text
        assert "connection secret" not in reply.text


def test_idle_projection_worker_reuses_existing_control_dispatch():
    stop = threading.Event()
    control = Mock()
    job_id = str(uuid4())
    control.store.next_projection_job.return_value = {
        "actor": "approved-actor",
        "job_id": job_id,
        "attempts": 0,
    }
    assert worker.process_one_projection(control, stop, logging.getLogger("test")) is True
    control.dispatch.assert_called_once_with(Identity("approved-actor"), job_id)


def test_projection_worker_stop_and_database_failure_do_not_escape():
    stop = threading.Event()
    stop.set()
    control = Mock()
    assert worker.process_one_projection(control, stop, logging.getLogger("test")) is False
    control.store.next_projection_job.assert_not_called()
    stop.clear()
    control.store.next_projection_job.side_effect = RuntimeError("do not log connection secrets")
    assert worker.process_one_projection(control, stop, logging.getLogger("test")) is False
    control.dispatch.assert_not_called()


def test_continuous_market_backlog_keeps_priority_and_projection_progress(monkeypatch):
    order = []
    market = Mock(side_effect=lambda *args: order.append("market") or True)
    projection = Mock(side_effect=lambda *args: order.append("projection") or True)
    monkeypatch.setattr(worker, "process_one", market)
    monkeypatch.setattr(worker, "process_one_projection", projection, raising=False)
    for _ in range(10):
        assert (
            worker.process_available_work(Mock(), Mock(), Mock(), threading.Event(), Mock()) is True
        )
    assert projection.call_count == 10 and order == ["market", "projection"] * 10


def test_stop_after_market_work_prevents_projection_delegation(monkeypatch):
    stop = threading.Event()

    def market(*args):
        stop.set()
        return True

    projection = Mock()
    monkeypatch.setattr(worker, "process_one", market)
    monkeypatch.setattr(worker, "process_one_projection", projection)
    assert worker.process_available_work(Mock(), Mock(), Mock(), stop, Mock()) is True
    projection.assert_not_called()


def test_projection_outage_does_not_hide_completed_market_work(monkeypatch):
    monkeypatch.setattr(worker, "process_one", Mock(return_value=True))
    monkeypatch.setattr(worker, "process_one_projection", Mock(return_value=False))
    assert worker.process_available_work(Mock(), Mock(), Mock(), threading.Event(), Mock()) is True


@pytest.fixture
def persisted_projection():
    from src.api.research_control import Command
    from src.api.research_ledger import (
        PersistentResearchLedger,
        create_persistent_control,
        validate_research_dsn,
    )
    from src.integration.strategy_lab import run_certified_fixture

    dsn = os.environ.get("TEST_RELEASE_RESEARCH_DSN")
    owner_dsn = os.environ.get("TEST_RELEASE_RESEARCH_OWNER_DSN")
    if not dsn or not owner_dsn:
        pytest.skip(
            "Explicit disposable release research App/owner DSNs on reserved ports required"
        )
    for value in (dsn, owner_dsn):
        endpoint = validate_research_dsn(value)
        if not 55820 <= int(endpoint["port"]) <= 55827:
            raise ValueError("Only reserved disposable PostgreSQL ports allowed")
    owner = PersistentResearchLedger(owner_dsn)
    # Explicit test-only operator publication; product startup never invokes this producer.
    bundle = run_certified_fixture()
    digest = owner.publish_bundle(bundle)
    actor = "projection:" + uuid4().hex
    token = owner.issue_credential(actor, [digest], ttl_seconds=600)
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
    control = create_persistent_control(dsn)
    return owner, control, Identity(actor), token, command, chart


@pytest.mark.integration
def test_actual_native_projection_http_worker_result_ack_and_acl(persisted_projection, tmp_path):
    owner, control, identity, token, command, chart = persisted_projection
    settings = Settings(
        data_dir=tmp_path, research_projection_enabled=True, research_database_url=control.store.dsn
    )
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        auth = {"Authorization": "Bearer " + token}
        body = command.model_dump(mode="json")
        assert client.post("/api/control/research", json=body).status_code == 401
        reply = client.post("/api/control/research", json=body, headers=auth)
        assert reply.status_code == 202
        job_id = reply.json()["job_id"]
        assert worker.process_one_projection(control, threading.Event(), logging.getLogger("test"))
        result = client.get("/api/control/research/" + job_id, headers=auth).json()
        assert result["state"] == "complete" and result["artifact"] == chart
        fills = [m["fill_id"] for m in chart["markers"]]
        assert (
            client.post(
                "/api/control/research/" + job_id + "/ack",
                headers=auth,
                json={
                    "artifact_digest": command.expected_artifact_digest,
                    "rendered_fill_ids": fills[:-1],
                },
            ).status_code
            == 409
        )
        ack = client.post(
            "/api/control/research/" + job_id + "/ack",
            headers=auth,
            json={"artifact_digest": command.expected_artifact_digest, "rendered_fill_ids": fills},
        )
        assert ack.status_code == 200 and ack.json()["acknowledged"] is True
        other = owner.issue_credential(
            "projection-other:" + uuid4().hex, [command.expected_artifact_digest]
        )
        assert (
            client.get(
                "/api/control/research/" + job_id, headers={"Authorization": "Bearer " + other}
            ).status_code
            == 403
        )
        with control.store.connection() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM tester_research.acknowledgments WHERE job_id=%s",
                    (job_id,),
                ).fetchone()["count"]
                == 1
            )
        owner.revoke(identity.actor)
        assert client.get("/api/control/research/" + job_id, headers=auth).status_code == 401


@pytest.mark.integration
def test_projection_claims_are_durably_bounded_and_old_lease_fenced(persisted_projection):
    owner, control, identity, token, command, chart = persisted_projection
    job = control.submit(identity, command)
    first = None
    for _ in range(3):
        lease = control.store.claim(identity.actor, job["job_id"])
        assert lease is not None
        first = first or lease["lease_token"]
        with owner.connection() as conn:
            conn.execute(
                "UPDATE tester_research.jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                (job["job_id"],),
            )
    assert control.store.claim(identity.actor, job["job_id"]) is None
    assert job["job_id"] not in control.store.pending_job_ids(identity.actor)
    with pytest.raises(ValueError, match="lease lost"):
        control.store.complete_leased(identity.actor, job["job_id"], first)
    with owner.connection() as conn:
        assert (
            conn.execute(
                "SELECT attempts FROM tester_research.jobs WHERE job_id=%s", (job["job_id"],)
            ).fetchone()["attempts"]
            == 3
        )


@pytest.mark.integration
def test_malformed_persisted_projection_exhausts_budget_without_worker_loop(persisted_projection):
    owner, control, identity, token, command, chart = persisted_projection
    job = control.submit(identity, command)
    with owner.connection() as conn:
        conn.execute(
            "UPDATE tester_research.jobs SET digest=%s WHERE job_id=%s", ("f" * 64, job["job_id"])
        )
    for _ in range(3):
        assert not worker.process_one_projection(
            control, threading.Event(), logging.getLogger("test")
        )
    assert control.store.next_projection_job() is None
    with owner.connection() as conn:
        row = conn.execute(
            "SELECT attempts,state,artifact_digest FROM tester_research.jobs WHERE job_id=%s",
            (job["job_id"],),
        ).fetchone()
        assert row["attempts"] == 3 and row["state"] == "pending" and row["artifact_digest"] is None


@pytest.mark.integration
def test_revoked_or_expired_principals_are_not_selected_by_native_worker(persisted_projection):
    owner, control, identity, token, command, chart = persisted_projection
    job = control.submit(identity, command)
    with owner.connection() as conn:
        conn.execute(
            "UPDATE tester_research.credentials SET expires_at=clock_timestamp()-interval '1 second' WHERE actor=%s",
            (identity.actor,),
        )
    assert control.store.next_projection_job() is None
    assert not worker.process_one_projection(control, threading.Event(), logging.getLogger("test"))
    with owner.connection() as conn:
        assert (
            conn.execute(
                "SELECT attempts FROM tester_research.jobs WHERE job_id=%s", (job["job_id"],)
            ).fetchone()["attempts"]
            == 0
        )


@pytest.mark.integration
def test_two_idle_dispatchers_publish_one_fenced_projection(persisted_projection):
    from concurrent.futures import ThreadPoolExecutor

    owner, control, identity, token, command, chart = persisted_projection
    job = control.submit(identity, command)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                lambda _: worker.process_one_projection(
                    control, threading.Event(), logging.getLogger("test")
                ),
                range(2),
            )
        )
    result = control.result(identity, job["job_id"])
    assert result["state"] == "complete"
    with owner.connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tester_research.audit WHERE job_id=%s AND event='completed'",
                (job["job_id"],),
            ).fetchone()["count"]
            == 1
        )


@pytest.mark.integration
def test_real_market_queue_work_precedes_persisted_projection(persisted_projection):
    from src.api.jobs import JobQueue
    from src.api.models import DownloadRequest

    owner, control, identity, token, command, chart = persisted_projection
    main_dsn = os.environ.get("TEST_RELEASE_PLATFORM_DSN")
    if not main_dsn:
        pytest.skip("Explicit disposable platform DSN required")
    queue = JobQueue(main_dsn)
    job = queue.create(
        DownloadRequest(
            category="linear",
            symbol="BTCUSDT",
            source="bybit",
            kind="candles",
            interval="1",
            start_ms=0,
            end_ms=60000,
        )
    )
    projection = control.submit(identity, command)

    class ExplicitTestHandler:
        def run(self, request, checkpoint, report, stop, commit):
            assert control.result(identity, projection["job_id"])["state"] == "pending"
            report({"fixture": "priority-only-no-market-acquisition"}, 1)

    assert worker.process_available_work(
        queue, ExplicitTestHandler(), control, threading.Event(), logging.getLogger("test")
    )
    assert queue.get(job.id).state == "completed"
    assert control.result(identity, projection["job_id"])["state"] == "complete"


@pytest.mark.parametrize("port", ["05432", "0005432", "0", "65536", "abc", "55824,55825"])
def test_research_constructor_requires_bounded_numeric_nondefault_port(monkeypatch, port):
    from src.api import research_ledger

    connect = Mock(side_effect=AssertionError("No database connection permitted"))
    monkeypatch.setattr(research_ledger.psycopg, "connect", connect)
    with pytest.raises(ValueError):
        research_ledger.validate_research_dsn(
            f"host=127.0.0.1 port={port} dbname=tester_research_test"
        )
    connect.assert_not_called()
