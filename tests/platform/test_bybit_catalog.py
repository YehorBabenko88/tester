import threading

import pytest

from src.api.bybit_catalog import AcquisitionError, BybitCatalogSource, acquire_instruments


class Source:
    def __init__(self, cycle=False):
        self.calls = []
        self.cycle = cycle

    def call(self, endpoint, params):
        self.calls.append(dict(params))
        page = bool(params.get("cursor"))
        status = params["status"]
        rows = (
            [
                {
                    "symbol": f"{status}{i}",
                    "status": status,
                    "contractType": "LinearPerpetual" if i % 2 else "LinearFutures",
                    "settleCoin": "USDT",
                }
                for i in range(1000 if not page else 1000, 1201)
            ]
            if page
            else [
                {
                    "symbol": f"{status}{i}",
                    "status": status,
                    "contractType": "LinearPerpetual",
                    "settleCoin": "USDT",
                }
                for i in range(1000)
            ]
        )
        return {
            "category": params["category"],
            "list": rows,
            "nextPageCursor": "next" if not page or self.cycle else "",
            "_source_time_ms": 1000000,
        }, "https://api.bybit.com/v5/market/instruments-info"


def test_all_statuses_exhausted_and_contracts_classified():
    source = Source()
    result = acquire_instruments(source, "linear", clock=lambda: 1000)
    assert len(result["rows"]) == 6005
    assert result["pages"] == 10
    assert set(result["status_coverage"]) == {
        "Trading",
        "PreLaunch",
        "PendingOpen",
        "Delivering",
        "Closed",
    }
    assert all(v["completeness"] == "EXHAUSTED" for v in result["status_coverage"].values())
    assert {r["contract_kind"] for r in result["rows"]} == {"perpetual", "dated"}
    assert result["rows"][0]["source_observed_at"] == 1000
    assert result["rows"][0]["source_time_ms"] == 1000000


@pytest.mark.parametrize(
    "options,reason", [({"max_pages": 1}, "page_budget"), ({"max_rows": 10}, "row_budget")]
)
def test_budget_failures_preserve_partial_evidence(options, reason):
    with pytest.raises(AcquisitionError, match=reason) as exc:
        acquire_instruments(Source(), "linear", **options)
    assert exc.value.loaded > 0
    assert exc.value.pages == 1


def test_repeated_cursor_and_cancellation_never_complete():
    with pytest.raises(AcquisitionError, match="repeated_cursor"):
        acquire_instruments(Source(True), "inverse")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(AcquisitionError, match="cancelled"):
        acquire_instruments(Source(), "linear", cancel=cancel)


def test_invalid_json_envelope_is_acquisition_error(monkeypatch):
    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, **kwargs):
            yield b"[]"

    monkeypatch.setattr("src.api.bybit_catalog._public_get", lambda *a, **k: Response())
    with pytest.raises(AcquisitionError, match="invalid_response"):
        BybitCatalogSource().call("/tickers", {"category": "linear"})


def test_exchange_status_filter_mismatch_preserves_actual_status():
    class ActualSource:
        def call(self, endpoint, params):
            return {
                "category": "linear",
                "nextPageCursor": "",
                "list": [
                    {"symbol": "BTCNEXT", "status": "PendingOpen", "contractType": "LinearFutures"}
                ],
            }, "https://api.bybit.com/v5/market/instruments-info"

    result = acquire_instruments(ActualSource(), "linear", statuses=("Closed",))
    assert result["rows"][0]["status"] == "PendingOpen"
    assert result["status_coverage"]["Closed"]["observed_statuses"] == {"PendingOpen": 1}


def test_empty_exchange_scope_has_blank_category_but_terminal_cursor():
    class EmptySource:
        def call(self, *args):
            return {"category": "", "list": [], "nextPageCursor": ""}, "https://api.bybit.com"

    result = acquire_instruments(EmptySource(), "inverse", statuses=("PreLaunch",))
    assert result["rows"] == []
    assert result["status_coverage"]["PreLaunch"]["completeness"] == "EXHAUSTED"


def test_cancellation_during_terminal_call_does_not_return_success():
    cancel = threading.Event()

    class CancellingSource:
        def call(self, *args):
            cancel.set()
            return {
                "category": "inverse",
                "list": [],
                "nextPageCursor": "",
            }, "https://api.bybit.com"

    with pytest.raises(AcquisitionError, match="cancelled"):
        acquire_instruments(CancellingSource(), "inverse", statuses=("Trading",), cancel=cancel)
