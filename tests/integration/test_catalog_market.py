from types import SimpleNamespace

import pytest

from src.api.models import DownloadRequest, Query
from src.api.providers import ProviderUnavailable


def request(**changes):
    return DownloadRequest(
        **(
            dict(
                category="linear",
                symbol="SUIUSDT",
                source="bybit",
                kind="candles",
                interval="1",
                start_ms=0,
                end_ms=600000,
            )
            | changes
        )
    )


def row(category="linear", symbol="SUIUSDT", **changes):
    return (
        dict(
            category=category,
            symbol=symbol,
            status="Trading",
            contract_kind="perpetual",
            contract_type="LinearPerpetual" if category == "linear" else "InversePerpetual",
        )
        | changes
    )


@pytest.mark.parametrize(
    "interval", ["1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M"]
)
def test_full_catalog_symbol_outside_old_ten_supports_all_intervals(interval):
    from src.integration.catalog_market import validate_catalog_download

    called = []

    def lookup(category, symbol):
        called.append((category, symbol))
        return row(category, symbol)

    validate_catalog_download(request(interval=interval), lookup)
    assert called == [("linear", "SUIUSDT")]


@pytest.mark.parametrize(
    "category,symbol,contract",
    [
        ("inverse", "BTCUSD", "InversePerpetual"),
        ("linear", "BTCUSDT-27NOV26", "LinearFutures"),
        ("inverse", "BTCUSDZ26", "InverseFutures"),
    ],
)
def test_both_futures_categories_and_contract_kinds(category, symbol, contract):
    from src.integration.catalog_market import validate_catalog_download

    validate_catalog_download(
        request(category=category, symbol=symbol),
        lambda c, s: row(
            c,
            s,
            contract_type=contract,
            contract_kind="dated" if contract.endswith("Futures") else "perpetual",
        ),
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        row(symbol="OTHERUSDT"),
        row(category="inverse"),
        row(contract_kind="unknown"),
        row(contract_type="InversePerpetual"),
        row(status="UNKNOWN"),
    ],
)
def test_missing_or_mismatched_catalog_identity_refused(value):
    from src.integration.catalog_market import validate_catalog_download

    with pytest.raises(ProviderUnavailable):
        validate_catalog_download(request(), lambda *_: value)


@pytest.mark.parametrize(
    "changes",
    [
        dict(category="spot"),
        dict(source="other"),
        dict(kind="trades"),
        dict(interval="2"),
        dict(end_ms=86400001),
    ],
)
def test_scope_and_public_history_budget_remain_bounded(changes):
    from src.integration.catalog_market import validate_catalog_download

    with pytest.raises(ProviderUnavailable):
        validate_catalog_download(request(**changes), lambda c, s: row(c, s))


def test_legacy_ten_symbol_guard_is_unchanged():
    from src.integration.lab_market import validate_download

    with pytest.raises(ProviderUnavailable):
        validate_download(request())


def test_catalog_reader_declares_public_candles_for_inverse_without_fake_coverage():
    from src.integration.catalog_market import CatalogReader

    reader = CatalogReader(None)
    response = reader.read("sources", Query(category="inverse"))
    source = response["data"][0]
    assert source["id"] == "bybit" and source["availability"] == "partial"
    assert source["kinds"] == ["candles"]
    assert "unknown" in source["reason"].lower()


def test_handler_validation_precedes_any_acquisition(tmp_path):
    from src.integration.lab_market import LabHandler

    calls = []

    def refused(value):
        calls.append(value.symbol)
        raise ProviderUnavailable("fixture refusal")

    handler = LabHandler(SimpleNamespace(data_dir=tmp_path), validator=refused)
    with pytest.raises(ProviderUnavailable):
        handler.run(request(), {}, None, None, None)
    assert calls == ["SUIUSDT"]
    assert not list(tmp_path.iterdir())
