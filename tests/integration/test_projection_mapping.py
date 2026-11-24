"""Original projections stay immutable while new authentic observations may replay."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.integration import lab_market
from src.storage.store import DataConflict


def test_inserted_projection_matches_captured_mapping(monkeypatch):
    monkeypatch.setattr(lab_market, "validate_record", lambda *a: None)
    connection = Mock()
    original = {"identity": "fixture", "metadata": {"trusted": True}}
    connection.execute.return_value.fetchone.return_value = (original,)
    assert lab_market.publish_projection(connection, "record:fixture", original) == original
    assert "ON CONFLICT DO NOTHING" in connection.execute.call_args_list[0].args[0]
    changed = deepcopy(original)
    changed["metadata"]["trusted"] = 1
    connection.execute.return_value.fetchone.return_value = (changed,)
    with pytest.raises(DataConflict, match="projection"):
        lab_market.publish_projection(connection, "record:fixture", original)


def test_projection_conflict_preserves_original_for_existing_batch_validation(monkeypatch):
    checked = []
    monkeypatch.setattr(lab_market, "validate_record", lambda kind, value: checked.append(value))
    connection = Mock()
    stored = {"provenance": [{"ingestion_run": "original-authentic-run"}]}
    new = {"provenance": [{"ingestion_run": "new-authentic-run"}]}
    connection.execute.return_value.fetchone.side_effect = [None, (stored,)]
    assert lab_market.publish_projection(connection, "record:fixture", new) == stored
    assert checked == [stored]
    assert all(
        "UPDATE" not in call.args[0] and "DELETE" not in call.args[0]
        for call in connection.execute.call_args_list
    )


def test_new_authentic_acquisition_retains_old_projection_but_invalid_scope_refused():
    from src.api.providers import ProviderDataUnavailable
    from src.storage.models import normalize

    source = dict(
        url="https://api.bybit.com/v5/market/kline",
        fetched_at="2026-11-08T00:00:00+00:00",
        sha256="a" * 64,
        ingestion_run="run:original",
    )
    raw = dict(
        schema_version="1.0",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        dataset="candles",
        interval="1",
        timestamp_ms=60000,
        payload=dict(open="1", high="2", low="1", close="2", volume="3"),
        source=source,
    )
    row = normalize(raw)
    observations = []
    for run, sha in [("run:original", "a" * 64), ("run:new-authentic", "b" * 64)]:
        observations.append(
            dict(
                authentic_run=True,
                evidence_id="e:" + run,
                source={**source, "sha256": sha, "ingestion_run": run},
                run_id=run,
                source_kind="rest",
                timestamp_precision="ms",
                source_run_status="matched",
                source_ingestion_run=run,
            )
        )
    row["provenance"] = dict(authentic=True, unlinked_sources=[], observations=observations)
    original = dict(
        schema_version="1.0",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        timestamp_ms=60000,
        start_ms=60000,
        end_ms=120000,
        interval="1",
        kind="official_trade",
        is_closed=True,
        open="1",
        high="2",
        low="1",
        close="2",
        volume="3",
        provenance=[
            dict(
                source_kind="bybit_rest",
                source_ref=source["url"],
                source_hash="a" * 64,
                ingestion_run="run:original",
                timestamp_precision="ms",
                source_timestamp=None,
            )
        ],
        availability=dict(status="PARTIAL", reason="bounded acquisition"),
        coverage=dict(status="UNKNOWN", start_ms=60000, end_ms=120000, evidence_refs=[]),
    )
    newer = deepcopy(original)
    newer["provenance"][0].update(source_hash="b" * 64, ingestion_run="run:new-authentic")
    connection = Mock()
    for candidate in [original, newer]:
        connection.execute.return_value.fetchone.side_effect = [None, (original,)]
        stored = lab_market.publish_projection(connection, row["record_key"], candidate)
        assert stored == original
        assert (
            lab_market.project_candle(row, stored, 60000, 120000)["provenance"][0]["ingestion_run"]
            == "run:original"
        )
    invalid = deepcopy(original)
    invalid["symbol"] = "ETHUSDT"
    with pytest.raises(ProviderDataUnavailable):
        lab_market.project_candle(row, invalid, 60000, 120000)
