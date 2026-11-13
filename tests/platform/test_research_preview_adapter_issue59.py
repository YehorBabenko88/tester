"""Exercise the real B one-page snapshot adapter without inventing coverage."""
import pytest

from src.analytics.core import normalize_trade
from src.integration.qualified_snapshot import SnapshotRequest
from src.integration.research_preview import research_preview


class Store:
    def __init__(self, page):
        self.page = page
        self.calls = []

    def read_page(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.page


def request(revision=None):
    return SnapshotRequest("bybit", "linear", "BTCUSDT", "1", 0, 60000, revision)


def page():
    return {
        "scope": request().scope,
        "outcome": "OK",
        "page_exhausted": True,
        "next_cursor": None,
        "rows": [{"start_ms": 0, "close": "100"}],
        "coverage": {
            "complete": True, "state": "COMPLETE",
            "proof_refs": ["receipt"], "start_ms": 0, "end_ms": 60000,
        },
        "knowledge_time": {"status": "UNAVAILABLE"},
    }


def test_real_adapter_never_promotes_complete_label():
    store = Store(page())
    result = research_preview(store, request(), [], tick_size="0.5")
    assert len(store.calls) == 1
    assert store.calls[0][1]["limit"] == 1000
    assert result["status"] == "UNVERIFIED"
    assert result["f_input"] is None
    assert result["snapshot_admitted"] is False
    assert "historical_visibility_not_proven" in result["reasons"]


def test_revision_mismatch_rejected():
    with pytest.raises(ValueError, match="dataset_revision_mismatch"):
        research_preview(Store(page()), request("a" * 64), [], tick_size="0.5")


def test_wrong_market_page_rejected():
    bad = page()
    bad["scope"] = dict(bad["scope"], symbol="ETHUSDT")
    with pytest.raises(ValueError, match="canonical_scope_mismatch"):
        research_preview(Store(bad), request(), [], tick_size="0.5")


def test_incomplete_page_is_not_accepted():
    bad = page()
    bad["page_exhausted"] = False
    bad["next_cursor"] = "next"
    result = research_preview(Store(bad), request(), [], tick_size="0.5")
    assert "single_page_limit" in result["reasons"]
    assert result["f_input"] is None


def test_actual_execution_math_binds_original_input_and_snapshot():
    source = {
        "source_kind": "bybit_rest", "source_ref": "read-only-fixture",
        "source_hash": None, "ingestion_run": "fixture-run",
        "timestamp_precision": "ms", "source_timestamp": "1",
    }
    trade = normalize_trade(
        {"time": "1", "price": "100", "size": "2",
         "side": "Buy", "execId": "trade-1"},
        category="linear", symbol="BTCUSDT", source_ref=source,
    )
    result = research_preview(Store(page()), request(), [trade], tick_size="0.5")
    assert result["metrics"]["bars"][0]["vwap"] == "100"
    assert result["metrics"]["bars"][0]["delta_known"] == "2"
    assert result["metrics"]["bars"][0]["delta_complete"] is False
    assert result["proof"]["execution_input_digest"]
    assert result["proof"]["snapshot_evidence_digest"]
    assert result["f_input"] is None
