import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.config import Settings
from src.api.jobs import JobQueue
from src.api.main import create_app


def test_native_health_rejects_fresh_previous_instance(monkeypatch, tmp_path):
    import src.api.main as main

    rows = {"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}

    class Connection:
        def execute(self, sql, params=None):
            self.alive = bool(rows) if not params else params[0] in rows
            return self

        def fetchone(self):
            return (self.alive,)

    @contextmanager
    def connect(_):
        yield Connection()

    monkeypatch.setattr(main, "connect", connect)
    monkeypatch.setenv("TESTER_SERVICE_INSTANCE", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    app = create_app(
        Settings(data_dir=tmp_path, database_url="postgresql://tester_app@127.0.0.1:55824/tester")
    )
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/health").json()["data"]["worker"] == "unavailable"
        rows.add("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        assert client.get("/api/health").json()["data"]["worker"] == "ok"


def test_heartbeat_persists_instance_and_legacy_null(monkeypatch, tmp_path):
    dsn = os.getenv("TEST_RELEASE_PLATFORM_DSN")
    if not dsn:
        pytest.skip("TEST_RELEASE_PLATFORM_DSN requires a disposable migrated database")
    from src.api.db import connect

    queue = JobQueue(dsn)
    worker = uuid4()
    instance = uuid4().hex
    monkeypatch.setenv("TESTER_SERVICE_INSTANCE", instance)
    client = TestClient(
        create_app(Settings(data_dir=tmp_path, database_url=dsn)), base_url="http://127.0.0.1:8765"
    )
    try:
        queue.heartbeat(worker, uuid4().hex)
        assert client.get("/api/health").json()["data"]["worker"] == "unavailable"
        queue.heartbeat(worker, instance)
        assert client.get("/api/health").json()["data"]["worker"] == "ok"
        with connect(dsn) as conn:
            assert conn.execute(
                "SELECT service_instance FROM tester_platform.workers WHERE id=%s", (worker,)
            ).fetchone() == (instance,)
        queue.heartbeat(worker)
        with connect(dsn) as conn:
            assert conn.execute(
                "SELECT service_instance FROM tester_platform.workers WHERE id=%s", (worker,)
            ).fetchone() == (None,)
        monkeypatch.delenv("TESTER_SERVICE_INSTANCE")
        legacy = TestClient(
            create_app(Settings(data_dir=tmp_path, database_url=dsn)),
            base_url="http://127.0.0.1:8765",
        )
        assert legacy.get("/api/health").json()["data"]["worker"] == "ok"
    finally:
        with connect(dsn) as conn:
            conn.execute("DELETE FROM tester_platform.workers WHERE id=%s", (worker,))
