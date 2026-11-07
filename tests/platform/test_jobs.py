import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from src.api.db import migrate
from src.api.jobs import Conflict, JobQueue, LeaseLost
from src.api.models import DownloadRequest


@pytest.fixture
def queue():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL must point to a disposable database")
    migrate(dsn)
    migrate(dsn)
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE tester_platform.jobs")
    return JobQueue(dsn)


def request():
    return DownloadRequest(
        category="linear",
        symbol="BTCUSDT",
        source="bybit",
        kind="candles",
        interval="60",
        start_ms=0,
        end_ms=60000,
    )


@pytest.mark.integration
def test_persist_transitions_pause_fences_old_worker_and_retry(queue):
    job = queue.create(request())
    lease = queue.claim()
    assert lease.id == job.id
    queue.transition(job.id, "pause")
    with pytest.raises(LeaseLost):
        queue.checkpoint(job.id, lease.lease_token, {"cursor": 5}, 0.5)
    assert queue.get(job.id).state == "paused"
    queue.transition(job.id, "resume")
    new = queue.claim()
    assert new.lease_token != lease.lease_token
    queue.finish(new.id, new.lease_token, error="provider_unavailable")
    assert queue.get(job.id).state == "failed"
    queue.transition(job.id, "retry")
    assert queue.get(job.id).state == "queued"
    with pytest.raises(Conflict):
        queue.transition(job.id, "retry")


@pytest.mark.integration
def test_claim_is_exclusive_and_recovery_preserves_checkpoint(queue):
    job = queue.create(request())
    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(lambda _: queue.claim(), range(2)))
    assert sum(x is not None for x in leases) == 1
    lease = next(x for x in leases if x)
    queue.checkpoint(job.id, lease.lease_token, {"cursor": 123}, 0.25)
    with psycopg.connect(queue.dsn) as conn:
        conn.execute("UPDATE tester_platform.jobs SET lease_until=now()-interval '1 second'")
    recovered = queue.claim()
    assert recovered.checkpoint == {"cursor": 123}
    assert recovered.attempts == 2
    with pytest.raises(LeaseLost):
        queue.finish(job.id, lease.lease_token)
    queue.finish(job.id, recovered.lease_token)
    assert queue.get(job.id).progress == 1.0
    assert queue.get(job.id).state == "completed"


@pytest.mark.integration
def test_api_job_roundtrip(queue, tmp_path):
    from fastapi.testclient import TestClient

    from src.api.config import Settings
    from src.api.main import create_app

    with TestClient(
        create_app(Settings(database_url=queue.dsn, data_dir=tmp_path)),
        base_url="http://127.0.0.1:8765",
    ) as c:
        result = c.post("/api/downloads", json=request().model_dump())
        assert result.status_code == 202
        job_id = result.json()["data"]["id"]
        assert c.get("/api/downloads/" + job_id).json()["data"]["state"] == "queued"
        assert c.post("/api/downloads/" + job_id + "/pause").status_code == 200
        assert c.post("/api/downloads/" + job_id + "/retry").status_code == 409
        assert c.get("/api/downloads/not-a-uuid").status_code == 422


@pytest.mark.integration
def test_worker_checkpoint_and_graceful_release(queue):
    import logging
    import threading

    from src.api.worker import process_one

    job = queue.create(request())
    stop = threading.Event()

    class InterruptedHandler:
        def run(self, request, checkpoint, report, cancelled, commit):
            report({"cursor": 1}, 0.5)
            stop.set()

    process_one(queue, InterruptedHandler(), stop, logging.getLogger("test"))
    assert queue.get(job.id).state == "queued"
    assert queue.get(job.id).checkpoint == {"cursor": 1}


@pytest.mark.integration
def test_worker_failure_is_persisted_without_secrets(queue):
    import logging
    import threading

    from src.api.worker import process_one

    job = queue.create(request())

    class BrokenHandler:
        def run(self, *args):
            raise RuntimeError("password=never-persist-this")

    process_one(queue, BrokenHandler(), threading.Event(), logging.getLogger("test"))
    assert queue.get(job.id).state == "failed"
    assert queue.get(job.id).error == "handler_failed"


@pytest.mark.integration
def test_provision_never_adopts_existing_database_or_role(queue):
    from src.api.bootstrap import provision

    with pytest.raises(ValueError, match="already exists"):
        provision(
            queue.dsn,
            "tester_acceptance" if "tester_acceptance" in queue.dsn else "tester_ci",
            "tester_must_not_be_created",
        )
    with psycopg.connect(queue.dsn) as conn:
        assert not conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname='tester_must_not_be_created'"
        ).fetchone()


@pytest.mark.integration
def test_revoked_worker_cannot_commit_market_writes(queue):
    job = queue.create(request())
    lease = queue.claim()
    with psycopg.connect(queue.dsn) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS tester_platform.commit_fixture(value integer)")
        conn.execute("TRUNCATE tester_platform.commit_fixture")
    queue.transition(job.id, "pause")
    with pytest.raises(LeaseLost):
        queue.commit(
            job.id,
            lease.lease_token,
            lambda conn: conn.execute("INSERT INTO tester_platform.commit_fixture VALUES(1)"),
            {"cursor": 1},
            0.5,
        )
    with psycopg.connect(queue.dsn) as conn:
        assert (
            conn.execute("SELECT count(*) FROM tester_platform.commit_fixture").fetchone()[0] == 0
        )


@pytest.mark.integration
def test_invalid_bootstrap_does_not_create_database(queue, tmp_path, monkeypatch):
    import sys

    from src.api.bootstrap import main

    monkeypatch.setenv("TESTER_ADMIN_DSN", queue.dsn)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bootstrap",
            "--data-dir",
            str(tmp_path),
            "--database",
            "tester_invalid_port",
            "--role",
            "tester_invalid_port",
            "--port",
            "80",
            "--min-free-bytes",
            "0",
        ],
    )
    with pytest.raises(ValueError):
        main()
    with psycopg.connect(queue.dsn) as conn:
        assert not conn.execute(
            "SELECT 1 FROM pg_database WHERE datname='tester_invalid_port'"
        ).fetchone()
