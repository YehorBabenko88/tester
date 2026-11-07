import os
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from src.api.db import migrate
from src.api.jobs import JobQueue
from src.api.models import DownloadRequest


@pytest.fixture
def queue():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Disposable TEST_DATABASE_URL required")
    migrate(dsn)
    with psycopg.connect(dsn) as conn:
        conn.execute("TRUNCATE tester_platform.jobs")
    queue = JobQueue(dsn, max_pending=2)
    queue.set_draining(False)
    yield queue
    queue.set_draining(False)
    queue.configure_limits(256, 2)


def request(i=0):
    return DownloadRequest(
        category="linear",
        symbol=f"TEST{i}USDT",
        source="fixture",
        kind="trades",
        start_ms=0,
        end_ms=60000,
    )


@pytest.mark.integration
def test_atomic_admission_never_exceeds_budget(queue):
    from src.api.jobs import AdmissionRejected

    def submit(i):
        try:
            return queue.create(request(i))
        except AdmissionRejected:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(10)))
    assert sum(result is not None for result in results) == 2
    assert queue.depth() == 2


@pytest.mark.integration
def test_drain_is_shared_and_claims_and_admission_stop(queue):
    from src.api.jobs import AdmissionRejected

    job = queue.create(request())
    peer = JobQueue(queue.dsn)
    queue.set_draining(True)
    assert peer.claim() is None
    with pytest.raises(AdmissionRejected):
        peer.create(request(1))
    assert peer.get(job.id).state == "queued"
    queue.set_draining(False)
    assert peer.claim().id == job.id


@pytest.mark.integration
def test_http_backpressure_has_retry_after_and_drain_blocks(queue, tmp_path):
    from fastapi.testclient import TestClient

    from src.api.config import Settings
    from src.api.main import create_app

    queue.create(request())
    queue.create(request(1))
    with TestClient(
        create_app(Settings(database_url=queue.dsn, data_dir=tmp_path, min_free_bytes=0)),
        base_url="http://127.0.0.1:8765",
    ) as client:
        response = client.post("/api/downloads", json=request(2).model_dump())
        assert response.status_code == 429
        assert response.headers["retry-after"] == "5"
        queue.set_draining(True)
        response = client.post("/api/downloads", json=request(2).model_dump())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "draining"
