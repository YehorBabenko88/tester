"""Compact lab reads preserve linkage without fetching repeated proof documents."""

import copy
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.integration.storage_api import IntegrationUnavailable, provenance_from_storage
from src.storage.provenance import resolve
from src.storage.store import Store


def observation():
    return dict(
        record_key="record",
        source_id="source",
        source=dict(
            ingestion_run="run:original",
            url="https://api.bybit.com/v5/market/kline",
            sha256="a" * 64,
            source_timestamp="60000",
        ),
        raw_envelope={"original": "raw document"},
        observation_id="observation",
        run_id="run:original",
        job_key="job",
        evidence_id="evidence",
        source_kind="rest",
        timestamp_precision="ms",
        run_origin="caller",
        pagination_status="UNVERIFIED",
        verified=False,
        evidence={"original": "large pagination proof"},
        validator_version="storage-1.0",
    )


class Connection:
    def __init__(self, row):
        self.row, self.queries = row, []

    def execute(self, query, params):
        self.queries.append((query, params))
        row = copy.deepcopy(self.row)
        if "NULL::jsonb AS raw_envelope" in query:
            row["raw_envelope"] = None
        if "NULL::jsonb AS evidence" in query:
            row["evidence"] = None
        return SimpleNamespace(fetchall=lambda: [row])


def test_default_full_documents_are_preserved_and_compact_is_sql_level_opt_in():
    row = observation()
    full_connection = Connection(row)
    full = resolve(full_connection, ["record"])["record"]
    assert full["observations"][0]["raw_envelope"] == row["raw_envelope"]
    assert full["observations"][0]["evidence"] == row["evidence"]
    assert "s.raw_envelope" in full_connection.queries[0][0]
    assert "p.evidence," in full_connection.queries[0][0]
    compact_connection = Connection(row)
    compact = resolve(compact_connection, ["record"], include_documents=False)["record"]
    query = compact_connection.queries[0][0]
    assert "NULL::jsonb AS raw_envelope" in query and "NULL::jsonb AS evidence" in query
    assert "s.raw_envelope" not in query and "p.evidence," not in query
    assert compact["observations"][0]["raw_envelope"] is None
    assert compact["observations"][0]["evidence"] is None
    assert provenance_from_storage(compact, require_declared_run=True) == provenance_from_storage(
        full, require_declared_run=True
    )
    for key in ("authentic", "run_ids", "evidence_refs", "unlinked_sources"):
        assert compact[key] == full[key]
    assert compact["observations"][0]["pagination_status"] == "UNVERIFIED"
    assert compact["observations"][0]["verified"] is False


@pytest.mark.parametrize(
    "mutation", ["unlinked", "mismatched_run", "undeclared_run", "legacy_origin", "unknown_kind"]
)
def test_compact_keeps_fail_closed_authenticity_and_original_run_checks(mutation):
    row = observation()
    if mutation == "unlinked":
        row["observation_id"] = None
    elif mutation == "mismatched_run":
        row["source"]["ingestion_run"] = "run:different"
    elif mutation == "undeclared_run":
        row["source"].pop("ingestion_run")
    elif mutation == "legacy_origin":
        row["run_origin"] = "legacy_job"
    elif mutation == "unknown_kind":
        row["source_kind"] = "unknown"
    compact = resolve(Connection(row), ["record"], include_documents=False)["record"]
    full = resolve(Connection(row), ["record"])["record"]
    assert compact["authentic"] is full["authentic"]
    assert compact["authentic"] is (mutation == "undeclared_run")
    if mutation == "unlinked":
        assert compact["unlinked_sources"] == full["unlinked_sources"]
    with pytest.raises(IntegrationUnavailable):
        provenance_from_storage(compact, require_declared_run=True)


def test_store_read_default_and_opt_in_only_change_resolver_document_selection(monkeypatch):
    from src.storage import provenance

    seen = []

    def resolver(connection, keys, *, include_documents=True):
        seen.append(include_documents)
        return {key: dict(observations=[], authentic=False) for key in keys}

    monkeypatch.setattr(provenance, "resolve", resolver)

    class FakeStore(Store):
        @contextmanager
        def connect(self):
            yield SimpleNamespace(
                execute=lambda *a: SimpleNamespace(
                    fetchall=lambda: [dict(record_key="record", inserted_at="original")]
                )
            )

    store = FakeStore("not used")
    ordinary = store.read("candles", "bybit", "linear", "BTCUSDT", 0, 120000, "1")
    compact = store.read(
        "candles", "bybit", "linear", "BTCUSDT", 0, 120000, "1", compact_provenance=True
    )
    assert seen == [True, False]
    assert compact == ordinary


def test_lab_reader_explicitly_requests_compact_linkage():
    from src.api.models import Query
    from src.integration.lab_market import LabReader

    seen = []

    class FakeStore:
        def read(self, *args, **kwargs):
            seen.append(kwargs["compact_provenance"])
            return []

        def coverage(self, *args):
            return {}

        @contextmanager
        def connect(self):
            yield SimpleNamespace(execute=lambda *a: SimpleNamespace(fetchall=lambda: []))

    value = LabReader(FakeStore()).read(
        "candles", Query(symbol="BTCUSDT", start_ms=0, end_ms=120000)
    )
    assert seen == [True]
    assert value["coverage"]["status"] == "UNKNOWN"


def test_fenced_verification_explicitly_requests_compact_linkage(monkeypatch):
    from src.integration import lab_market
    from src.storage.models import normalize

    raw = dict(
        schema_version="1.0",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        dataset="candles",
        interval="1",
        timestamp_ms=60000,
        payload=dict(open="1", high="2", low="1", close="2", volume="3"),
        source=dict(
            url="https://api.bybit.com/v5/market/kline", fetched_at="2026-11-08T00:00:00+00:00"
        ),
    )
    normalized = normalize(raw)
    seen = []

    def read(*args, **kwargs):
        seen.append(kwargs["compact_provenance"])
        return [normalized]

    store = SimpleNamespace(read=read)
    connection = SimpleNamespace(
        execute=lambda *a: SimpleNamespace(fetchall=lambda: [(normalized["record_key"], {})])
    )
    monkeypatch.setattr(lab_market, "project_candle", lambda *a: None)
    batch = dict(
        records=[raw],
        evidence=dict(
            venue="bybit",
            category="linear",
            symbol="BTCUSDT",
            interval="1",
            start_ms=60000,
            end_ms=120000,
        ),
    )
    assert lab_market.verify_published_batch(store, connection, batch) == {
        normalized["record_key"]: normalized["content_hash"]
    }
    assert seen == [True]
