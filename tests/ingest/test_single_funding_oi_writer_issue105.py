"""Issue #105(c): one funding/OI producer -> replays can never raise DataConflict.

Store.ingest raises DataConflict only when an existing record_key has a
different content_hash. The sole writer is rest.Backfill -> handoff.storage_record
-> storage.models.normalize; its identity and content hash must not depend on
acquisition facts (fetch time, run, evidence), so re-acquiring the same source
row is always an idempotent replay.
"""

import pytest

from src.ingest import public_auxiliary
from src.ingest.handoff import storage_record
from src.storage.models import normalize

ROWS = {
    "funding": {"symbol": "BTCUSDT", "fundingRate": "-0.0000123400", "fundingRateTimestamp": "1794560000000"},
    "open_interest": {"openInterest": "123456789.000001", "timestamp": "1794560000000"},
}


def raw(kind, run, fetched_at):
    row = ROWS[kind]
    return {
        "schema_version": "1.0",
        "venue": "bybit",
        "category": "linear",
        "symbol": "BTCUSDT",
        "dataset": kind,
        "interval": "5min" if kind == "open_interest" else "",
        "timestamp_ms": 1794560000000,
        "timestamp_precision": "ms",
        "timestamp_ns": None,
        "payload": dict(row),
        "source": {
            "url": "https://api.bybit.com/v5/market/"
            + ("open-interest" if kind == "open_interest" else "funding/history")
            + "?category=linear&symbol=BTCUSDT",
            "fetched_at": fetched_at,
            "source_kind": "bybit_rest",
            "ingestion_run": run,
        },
    }


def metadata(run, ref):
    return {
        "provenance": [{"source_kind": "bybit_rest"}],
        "ingestion_run": run,
        "evidence_refs": [ref],
        "availability": {"status": "UNVERIFIED"},
        "coverage": {"status": "UNKNOWN"},
    }


@pytest.mark.parametrize("kind", sorted(ROWS))
def test_canonical_replay_has_identical_identity_and_content(kind):
    first = normalize(storage_record(raw(kind, "run-a", "2026-11-13T12:00:00+00:00"), metadata("run-a", "e1")))
    again = normalize(storage_record(raw(kind, "run-b", "2026-11-14T08:30:00+00:00"), metadata("run-b", "e2")))
    assert first["record_key"] == again["record_key"]
    assert first["content_hash"] == again["content_hash"]  # => no DataConflict on replay
    assert first["source"]["ingestion_run"] != again["source"]["ingestion_run"]


@pytest.mark.parametrize("kind", sorted(ROWS))
def test_no_second_funding_oi_normalizer_remains(kind):
    # The former public_auxiliary payload {"value","unit",...,"source_fields"}
    # had the same record_key but a different content_hash than the canonical
    # {"value"} payload; that writer no longer exists.
    with pytest.raises(ValueError):
        public_auxiliary.request(kind, "linear", "BTCUSDT")
    canonical = normalize(storage_record(raw(kind, "run-a", "2026-11-13T12:00:00+00:00"), metadata("run-a", "e1")))
    assert set(canonical["payload"]) == {"value"}
