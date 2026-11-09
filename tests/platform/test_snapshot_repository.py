"""Mocked connection-boundary tests; optional PostgreSQL case is hosted-CI only."""

import json
import os
from unittest.mock import Mock

import pytest

from src.integration.qualified_snapshot import certified_fixture_snapshot
from src.integration.snapshot_repository import QualifiedSnapshotRepository

DSN = "host=127.0.0.1 port=55472 dbname=tester_research_snapshot_test"


def repository(monkeypatch, document):
    conn = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    conn.execute.return_value.fetchone.return_value = document
    monkeypatch.setattr(QualifiedSnapshotRepository, "connection", lambda self: conn)
    return QualifiedSnapshotRepository(DSN), conn


def test_read_requires_explicit_safe_endpoint_without_connecting():
    for dsn in ("dbname=postgres", "host=127.0.0.1 port=5432 dbname=tester_research_snapshot_test"):
        with pytest.raises(ValueError):
            QualifiedSnapshotRepository(dsn)


def test_bounded_read_is_readonly_and_revalidates_pinned_snapshot(monkeypatch):
    value = certified_fixture_snapshot()
    repo, conn = repository(monkeypatch, dict(encoded=json.dumps(value.as_dict())))
    assert repo.read(value.snapshot_id).as_dict() == value.as_dict()
    queries = [call.args[0] for call in conn.execute.call_args_list]
    assert queries[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert "CASE WHEN octet_length" in queries[1]
    assert "LIMIT 1" in queries[1]
    assert not any("UPDATE" in query or "INSERT" in query for query in queries)


@pytest.mark.parametrize("document", [None, {"encoded": None}, {"encoded": '{"admitted":true}'}])
def test_missing_oversized_or_forged_document_fails_closed(monkeypatch, document):
    repo, _ = repository(monkeypatch, document)
    with pytest.raises(ValueError):
        repo.read(certified_fixture_snapshot().snapshot_id)


def test_unknown_snapshot_id_rejected_before_connection(monkeypatch):
    repo, conn = repository(monkeypatch, None)
    with pytest.raises(ValueError):
        repo.read("../../private")
    conn.execute.assert_not_called()


def test_operator_publish_only_exact_fixture_is_idempotent_never_overwrites(monkeypatch):
    value = certified_fixture_snapshot()
    repo, conn = repository(monkeypatch, dict(encoded=json.dumps(value.as_dict())))
    assert repo.publish_fixture().as_dict() == value.as_dict()
    sql = conn.execute.call_args_list[0].args[0]
    assert "ON CONFLICT (snapshot_id) DO NOTHING" in sql
    assert not hasattr(repo, "publish_snapshot")


def test_publication_refuses_conflicting_existing_fact(monkeypatch):
    repo, _ = repository(monkeypatch, dict(encoded='{"admitted":false}'))
    with pytest.raises(ValueError):
        repo.publish_fixture()


@pytest.mark.integration
@pytest.mark.parametrize("variant", ["spot", "terminal"])
def test_postgres_snapshot_restart_readback_and_f_execution(variant):
    dsn = os.environ.get("TEST_QUALIFIED_SNAPSHOT_DATABASE_URL")
    if not dsn:
        pytest.skip("hosted CI explicitly disposable snapshot PostgreSQL required")
    from src.integration.strategy_lab import run_synthetic_research

    repo = QualifiedSnapshotRepository(dsn)
    repo.migrate()
    repo.migrate()
    publish = repo.publish_fixture if variant == "spot" else repo.publish_terminal_fixture
    first = publish()
    assert publish().as_dict() == first.as_dict()
    loaded = QualifiedSnapshotRepository(dsn).read(first.snapshot_id)
    assert loaded.as_dict() == first.as_dict()
    from pathlib import Path

    import src.integration.qualified_snapshot as producer

    strategy = (
        json.loads(
            (Path(producer.__file__).parent / "fixtures/f998/example.strategy.json").read_bytes()
        )
        if variant == "spot"
        else producer.certified_terminal_fixture_strategy()
    )
    bundle = run_synthetic_research(strategy, loaded.snapshot)
    assert bundle["snapshot_digest"] == loaded.snapshot_digest
    assert bundle["synthetic"] is True
    assert bundle["certification"]["scope"] == "SYNTHETIC_ONLY"
