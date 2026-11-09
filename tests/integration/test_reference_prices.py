"""Pure mark/index integration; actual A replay, no source network or PostgreSQL."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.api.models import DownloadRequest
from src.api.providers import ProviderUnavailable
from src.integration.reference_prices import (
    ReferencePriceHandler,
    original_price_projection,
    project_price_candle,
    validate_reference_download,
)


def request(kind="mark", **changes):
    return DownloadRequest(
        **(
            dict(
                category="linear",
                symbol="BTCUSDT",
                source="bybit",
                kind=kind,
                interval="1",
                start_ms=0,
                end_ms=60000,
            )
            | changes
        )
    )


def identity(category, symbol):
    return dict(
        category=category,
        symbol=symbol,
        status="Trading",
        contract_kind="perpetual",
        contract_type="LinearPerpetual" if category == "linear" else "InversePerpetual",
    )


@pytest.mark.parametrize("kind", ["mark", "index"])
def test_typed_admission_explicit_reference_handler_only(kind):
    validate_reference_download(request(kind), identity)
    handler = ReferencePriceHandler(
        SimpleNamespace(market_catalog_enabled=True, market_catalog_dsn="explicit", dsn="explicit"),
        lookup=identity,
    )
    assert handler._acquisition_kind(request(kind)) == kind
    assert handler._acquisition_kind(request("candles")) == "kline"
    from src.integration.catalog_market import validate_catalog_download

    with pytest.raises(ProviderUnavailable):
        validate_catalog_download(request(kind), identity)
    for unsupported in ("funding", "open_interest", "trades"):
        with pytest.raises(ProviderUnavailable):
            validate_reference_download(request(unsupported), identity)


def item(kind="mark"):
    endpoint = "mark-price-kline" if kind == "mark" else "index-price-kline"
    source = dict(
        source_kind="bybit_rest",
        source_ref="https://api.bybit.com/v5/market/"
        + endpoint
        + "?category=linear&symbol=BTCUSDT&interval=1",
        source_hash="a" * 64,
        ingestion_run="actual-caller",
        timestamp_precision="ms",
        source_timestamp="0",
    )
    raw = dict(
        schema_version="1.0",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        timestamp_ms=0,
        interval="1",
        dataset=kind,
        is_closed=True,
        closure_server_ms=60000,
    )
    storage = {
        **raw,
        "payload": dict(open="100.000000000000000001", high="101", low="99", close="100"),
    }
    return dict(
        raw_record=raw,
        storage_record=storage,
        provenance=[source],
        coverage=dict(
            status="PARTIAL", start_ms=0, end_ms=60000, evidence_refs=["original-http-proof"]
        ),
        availability=dict(status="AVAILABLE", reason=None),
        quarantined=False,
    )


def test_price_only_projection_preserves_decimal_provenance_and_unknown_without_volume():
    value = item()
    original = deepcopy(value)
    projected = original_price_projection(value)
    assert projected["schema_version"] == "1.1"
    assert projected["kind"] == "official_mark"
    assert projected["open"] == "100.000000000000000001"
    assert "volume" not in projected
    assert projected["coverage"]["status"] == "UNKNOWN"
    assert projected["provenance"] == value["provenance"]
    assert value == original


@pytest.mark.parametrize("change", [dict(is_closed=False), dict(closure_server_ms=59999)])
def test_no_closed_reference_projection_without_exchange_closure_evidence(change):
    value = item()
    value["raw_record"].update(change)
    with pytest.raises(ValueError):
        original_price_projection(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_ref", "https://api.bybit.com/v5/market/funding/history"),
        ("source_timestamp", "1"),
        ("timestamp_precision", "ns"),
    ],
)
def test_reference_source_identity_is_not_inferred_from_other_raw_kinds(field, value):
    original = item()
    original["provenance"][0][field] = value
    with pytest.raises(ValueError):
        original_price_projection(original)


def test_unverified_source_availability_is_not_promoted():
    original = item()
    original["availability"] = dict(status="UNVERIFIED", reason="Not verified")
    with pytest.raises(ValueError):
        original_price_projection(original)


def test_price_reader_never_relabels_trade_or_changed_values(monkeypatch):
    from src.integration import reference_prices as module

    value = original_price_projection(item())
    row = dict(
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        interval="1",
        timestamp_ms=0,
        dataset="mark",
        payload=deepcopy(item()["storage_record"]["payload"]),
        provenance={},
    )
    monkeypatch.setattr(module, "provenance_from_storage", lambda *a, **k: value["provenance"])
    assert project_price_candle(row, value, 0, 60000) == value
    for change in (dict(dataset="candles"), dict(payload={**row["payload"], "close": "99"})):
        with pytest.raises(ProviderUnavailable):
            project_price_candle(row | change, value, 0, 60000)


@pytest.mark.parametrize("kind", ["mark", "index"])
def test_actual_a_backfill_retains_http_hash_utc_and_price_only_handoff(tmp_path, kind):
    import base64
    import hashlib
    import json
    from datetime import datetime
    from urllib.parse import urlencode

    from src.ingest.journal import Journal
    from src.ingest.rest import Backfill

    class Replay:
        def server_time_ms(self):
            return 60000

        def call(self, path, params):
            result = dict(
                category="linear",
                list=[["0", "100.000000000000000001", "101", "99", "100"]],
                nextPageCursor="",
            )
            body = json.dumps(dict(retCode=0, result=result)).encode()
            url = "https://api.bybit.com/v5/market" + path + "?" + urlencode(params)
            self.last_response = dict(
                url=url,
                bytes_base64=base64.b64encode(body).decode(),
                sha256=hashlib.sha256(body).hexdigest(),
                hash_scope="original_http_body",
                status_code=200,
            )
            return result, url

    journal = Journal(tmp_path / "journal.sqlite")
    try:
        Backfill(Replay(), journal).run("linear", "BTCUSDT", kind, "1", 0, 60000)
        path = tmp_path / "handoff.json"
        journal.export(path)
        bundle = json.loads(path.read_text())
    finally:
        journal.close()
    handler = ReferencePriceHandler(
        SimpleNamespace(market_catalog_enabled=True, market_catalog_dsn="explicit", dsn="explicit"),
        lookup=identity,
    )
    originals = handler._originals(bundle)
    assert len(originals) == 1
    projected = next(iter(originals.values()))
    assert projected["kind"] == "official_" + kind
    assert projected["schema_version"] == "1.1" and "volume" not in projected
    assert projected["open"] == "100.000000000000000001"
    assert projected["coverage"]["status"] == "UNKNOWN"
    batch = next(b for b in bundle["storage_batches"] if b["records"])
    assert batch["evidence"]["status"] == "UNVERIFIED"
    assert batch["records"][0]["dataset"] == kind
    assert (
        datetime.fromisoformat(batch["records"][0]["source"]["fetched_at"])
        .utcoffset()
        .total_seconds()
        == 0
    )
    responses = [
        p
        for p in batch["evidence"]["acquisition_manifest"]["evidence"].values()
        if p["kind"] == "rest_response"
    ]
    assert responses
    for proof in responses:
        original = proof["http_response"]
        body = base64.b64decode(original["bytes_base64"])
        assert hashlib.sha256(body).hexdigest() == original["sha256"]
        assert original["hash_scope"] == "original_http_body"


def test_explicit_profile_routes_and_legacy_default_remain_distinct(tmp_path):
    from fastapi.testclient import TestClient

    from src.api.config import Settings
    from src.api.main import create_app
    from src.integration.market_runtime import config_for
    from src.integration.reference_prices import ReferencePriceReader

    baseline = config_for(tmp_path, tmp_path, "explicit")
    selected = config_for(tmp_path, tmp_path, "explicit", "reference-prices/1.1")
    assert baseline["provider_factory"] == "src.integration.catalog_market:create_reader"
    assert selected["provider_factory"] == "src.integration.reference_prices:create_reader"
    assert selected["handler_factory"] == "src.integration.reference_prices:create_handler"
    with pytest.raises(ValueError):
        config_for(tmp_path, tmp_path, "explicit", "arbitrary-module")
    reader = ReferencePriceReader(None)
    reader.read = lambda resource, query: dict(
        schema_version="1.1",
        data=[original_price_projection(item())],
        next_cursor=None,
        availability=dict(status="PARTIAL", reason="Observed only"),
        coverage=dict(status="UNKNOWN", start_ms=0, end_ms=60000, evidence_refs=[]),
        provenance=item()["provenance"],
    )
    app = create_app(
        Settings(data_dir=tmp_path, provider_factory=selected["provider_factory"]), provider=reader
    )
    plain = create_app(Settings(data_dir=tmp_path))
    assert "/api/price-candles" not in {r.path for r in plain.routes}
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    params = dict(
        category="linear",
        symbol="BTCUSDT",
        start_ms=0,
        end_ms=60000,
        candle_kind="official_mark",
        interval="1",
    )
    response = client.get("/api/price-candles", params=params)
    assert response.status_code == 200, response.text
    assert response.json()["schema_version"] == "1.1"
    assert "volume" not in response.json()["data"][0]
    assert (
        client.get("/api/price-candles", params=params | {"schema_version": "1.0"}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/price-candles", params=params | {"candle_kind": "official_trade"}
        ).status_code
        == 422
    )
    assert "PriceEnvelope" in app.openapi()["components"]["schemas"]
    for change in ({"start_ms": "0.0"}, {"end_ms": "60000.0"}):
        assert client.get("/api/price-candles", params=params | change).status_code == 422


def test_actual_reference_reader_dict_row_projection_and_authentic_run():
    from unittest.mock import Mock

    from src.api.models import Query
    from src.integration.reference_prices import ReferencePriceReader

    original = original_price_projection(item())
    row = dict(
        record_key="a" * 64,
        dataset="mark",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        interval="1",
        timestamp_ms=0,
        payload=deepcopy(item()["storage_record"]["payload"]),
    )
    observation = dict(
        record_key=row["record_key"],
        authentic_run=True,
        source_kind="rest",
        source_run_status="matched",
        run_id="actual-caller",
        evidence_id="proof",
        timestamp_precision="ms",
        source=dict(
            url=item()["provenance"][0]["source_ref"],
            sha256="a" * 64,
            ingestion_run="actual-caller",
            source_timestamp="0",
        ),
    )
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    # B Store.connect uses psycopg dict_row, not tuple rows.
    connection.execute.return_value.fetchall.return_value = [
        dict(record_key=row["record_key"], record=original)
    ]
    store = Mock()
    store.connect.return_value = connection
    store.read_page.return_value = dict(
        outcome="OK",
        rows=[row],
        observations=[observation],
        scope=dict(
            dataset="mark", venue="bybit", category="linear", symbol="BTCUSDT", interval="1"
        ),
        coverage=dict(intervals=dict(corrupt=[], unavailable=[])),
        next_cursor=None,
    )
    query = Query(
        category="linear",
        symbol="BTCUSDT",
        interval="1",
        start_ms=0,
        end_ms=60000,
        candle_kind="official_mark",
    )
    response = ReferencePriceReader(store).read("price-candles", query)
    assert response["schema_version"] == "1.1"
    assert response["data"][0] == original
    assert response["coverage"]["status"] == "UNKNOWN"
    assert store.read_page.call_args.kwargs["max_archive_bytes"] == 0
    store.read_page.return_value["observations"][0]["authentic_run"] = False
    with pytest.raises(ProviderUnavailable):
        ReferencePriceReader(store).read("price-candles", query)


def test_published_pc4_selector_is_explicit_and_unknown_profile_precedes_side_effects(monkeypatch):
    from src.integration import market_runtime, pc4_runtime

    captured = []
    monkeypatch.setattr(pc4_runtime, "prepare", lambda *a, **k: captured.append((a, k)))
    pc4_runtime.prepare_price_sources("new-root", "lab-sha", "new-source-sha")
    assert captured == [
        (("new-root", "lab-sha", "new-source-sha"), dict(source_profile="reference-prices/1.1"))
    ]

    def refuse(*args):
        raise AssertionError("No connection/ownership query before rejecting unknown profile")

    monkeypatch.setattr(market_runtime, "context", refuse)
    with pytest.raises(ValueError):
        market_runtime.seed("new-root", "lab", "source", source_profile="unreviewed")
