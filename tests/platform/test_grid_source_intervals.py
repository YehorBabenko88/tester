from copy import deepcopy

import pytest
from canonical_fixtures import envelope
from fastapi.testclient import TestClient

from src.api.config import Settings
from src.api.main import create_app
from src.api.scope import _bar_interval


@pytest.mark.parametrize(
    "interval,start,end",
    [
        ("1", 1, 60001),
        ("60", 60000, 3660000),
        ("D", 1, 86400001),
        ("W", 1, 604800001),
        ("W", 0, 604800000),
        ("1", 0.0, 60000.0),
        ("D", False, 86400000),
    ],
)
def test_source_bar_rejects_off_grid_and_non_integer_times(interval, start, end):
    assert _bar_interval(start, end, interval) is False


@pytest.mark.parametrize(
    "interval,start,end",
    [
        ("1", 0, 60000),
        ("60", 0, 3600000),
        ("D", 0, 86400000),
        ("W", 345600000, 950400000),
        ("M", 1706745600000, 1709251200000),
        ("M", 1733011200000, 1735689600000),
        ("M", 1675209600000, 1677628800000),
    ],
)
def test_source_bar_accepts_exact_utc_calendar_grid(interval, start, end):
    assert _bar_interval(start, end, interval) is True


@pytest.mark.timeout(10)
@pytest.mark.parametrize(
    "interval,start,end,status",
    [
        ("1", 1, 60001, 502),
        ("D", 1, 86400001, 502),
        ("W", 1, 604800001, 502),
        ("W", 345600000, 950400000, 200),
        ("M", 1706745600000, 1709251200000, 200),
        ("M", 1733011200000, 1735689600000, 200),
    ],
)
def test_http_source_bar_grid(interval, start, end, status, tmp_path):
    value = envelope()
    value["next_cursor"] = None
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
        interval=interval,
        kind="official_trade",
        open="100",
        high="102",
        low="99",
        close="101",
        volume="1",
        is_closed=True,
    )
    row["coverage"].update(start_ms=start, end_ms=end)
    value["coverage"] = deepcopy(row["coverage"])

    class Provider:
        def read(self, resource, query):
            return value

    with TestClient(
        create_app(Settings(data_dir=tmp_path), Provider()), base_url="http://127.0.0.1:8765"
    ) as client:
        response = client.get(
            "/api/candles",
            params={"start_ms": start, "end_ms": end, "interval": interval, "symbol": "BTCUSDT"},
        )
        assert response.status_code == status
        if status == 200:
            assert response.json() == value


def observation(kind, interval):
    value = envelope()
    row = value["data"][0]
    for name in [
        "timestamp_ns",
        "timestamp_precision",
        "trade_id",
        "id_origin",
        "price",
        "quantity",
        "taker_side",
    ]:
        row.pop(name)
    row.update(
        kind=kind,
        interval=interval,
        value="-0.0001" if kind == "funding" else "100",
        unit="rate" if kind == "funding" else "contracts",
    )
    return value


@pytest.mark.timeout(10)
@pytest.mark.parametrize(
    "resource,kind,source",
    [
        ("funding", "funding", "event"),
        ("open-interest", "oi", "5min"),
    ],
)
def test_source_cadence_is_independent_of_viewport_and_cursors(tmp_path, resource, kind, source):
    class Provider:
        seen = []

        def read(self, name, query):
            self.seen.append(
                (query.interval, query.source_interval, query.viewport_interval, query.cursor)
            )
            return deepcopy(observation(kind, source))

    provider = Provider()
    params = dict(
        symbol="BTCUSDT",
        category="linear",
        start_ms=1700000000000,
        end_ms=1700000060000,
        interval="60",
        source_interval=source,
    )
    with TestClient(
        create_app(Settings(data_dir=tmp_path), provider), base_url="http://127.0.0.1:8765"
    ) as client:
        first = client.get("/api/" + resource, params=params)
        assert first.status_code == 200
        token = first.json()["next_cursor"]
        assert first.json()["data"][0]["interval"] == source
        second = client.get("/api/" + resource, params={**params, "interval": "D", "cursor": token})
        assert second.status_code == 200
        assert provider.seen == [
            (source, source, "60", None),
            (source, source, "D", "opaque-source-cursor"),
        ]
        if resource == "open-interest":
            changed = client.get(
                "/api/" + resource, params={**params, "source_interval": "1h", "cursor": token}
            )
            assert changed.status_code == 422
            assert len(provider.seen) == 2


@pytest.mark.timeout(10)
def test_resource_defaults_and_invalid_source_fail_before_provider(tmp_path):
    class Provider:
        seen = []

        def read(self, name, query):
            self.seen.append((name, query.interval))
            return observation("funding" if name == "funding" else "oi", query.interval)

    provider = Provider()
    params = dict(start_ms=1700000000000, end_ms=1700000060000, interval="D")
    with TestClient(
        create_app(Settings(data_dir=tmp_path), provider), base_url="http://127.0.0.1:8765"
    ) as client:
        assert client.get("/api/funding", params=params).status_code == 200
        assert client.get("/api/open-interest", params=params).status_code == 200
        assert provider.seen == [("funding", "event"), ("open-interest", "5min")]
        # A display label that also looks like a native cadence must not select it.
        response = client.get("/api/open-interest", params={**params, "interval": "1h"})
        assert response.status_code == 200
        assert response.json()["data"][0]["interval"] == "5min"
        response = client.get("/api/open-interest", params={**params, "source_interval": "1h"})
        assert response.status_code == 200
        assert response.json()["data"][0]["interval"] == "1h"
        assert (
            client.get("/api/funding", params={**params, "source_interval": "60"}).status_code
            == 422
        )
        assert (
            client.get("/api/open-interest", params={**params, "source_interval": "M"}).status_code
            == 422
        )
        assert len(provider.seen) == 4


@pytest.mark.timeout(10)
def test_http_rejects_float_wire_timestamp_even_when_numerically_integral(tmp_path):
    value = observation("oi", "5min")
    value["data"][0]["timestamp_ms"] = 1700000000123.0

    class Provider:
        def read(self, resource, query):
            return value

    with TestClient(
        create_app(Settings(data_dir=tmp_path), Provider()), base_url="http://127.0.0.1:8765"
    ) as client:
        response = client.get(
            "/api/open-interest", params={"start_ms": 1700000000000, "end_ms": 1700000060000}
        )
        assert response.status_code == 502
