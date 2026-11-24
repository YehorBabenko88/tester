"""Projection authority uses owner guards, never grants runtime auth mutation."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import psycopg
import pytest

from src.api.research_ledger import PersistentResearchLedger


def test_research_auth_guard_queries_preserve_lock_boundary():
    from unittest.mock import Mock

    connection = Mock()
    connection.execute.return_value.fetchone.return_value = {"allowed": True}
    PersistentResearchLedger._credential(connection, "operator")
    assert connection.execute.call_args.args == (
        "SELECT tester_research.lock_projection_credential(%s) AS allowed",
        ("operator",),
    )


def test_actual_projection_owner_revocation_waits_for_runtime_guard():
    runtime = os.getenv("TEST_RELEASE_RESEARCH_DSN")
    owner = os.getenv("TEST_RELEASE_RESEARCH_OWNER_DSN")
    if not runtime or not owner:
        pytest.skip("Explicit disposable runtime and owner research DSNs required")
    actor = "guard-test:" + uuid4().hex
    operator = PersistentResearchLedger(owner)
    # Deliberately owner-issued temporary capability for lock serialization only.
    with operator.connection() as conn:
        conn.execute(
            "INSERT INTO tester_research.credentials(actor,token_hash,expires_at) VALUES(%s,%s,clock_timestamp()+interval '60 seconds')",
            (actor, uuid4().hex + uuid4().hex),
        )
    started = Event()

    def revoke():
        started.set()
        operator.revoke(actor)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with PersistentResearchLedger(runtime).connection() as conn:
                PersistentResearchLedger._credential(conn, actor)
                future = executor.submit(revoke)
                assert started.wait(5)
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.15)
            future.result(timeout=5)
        with PersistentResearchLedger(runtime).connection() as conn:
            assert (
                conn.execute(
                    "SELECT tester_research.lock_projection_credential(%s) AS allowed", (actor,)
                ).fetchone()["allowed"]
                is False
            )
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            PersistentResearchLedger(runtime).connection() as conn,
        ):
            conn.execute(
                "UPDATE tester_research.credentials SET revoked=false WHERE actor=%s", (actor,)
            )
    finally:
        # Runtime cannot revoke/issue; owner cleanup is limited to this test actor.
        with operator.connection() as conn:
            conn.execute("DELETE FROM tester_research.credentials WHERE actor=%s", (actor,))
