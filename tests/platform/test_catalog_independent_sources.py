from src.api.market_catalog import AcquisitionError, CatalogService


class Source:
    def __init__(self):
        self.fail_tickers = False
        self.symbols = ["KEEP", "REMOVE"]
        self.now = 1000
        self.cancel = None

    def call(self, endpoint, params):
        category = params["category"]
        if endpoint == "/tickers":
            if self.cancel:
                self.cancel.set()
            if self.fail_tickers:
                raise AcquisitionError("rate_limited")
            return {
                "category": category,
                "_source_time_ms": self.now * 1000,
                "list": [
                    {"symbol": s, "lastPrice": "10", "volume24h": "2", "openInterest": "4"}
                    for s in self.symbols
                ],
            }, "https://api.bybit.com/v5/market/tickers"
        return {
            "category": category,
            "_source_time_ms": self.now * 1000,
            "nextPageCursor": "",
            "list": [
                {
                    "symbol": s,
                    "status": "Trading",
                    "contractType": "LinearPerpetual"
                    if category == "linear"
                    else "InversePerpetual",
                    "baseCoin": "BTC",
                    "quoteCoin": "USDT" if category == "linear" else "USD",
                    "settleCoin": "BTC",
                }
                for s in self.symbols
            ]
            if params["status"] == "Trading"
            else [],
        }, "https://api.bybit.com/v5/market/instruments-info"


def test_first_ticker_failure_publishes_complete_catalog_with_unavailable_metrics():
    source = Source()
    source.fail_tickers = True
    service = CatalogService(source, clock=lambda: source.now)
    assert not service.refresh("linear")
    result = service.query("linear")
    assert result["total"] == 2
    assert result["catalog"]["completeness"] == "EXHAUSTED"
    assert result["catalog"]["state"] == "CURRENT"
    assert result["catalog"]["last_error"] is None
    assert result["tickers"]["state"] == "UNAVAILABLE"
    assert result["tickers"]["last_error"] == "rate_limited"
    assert result["rows"][0]["metrics"]["price"]["value"] is None
    assert not service.refresh("linear")
    assert service.query("linear")["tickers"]["state"] == "UNAVAILABLE"


def test_ticker_failure_preserves_old_metrics_times_and_refreshes_listing():
    source = Source()
    service = CatalogService(source, clock=lambda: source.now)
    assert service.refresh("linear")
    old = service.query("linear")
    source.now = 1100
    source.symbols = ["KEEP", "NEW"]
    source.fail_tickers = True
    assert not service.refresh("linear")
    result = service.query("linear")
    assert result["revision"] != old["revision"]
    rows = {r["symbol"]: r for r in result["rows"]}
    assert set(rows) == {"KEEP", "NEW"}
    metric = rows["KEEP"]["metrics"]["price"]
    assert (metric["value"], metric["state"], metric["observed_at"], metric["source_time_ms"]) == (
        "10",
        "STALE",
        1000,
        1000000,
    )
    assert rows["NEW"]["metrics"]["price"]["value"] is None
    assert result["tickers"]["last_success"] == 1000
    assert result["catalog"]["last_success"] == 1100


def test_cancellation_during_ticker_failure_never_publishes_listing():
    source = Source()
    service = CatalogService(source, clock=lambda: source.now)
    source.cancel = service._cancel
    source.fail_tickers = True
    assert not service.refresh("linear")
    assert service.query("linear")["revision"] is None


def test_explicit_units_respect_linear_and_inverse_contracts():
    source = Source()
    service = CatalogService(source, clock=lambda: source.now)
    for category, expected in [("linear", "BTC"), ("inverse", "USD")]:
        assert service.refresh(category)
        row = service.query(category)["rows"][0]
        assert row["base_coin"] == "BTC"
        assert row["metrics"]["volume_24h"]["unit"] == expected
        assert row["metrics"]["open_interest"]["unit"] == expected
        assert row["metrics"]["price_change_24h"]["unit"] == "ratio"
        assert row["metrics"]["price"]["unit"] == (
            "USDT/BTC" if category == "linear" else "USD/BTC"
        )


def test_metric_freshness_uses_own_timestamp_and_retains_derived_observation():
    source = Source()
    service = CatalogService(source, clock=lambda: source.now)
    assert service.refresh("linear")
    row = service._snapshots["linear"]["rows"][0]
    row["metrics"]["price_change_1h"] = {
        "value": "0.2",
        "unit": "ratio",
        "state": "OBSERVED",
        "reason": None,
        "source": "observation_history",
        "observed_at": 1000,
        "source_time_ms": 1000000,
    }
    row["metrics"]["open_interest"]["source_time_ms"] = 600000
    result = service.query("linear")
    assert result["catalog"]["state"] == "CURRENT"
    assert result["rows"][0]["metrics"]["open_interest"]["state"] == "STALE"
    assert result["rows"][0]["metrics"]["price"]["state"] == "OBSERVED"
    source.fail_tickers = True
    source.now = 1100
    assert not service.refresh("linear")
    metric = service.query("linear")["rows"][0]["metrics"]["price_change_1h"]
    assert (metric["value"], metric["unit"], metric["source_time_ms"], metric["state"]) == (
        "0.2",
        "ratio",
        1000000,
        "STALE",
    )


def test_catalog_source_age_expires_independently_before_local_refresh_ttl():
    source = Source()
    original = source.call

    def old_catalog(endpoint, params):
        result, url = original(endpoint, params)
        if endpoint == "/instruments-info":
            result["_source_time_ms"] = 800000
        return result, url

    source.call = old_catalog
    service = CatalogService(source, clock=lambda: source.now)
    assert service.refresh("linear")
    source.now = 1101
    result = service.query("linear")
    assert result["catalog"]["state"] == "STALE"
    assert result["rows"][0]["metrics"]["price"]["state"] == "OBSERVED"


def test_catalog_and_ticker_last_success_are_independent_completion_times():
    source = Source()
    original = source.call

    def slow_ticker(endpoint, params):
        if endpoint == "/tickers":
            source.now += 100
        return original(endpoint, params)

    source.call = slow_ticker
    service = CatalogService(source, clock=lambda: source.now)
    assert service.refresh("linear")
    result = service.query("linear")
    assert result["catalog"]["last_success"] == 1000
    assert result["tickers"]["last_success"] == 1100
