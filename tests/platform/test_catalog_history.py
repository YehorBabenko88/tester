"""Measured history qualification, with no fabricated prior observations."""

from copy import deepcopy
from uuid import uuid4

import pytest

from src.api.catalog_history import enrich_snapshot, fresh_points, qualify_delta
from tests.platform.test_catalog_runtime import stores as stores


def metric(
    value="120",
    stamp=90000000,
    unit="BTC",
    source="https://api.bybit.com/v5/market/tickers?category=linear",
):
    return dict(
        value=value,
        source_time_ms=stamp,
        observed_at=stamp / 1000,
        source=source,
        state="OBSERVED",
        unit=unit,
        reason=None,
    )


def test_ratio_uses_actual_positive_baseline_with_provenance():
    current, baseline = metric(), metric("100", 86400000)
    result = qualify_delta(current, baseline, 3600000)
    assert result["value"] == "0.2"
    assert result["unit"] == "ratio"
    assert result["state"] == "OBSERVED"
    assert result["baseline_source_time_ms"] == 86400000
    assert result["actual_window_ms"] == 3600000
    assert result["source_time_ms"] == 90000000
    assert qualify_delta(metric("0"), baseline, 3600000)["value"] == "-1"


def test_small_genuine_ratio_remains_a_frontend_compatible_fixed_decimal():
    result = qualify_delta(metric("10000001"), metric("10000000", 86400000), 3600000)
    assert result["state"] == "OBSERVED"
    assert result["value"] == "0.0000001"
    assert (
        qualify_delta(metric("9999999"), metric("10000000", 86400000), 3600000)["value"]
        == "-0.0000001"
    )
    extreme = qualify_delta(metric("1e100"), metric("1e-100", 86400000), 3600000)
    assert extreme["value"] is None
    assert extreme["reason"] == "history_precision_budget"


@pytest.mark.parametrize("unit", ["UNKNOWN", "unknown", "Unknown", "UNKNOWN/BTC", "USD/UNKNOWN"])
def test_unknown_units_never_record_or_qualify_even_when_they_match(unit):
    current, baseline = metric("120", unit=unit), metric("100", 86400000, unit)
    result = qualify_delta(current, baseline, 3600000)
    assert result["state"] == "UNAVAILABLE"
    assert result["value"] is None
    assert result["reason"] == "history_unit_unqualified"
    assert (
        fresh_points(
            {"rows": [{"symbol": "BTCUSDT", "metrics": {"open_interest": current}}]}, 90000
        )
        == []
    )
    assert qualify_delta(metric(), baseline, 3600000)["reason"] == "history_unit_unqualified"


@pytest.mark.parametrize(
    "baseline,reason",
    [
        (None, "history_not_observed"),
        (metric("100", 86400001), "history_window_unqualified"),
        (metric("100", 86099999), "history_window_unqualified"),
        (metric("0", 86400000), "history_baseline_nonpositive"),
        (metric("100", 86400000, "USD"), "history_unit_mismatch"),
        (metric("100", 86400000, source="different"), "history_source_mismatch"),
        (metric("NaN", 86400000), "history_value_invalid"),
    ],
)
def test_missing_or_incompatible_history_is_unavailable(baseline, reason):
    result = qualify_delta(metric(), baseline, 3600000)
    assert result["state"] == "UNAVAILABLE"
    assert result["value"] is None
    assert result["reason"] == reason


def test_only_genuine_fresh_observations_are_recorded():
    row = {
        "symbol": "BTCUSDT",
        "metrics": {"price": metric(unit="USDT/BTC"), "open_interest": metric()},
    }
    snapshot = {"rows": [row]}
    points = fresh_points(snapshot, 90000)
    assert len(points) == 1
    assert set(points[0]["metrics"]) == {"price", "open_interest"}
    assert fresh_points(snapshot, 90300) == []  # TTL boundary is stale.
    row["metrics"]["price"]["state"] = "STALE"
    row["metrics"]["open_interest"]["source_time_ms"] = 90000001
    assert fresh_points(snapshot, 90000) == []


def test_stale_current_and_unobserved_baseline_never_derive():
    current, baseline = metric(), metric("100", 86400000)
    current["state"] = "STALE"
    assert qualify_delta(current, baseline, 3600000)["reason"] == "current_observation_unavailable"
    current["state"] = "OBSERVED"
    baseline["state"] = "UNKNOWN"
    assert qualify_delta(current, baseline, 3600000)["reason"] == "history_observation_unavailable"


def test_enrich_keeps_missing_history_unavailable_and_preserves_stale_values():
    snapshot = {
        "rows": [
            {
                "symbol": "BTCUSDT",
                "metrics": {"price": metric(unit="USD/BTC"), "open_interest": metric()},
            }
        ],
        "revision": "before",
    }
    baseline = {
        "price": metric("100", 86400000, "USD/BTC"),
        "open_interest": metric("100", 86400000),
    }
    enrich_snapshot(snapshot, {("BTCUSDT", 3600000, 90000000): baseline}, 90000)
    metrics = snapshot["rows"][0]["metrics"]
    assert metrics["price_change_1h"]["value"] == "0.2"
    assert metrics["oi_change_1h"]["value"] == "0.2"
    assert metrics["oi_change_24h"]["value"] is None
    assert snapshot["revision"] != "before"
    previous = deepcopy(metrics["price_change_1h"])
    metrics["price"]["state"] = "STALE"
    metrics["open_interest"]["state"] = "STALE"
    enrich_snapshot(snapshot, {}, 90001)
    assert metrics["price_change_1h"] == previous


@pytest.mark.integration
def test_postgres_genuine_history_survives_restart_and_derives_qualified_deltas(stores):
    a, b = stores
    assert a.claim("linear")
    symbol = "HISTORY" + uuid4().hex

    def publish(stamp, price, oi):
        snapshot = {
            "revision": "raw",
            "last_success": stamp / 1000,
            "source_time_ms": stamp,
            "source_time_error": None,
            "rows": [
                {
                    "symbol": symbol,
                    "category": "linear",
                    "status": "Trading",
                    "contract_type": "LinearPerpetual",
                    "contract_kind": "perpetual",
                    "settle_coin": "USDT",
                    "metrics": {
                        "price": metric(price, stamp, "USDT/BTC"),
                        "open_interest": metric(oi, stamp, "BTC"),
                    },
                }
            ],
        }
        attempt = {"at": stamp / 1000, "completeness": "EXHAUSTED", "loaded": 1, "last_error": None}
        a.publish("linear", snapshot, attempt, stamp / 1000 + 300, stamp / 1000 + 30, 0)

    publish(3600000, "10", "100")
    assert a.read("linear")["snapshot"]["rows"][0]["metrics"]["oi_change_24h"]["value"] is None
    publish(86400000, "10000000", "200")
    publish(90000000, "10000001", "300")
    publish(90000000, "10000001", "300")  # Same source observation is idempotent.
    a.close()
    assert b.claim("linear")
    snapshot = b.read("linear")["snapshot"]
    metrics = snapshot["rows"][0]["metrics"]
    assert metrics["price_change_1h"]["value"] == "0.0000001"
    assert metrics["oi_change_1h"]["value"] == "0.5"
    assert metrics["oi_change_24h"]["value"] == "2"
    assert metrics["oi_change_24h"]["baseline_source_time_ms"] == 3600000
    assert metrics["oi_change_24h"]["baseline_unit"] == "BTC"
    from src.api.db import connect

    with connect(b.dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tester_catalog.ticker_observations "
                "WHERE category=%s AND symbol=%s",
                ("linear", symbol),
            ).fetchone()[0]
            == 3
        )


@pytest.mark.integration
def test_postgres_retention_and_missing_history_never_invent_values(stores):
    a, _ = stores
    assert a.claim("inverse")
    symbol = "RETENTION" + uuid4().hex
    for stamp, oi in [(3600000, "0"), (86400000, "0"), (90000000, "10"), (100000000, "11")]:
        snapshot = {
            "revision": "raw",
            "rows": [
                {
                    "symbol": symbol,
                    "metrics": {
                        "price": metric("120", stamp, "USD/BTC"),
                        "open_interest": metric(oi, stamp, "USD"),
                    },
                }
            ],
        }
        attempt = {"at": stamp / 1000, "last_error": None, "completeness": "EXHAUSTED", "loaded": 1}
        a.publish("inverse", snapshot, attempt, stamp / 1000 + 300, stamp / 1000 + 30, 0)
        if stamp == 90000000:
            metrics = snapshot["rows"][0]["metrics"]
            assert metrics["oi_change_1h"]["value"] is None
            assert metrics["oi_change_1h"]["reason"] == "history_baseline_nonpositive"
            assert metrics["oi_change_24h"]["value"] is None
    from src.api.db import connect

    with connect(a.dsn) as conn:
        stamps = conn.execute(
            "SELECT source_time_ms FROM tester_catalog.ticker_observations "
            "WHERE category=%s AND symbol=%s ORDER BY source_time_ms",
            ("inverse", symbol),
        ).fetchall()
    assert stamps == [(86400000,), (90000000,), (100000000,)]


@pytest.mark.integration
def test_postgres_unknown_units_not_recorded_and_legacy_baselines_never_qualify(stores):
    from psycopg.types.json import Jsonb

    from src.api.db import connect

    a, _ = stores
    assert a.claim("linear")
    symbol = "UNKNOWNUNIT" + uuid4().hex
    snapshot = {
        "revision": "raw",
        "rows": [
            {
                "symbol": symbol,
                "metrics": {
                    "price": metric("120", unit="UNKNOWN"),
                    "open_interest": metric("120", unit="UNKNOWN"),
                },
            }
        ],
    }
    attempt = {"at": 90000, "last_error": None, "completeness": "EXHAUSTED", "loaded": 1}
    a.publish("linear", snapshot, attempt, 90300, 90030, 0)
    with connect(a.dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tester_catalog.ticker_observations "
                "WHERE category=%s AND symbol=%s",
                ("linear", symbol),
            ).fetchone()[0]
            == 0
        )
        # Simulate a durable baseline produced by the old qualification bug.
        conn.execute(
            "INSERT INTO tester_catalog.ticker_observations VALUES (%s,%s,%s,%s)",
            (
                "linear",
                symbol,
                86400000,
                Jsonb(
                    {
                        "price": metric("100", 86400000, "UNKNOWN"),
                        "open_interest": metric("100", 86400000, "UNKNOWN"),
                    }
                ),
            ),
        )
    snapshot["rows"][0]["metrics"] = {
        "price": metric("120", unit="USDT/BTC"),
        "open_interest": metric("120", unit="BTC"),
    }
    a.publish("linear", snapshot, attempt, 90300, 90030, 0)
    committed = a.read("linear")["snapshot"]["rows"][0]["metrics"]
    for name in ("price_change_1h", "oi_change_1h"):
        assert committed[name]["value"] is None
        assert committed[name]["reason"] == "history_unit_unqualified"
