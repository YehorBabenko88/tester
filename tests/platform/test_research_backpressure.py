"""Real E API/queue acceptance; only an explicitly selected disposable research DB.

Default runs never connect. No TEST_DATABASE_URL fallback, truncation, service
operations, market handler, exchange access or installed lab endpoint is used.
"""

import pytest


@pytest.mark.parametrize(
    "dsn",
    [
        "host=127.0.0.1 port=5432 dbname=tester_research_i",
        "host=127.0.0.1 port=55470 dbname=tester_research_i",
        "host=example.com port=55471 dbname=tester_research_i",
        "host=127.0.0.1 port=55471 dbname=tester_lab",
        "host=127.0.0.1 port=55471 dbname=tester_research_",
        "host=127.0.0.1 port=55471 dbname=tester_research_i hostaddr=192.0.2.1",
        "service=research host=127.0.0.1 port=55471 dbname=tester_research_i",
        "host=127.0.0.1 dbname=tester_research_i",
    ],
)
def test_research_dsn_refuses_unsafe_targets_before_connect(dsn):
    with pytest.raises(ValueError, match="research"):
        research_target(dsn)


def test_research_dsn_accepts_explicit_disposable_loopback_target():
    assert research_target("host=127.0.0.1 port=55471 dbname=tester_research_i") == (
        "tester_research_i",
        55471,
    )


def research_target(dsn):
    import re

    from psycopg.conninfo import conninfo_to_dict

    try:
        parts = conninfo_to_dict(dsn)
        port = int(parts.get("port", "0"))
        database = parts.get("dbname", "")
        if (
            parts.get("host") not in {"127.0.0.1", "::1"}
            or not 1024 <= port <= 65535
            or port in {5432, 55470}
            or not re.fullmatch(r"tester_research_[a-z0-9_]+", database)
            or any(parts.get(key) for key in ("hostaddr", "service", "options"))
        ):
            raise ValueError
    except Exception:
        raise ValueError("Explicit disposable research loopback DSN required") from None
    return database, port


def research_connect(dsn):
    import psycopg

    return psycopg.connect(
        dsn, connect_timeout=3, options="-c statement_timeout=5000 -c lock_timeout=1000"
    )


@pytest.fixture
def research_db():
    import os

    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from src.api.db import migrate

    raw = os.environ.get("TEST_RESEARCH_DATABASE_URL")
    if not raw:
        pytest.skip("Explicit disposable TEST_RESEARCH_DATABASE_URL required; no fallback")
    database, port = research_target(raw)
    # Pin the numeric destination as well as host to override PGHOSTADDR defaults.
    dsn = make_conninfo(raw, hostaddr=conninfo_to_dict(raw)["host"])
    with research_connect(dsn) as guard:
        actual = guard.execute(
            "SELECT current_database(),inet_server_port(),inet_server_addr()::text"
        ).fetchone()
        assert actual == (database, port, conninfo_to_dict(raw)["host"]), (
            "Research endpoint mismatch"
        )
        # Cooperating acceptance runs fail immediately instead of sharing limits.
        assert guard.execute("SELECT pg_try_advisory_lock(724913501)").fetchone()[0], (
            "Research backpressure test already running"
        )
        guard.commit()
        migrate(dsn)
        with research_connect(dsn) as conn:
            baseline = conn.execute("SELECT * FROM tester_platform.jobs ORDER BY id").fetchall()
            assert not any(row[2] in {"queued", "running", "paused"} for row in baseline), (
                "Research queue must start idle; existing jobs are preserved"
            )
            control = conn.execute(
                "SELECT draining,max_pending,max_running FROM tester_platform.control WHERE singleton"
            ).fetchone()
        owned = set()
        try:
            yield dsn, baseline, owned
        finally:
            # Never truncate or change pre-existing jobs. Remove only acknowledged test IDs.
            with research_connect(dsn) as conn:
                if owned:
                    conn.execute(
                        "DELETE FROM tester_platform.jobs WHERE id=ANY(%s)", (list(owned),)
                    )
                conn.execute(
                    "UPDATE tester_platform.control SET draining=%s,max_pending=%s,max_running=%s WHERE singleton",
                    control,
                )
                remaining = conn.execute(
                    "SELECT * FROM tester_platform.jobs ORDER BY id"
                ).fetchall()
            assert remaining == baseline, "Existing research jobs changed"


@pytest.mark.integration
def test_real_postgres_canonical_api_backpressure_preserves_jobs(research_db, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import UUID, uuid4

    from fastapi.testclient import TestClient

    from src.api.config import Settings
    from src.api.jobs import JobQueue
    from src.api.main import create_app

    dsn, baseline, owned = research_db
    settings = Settings(
        database_url=dsn,
        data_dir=tmp_path,
        min_free_bytes=0,
        max_pending=2,
        max_running=1,
        provider_factory=None,
        handler_factory=None,
    )
    queue = JobQueue(dsn, lease_seconds=600, max_pending=2)
    queue.configure_limits(settings.max_pending, settings.max_running)
    queue.set_draining(False)
    tag = uuid4().hex[:8].upper()

    def body(index):
        return dict(
            category="linear",
            symbol=f"I{tag}{index}USDT",
            source="fixture",
            kind="trades",
            start_ms=0,
            end_ms=60000,
        )

    def rows():
        with research_connect(dsn) as conn:
            return conn.execute("SELECT * FROM tester_platform.jobs ORDER BY id").fetchall()

    def admitted(response):
        assert response.status_code == 202, response.text
        ident = UUID(response.json()["data"]["id"])
        owned.add(ident)
        return ident

    # TestClient dispatches the actual canonical FastAPI app, without a listener,
    # mocked queue, background worker, handler, exchange request or process action.
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        sentinel = admitted(client.post("/api/downloads", json=body(0)))
        lease = queue.claim()
        assert lease.id == sentinel
        queue.finish(lease.id, lease.lease_token)
        before = rows()
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(
                pool.map(lambda i: client.post("/api/downloads", json=body(i)), range(1, 9))
            )
        accepted = [r for r in responses if r.status_code == 202]
        for response in accepted:
            admitted(response)
        assert len(accepted) == 2
        rejected = [r for r in responses if r.status_code != 202]
        assert len(rejected) == 6
        assert all(
            r.status_code == 429
            and r.headers.get("retry-after") == "5"
            and r.json()["error"]["code"] == "queue_full"
            for r in rejected
        )
        assert queue.depth() == 2
        assert len(rows()) == len(before) + 2
        assert all(row in rows() for row in before)

        with ThreadPoolExecutor(max_workers=8) as pool:
            leases = list(pool.map(lambda _: queue.claim(), range(8)))
        live = [lease for lease in leases if lease is not None]
        assert len(live) == 1  # Real PostgreSQL max_running guard, independently of admissions.
        assert queue.depth() == 2
        snapshot = rows()
        denied = client.post("/api/downloads", json=body(9))
        assert denied.status_code == 429 and denied.json()["error"]["code"] == "queue_full"
        assert denied.headers["retry-after"] == "5"
        assert (
            rows() == snapshot
        )  # Rejection cannot alter requests, checkpoints, leases or timestamps.
        assert client.get(f"/api/downloads/{sentinel}").json()["data"]["state"] == "completed"

        queue.finish(live[0].id, live[0].lease_token)
        assert queue.depth() == 1
        admitted(client.post("/api/downloads", json=body(10)))
        assert queue.depth() == 2  # Capacity becomes available after actual completion.
        queue.set_draining(True)
        snapshot = rows()
        draining = client.post("/api/downloads", json=body(11))
        assert draining.status_code == 503 and draining.json()["error"]["code"] == "draining"
        assert draining.headers["retry-after"] == "5"
        assert queue.claim() is None
        assert rows() == snapshot
        assert all(row in rows() for row in baseline)
