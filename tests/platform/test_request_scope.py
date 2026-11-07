from copy import deepcopy

import pytest
from canonical_fixtures import envelope
from fastapi.testclient import TestClient

from src.api.config import Settings
from src.api.main import create_app
from src.api.models import Query

START, END = 1700000000000, 1700000060000


def params(**changes):
    value = dict(category="linear", symbol="BTCUSDT", start_ms=START, end_ms=END, interval="1")
    value.update(changes)
    return value


@pytest.mark.parametrize(
    "query",
    [
        params(symbol="ETHUSDT"),
        params(category="spot"),
        params(start_ms=0, end_ms=60000),
    ],
)
def test_provider_boundary_rejects_wrong_request_identity(query):
    from src.api.scope import bound_response

    with pytest.raises(ValueError):
        bound_response("trades", "trade", envelope(), Query(**query))


@pytest.mark.timeout(10)
@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "ETHUSDT"},
        {"category": "spot"},
        {"timestamp_ms": END},
        {"timestamp_ms": START - 1},
        {"coverage": dict(status="PARTIAL", start_ms=0, end_ms=60000, evidence_refs=["fixture"])},
    ],
)
def test_http_rejects_wrong_and_mixed_scope_pages(tmp_path, change):
    value = envelope()
    bad = deepcopy(value["data"][0])
    bad.update(change)
    # Changing a timestamp must remain a structurally valid canonical record.
    if "timestamp_ms" in change:
        bad.update(timestamp_ns=None, timestamp_precision="ms")
        bad["provenance"][0].update(
            timestamp_precision="ms", source_timestamp=str(change["timestamp_ms"])
        )
    bad["trade_id"] = "fixture-other"
    value["data"].append(bad)

    class Provider:
        def read(self, resource, query):
            return value

    with TestClient(
        create_app(Settings(data_dir=tmp_path), Provider()), base_url="http://127.0.0.1:8765"
    ) as client:
        result = client.get("/api/trades", params=params())
        assert result.status_code == 502
        assert result.json()["error"]["code"] == "provider_scope_mismatch"
        assert "fixture-other" not in result.text


@pytest.mark.timeout(10)
def test_exact_master_reproduction_is_rejected(tmp_path):
    class Provider:
        def read(self, resource, query):
            return envelope()

    with TestClient(
        create_app(Settings(data_dir=tmp_path), Provider()), base_url="http://127.0.0.1:8765"
    ) as client:
        response = client.get(
            "/api/trades", params=dict(category="spot", symbol="ETHUSDT", start_ms=0, end_ms=60000)
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "provider_scope_mismatch"


@pytest.mark.timeout(10)
def test_provider_cannot_rewrite_authoritative_request_scope(tmp_path):
    class Provider:
        def read(self, resource, query):
            query.symbol = "BTCUSDT"
            query.category = "linear"
            query.start_ms = START
            query.end_ms = END
            return envelope()

    with TestClient(
        create_app(Settings(data_dir=tmp_path), Provider()), base_url="http://127.0.0.1:8765"
    ) as client:
        response = client.get(
            "/api/trades", params=dict(category="spot", symbol="ETHUSDT", start_ms=0, end_ms=60000)
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "provider_scope_mismatch"


@pytest.mark.timeout(10)
def test_unrelated_envelope_coverage_rejected_with_safe_context(tmp_path):
    value = envelope()
    value["coverage"].update(start_ms=0, end_ms=60000)
    value["provenance"][0]["source_ref"] = "postgresql://user:SECRET@host/db"
    value["provenance"][0]["ingestion_run"] = "password=SECRET"

    class Provider:
        def read(self, resource, query):
            return value

    with TestClient(
        create_app(Settings(data_dir=tmp_path), Provider()), base_url="http://127.0.0.1:8765"
    ) as client:
        response = client.get("/api/trades", params=params())
        assert response.status_code == 502
        assert "SECRET" not in response.text
        context = response.json()["error"]["context"]
        assert context["upstream_availability"] == "PARTIAL"
        assert context["provenance"][0]["source_kind"] == "synthetic_fixture"
        assert len(context["provenance"][0]["source_ref_sha256"]) == 64


@pytest.mark.timeout(10)
def test_http_rejects_wrong_candle_interval(tmp_path):
    value = envelope()
    row = value["data"][0]
    for key in [
        "timestamp_ns",
        "timestamp_precision",
        "trade_id",
        "id_origin",
        "price",
        "quantity",
        "taker_side",
    ]:
        row.pop(key)
    row.update(
        timestamp_ms=START,
        start_ms=START,
        end_ms=END,
        interval="1",
        kind="official_trade",
        open="100",
        high="102",
        low="99",
        close="101",
        volume="1",
        is_closed=True,
    )

    class Provider:
        def read(self, resource, query):
            return value

    with TestClient(
        create_app(Settings(data_dir=tmp_path), Provider()), base_url="http://127.0.0.1:8765"
    ) as client:
        assert client.get("/api/candles", params=params(interval="60")).status_code == 502


@pytest.mark.timeout(10)
def test_cursor_bound_to_resource_filters_and_tamper_proof(tmp_path):
    class Provider:
        seen = []

        def read(self, resource, query):
            self.seen.append(query.cursor)
            return envelope()

    provider = Provider()
    with TestClient(
        create_app(Settings(data_dir=tmp_path), provider), base_url="http://127.0.0.1:8765"
    ) as client:
        first = client.get("/api/trades", params=params())
        assert first.status_code == 200
        token = first.json()["next_cursor"]
        assert {key: item for key, item in first.json().items() if key != "next_cursor"} == {
            key: item for key, item in envelope().items() if key != "next_cursor"
        }
        assert token != "opaque-source-cursor"
        assert client.get("/api/trades", params=params(cursor=token)).status_code == 200
        assert provider.seen == [None, "opaque-source-cursor"]
        count = len(provider.seen)
        for url, query in [
            ("/api/trades", params(cursor=token, symbol="ETHUSDT")),
            ("/api/candles", params(cursor=token)),
            ("/api/trades", params(cursor=token, interval="60")),
            ("/api/trades", params(cursor=token, category="spot")),
            ("/api/trades", params(cursor=token, start_ms=0)),
            ("/api/trades", params(cursor=token, limit=1)),
            ("/api/trades", params(cursor=token[:-1] + "!")),
        ]:
            assert client.get(url, params=query).status_code == 422
        assert len(provider.seen) == count


def test_provider_coverage_rows_must_be_inside_requested_range():
    from src.api.scope import bound_response

    value = envelope()
    value["data"] = [dict(status="PARTIAL", start_ms=0, end_ms=60000, evidence_refs=["fixture"])]
    with pytest.raises(ValueError):
        bound_response("coverage", "coverage", value, Query(**params()))


@pytest.mark.timeout(10)
def test_explicit_venue_and_candle_kind_are_bound(tmp_path):
    value = envelope()
    row = value["data"][0]
    for key in [
        "timestamp_ns",
        "timestamp_precision",
        "trade_id",
        "id_origin",
        "price",
        "quantity",
        "taker_side",
    ]:
        row.pop(key)
    row.update(
        timestamp_ms=START,
        start_ms=START,
        end_ms=END,
        interval="1",
        kind="official_mark",
        open="100",
        high="102",
        low="99",
        close="101",
        volume="1",
        is_closed=True,
    )

    class Provider:
        def read(self, resource, query):
            return value

    with TestClient(
        create_app(Settings(data_dir=tmp_path), Provider()), base_url="http://127.0.0.1:8765"
    ) as client:
        assert client.get("/api/candles", params=params()).status_code == 502
        assert (
            client.get("/api/candles", params=params(candle_kind="official_mark")).status_code
            == 200
        )
        assert client.get("/api/candles", params=params(venue="other")).status_code == 422


def test_cursor_restart_and_unicode_budget_fail_closed():
    from src.api.scope import CursorCodec

    query = Query(**params())
    token = CursorCodec().encode("trades", query, "source-cursor")
    with pytest.raises(ValueError):
        CursorCodec().decode("trades", query.model_copy(update={"cursor": token}))
    with pytest.raises(ValueError):
        CursorCodec().encode("trades", query, "\U0001f600" * 2048)


@pytest.mark.parametrize(
    "interval,start,end,valid",
    [
        ("1", 0, 60000, True),
        ("D", 0, 86400000, True),
        ("W", 0, 604800000, True),
        ("M", 1706745600000, 1709251200000, True),
        ("M", 1706745600000, 1709337600000, False),
    ],
)
def test_footprint_without_interval_field_binds_utc_bar_window(interval, start, end, valid):
    from src.api.scope import bound_response

    value = envelope()
    row = value["data"][0]
    for key in [
        "timestamp_ns",
        "timestamp_precision",
        "trade_id",
        "id_origin",
        "price",
        "quantity",
        "taker_side",
    ]:
        row.pop(key)
    row.update(
        timestamp_ms=start,
        start_ms=start,
        end_ms=end,
        side_basis="taker",
        buy_volume="1",
        sell_volume="0",
        unknown_side_volume="1",
        delta=None,
    )
    row["provenance"][0].update(
        source_kind="derived",
        source_ref="fixture://aggregate",
        source_timestamp=None,
        parent_refs=["fixture://trades"],
        method_version="fixture-v1",
    )
    row["coverage"].update(start_ms=start, end_ms=end)
    value["coverage"] = deepcopy(row["coverage"])
    value["provenance"] = deepcopy(row["provenance"])
    query = Query(**params(start_ms=start, end_ms=end, interval=interval))
    if valid:
        assert bound_response("footprint", "footprint", value, query) == value
    else:
        with pytest.raises(ValueError):
            bound_response("footprint", "footprint", value, query)
