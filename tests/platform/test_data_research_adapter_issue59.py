"""DATA #72 producer schema → strict RESEARCH adapter regression matrix.

All examples represent PUBLIC recent executions only, NOT full market history.
"""
import base64
import copy
import hashlib
import json
from urllib.parse import urlencode

import pytest

from src.integration.data_research_adapter import project_data_page, read_data_metrics
from src.storage.models import canonical, digest, normalize

START = 1704067200000
STAMP = START + 1000
END = START + 60000
OBSERVED = "2026-11-13T16:00:00+00:00"


def source_row(trade_id="a", price="100", size="2", side="Buy", time=STAMP):
    return {"symbol": "BTCUSDT", "execId": trade_id, "time": str(time),
            "price": price, "size": size, "side": side}


def make_page(raw_rows=None, *, quantity_unit="source_native_quantity"):
    raw_rows = [source_row()] if raw_rows is None else raw_rows
    params = {"category": "linear", "symbol": "BTCUSDT", "limit": 1000}
    url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(params)
    raw_http = json.dumps({"retCode": 0, "result": {"list": raw_rows}}).encode()
    http_sha = hashlib.sha256(raw_http).hexdigest()
    run_id, job_key, evidence_id = "ci-public-research-run", "ci-public-research-job", "evidence-1"
    start, end = min(int(r["time"]) for r in raw_rows), max(int(r["time"]) for r in raw_rows) + 1
    receipt = {
        "venue": "bybit", "category": "linear", "symbol": "BTCUSDT",
        "dataset": "trades", "interval": "", "start_ms": start, "end_ms": end,
        "status": "UNVERIFIED", "coverage_status": "UNKNOWN",
        "ingestion_run": run_id, "source_url": url,
        "observed_at_utc": OBSERVED, "http_sha256": http_sha,
        "http_hash_scope": "original_http_body",
        "http_bytes_base64": base64.b64encode(raw_http).decode(),
        "cursor_in": None,
        "pages": [{"params": params, "cursor": None, "next_cursor": None,
                   "count": len(raw_rows), "http_sha256": http_sha}],
    }
    evidence = {
        "evidence_id": evidence_id, "job_key": job_key,
        "start_ms": start, "end_ms": end,
        "status": "UNVERIFIED", "verified": False,
        "record_count": len(raw_rows), "evidence": receipt,
    }
    raw_envelopes, rows, observations = [], [], []
    for row in raw_rows:
        source = {
            "url": url, "fetched_at": OBSERVED,
            "sha256": hashlib.sha256(canonical(row).encode()).hexdigest(),
            "hash_scope": "canonical_row_json",
            "source_kind": "rest",
            "source_timestamp": str(row["time"]),
            "observed_at_utc": OBSERVED, "ingestion_run": run_id,
            "evidence_refs": [digest([run_id, job_key, url, http_sha])],
        }
        payload = {
            "price": row["price"], "size": row["size"], "side": row["side"],
            "trade_id": row["execId"], "unit": quantity_unit,
            "event_timestamp_ms": int(row["time"]),
            "publication_timestamp_ms": None,
            "anonymous_public_execution": True, "source_fields": row,
        }
        envelope = normalize({
            "schema_version": "1.0", "venue": "bybit", "category": "linear",
            "symbol": "BTCUSDT", "dataset": "trades", "interval": "",
            "timestamp_ms": int(row["time"]), "timestamp_precision": "ms",
            "timestamp_ns": None, "payload": payload, "source": source,
        })
        raw_envelopes.append(envelope)
        rows.append({k: v for k, v in envelope.items() if k != "source"})
        observations.append({
            "record_key": envelope["record_key"], "authentic_run": True,
            "source_run_status": "matched", "run_origin": "caller",
            "source": source, "raw_envelope": envelope,
            "source_kind": "rest", "run_id": run_id, "job_key": job_key,
            "evidence_id": evidence_id,
        })
    page = {
        "outcome": "OK", "scope": {"venue": "bybit", "category": "linear",
            "symbol": "BTCUSDT", "dataset": "trades", "interval": ""},
        "rows": rows, "observations": observations, "evidence": [evidence],
        "page_exhausted": True, "next_cursor": None,
        "coverage": {"state": "UNKNOWN", "complete": False},
    }
    return page, raw_envelopes, receipt


def project(page):
    return project_data_page(page, category="linear", symbol="BTCUSDT",
                             start_ms=START, end_ms=END)


class Store:
    def __init__(self, page):
        self.page = page
        self.calls = []

    def read_page(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.page


def test_data72_exact_projection_and_mathematics():
    page, _, _ = make_page([source_row(), source_row("b", "102", "1", "Sell")])
    data = project(page)
    assert data["coverage"] == "UNKNOWN"
    assert data["admitted_to_f"] is False
    assert data["trades"][0]["quantity"] == "2"
    assert data["trades"][0]["provenance"][0]["source_hash"]
    store = Store(page)
    result = read_data_metrics(store, category="linear", symbol="BTCUSDT",
                               start_ms=START, end_ms=END,
                               interval_ms=60000, tick_size="0.5")
    assert store.calls[0][0][0] == "trades"
    assert store.calls[0][1]["limit"] == 1000
    bar = result["metrics"]["bars"][0]
    assert bar["vwap"].startswith("100.666666666")
    assert bar["poc"] == "100"
    assert bar["delta_known"] == "1"
    assert bar["cvd_known"] == "1"
    assert bar["delta_complete"] is False
    assert result["f_input"] is None
    assert result["g_e_result"] is None


def test_source_order_does_not_change_metrics():
    rows = [source_row("a", "100", "2"), source_row("b", "102", "1", "Sell")]
    outcomes = []
    for order in (rows, list(reversed(rows))):
        store = Store(make_page(order)[0])
        result = read_data_metrics(store, category="linear", symbol="BTCUSDT",
                                   start_ms=START, end_ms=END,
                                   interval_ms=60000, tick_size="0.5")
        outcomes.append(result["metrics"]["bars"])
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("mutation,reason", [
    (lambda p: p["evidence"][0]["evidence"].pop("http_bytes_base64"), "missing_or_oversized_http_body"),
    (lambda p: p["evidence"][0]["evidence"].__setitem__("http_sha256", "0" * 64), "http_body_sha256_mismatch"),
    (lambda p: p["observations"][0]["source"].__setitem__("sha256", "0" * 64), "wrong_provenance_source_kind"),
    (lambda p: p["rows"][0]["payload"].__setitem__("unit", "inverse_contracts"), "source_envelope_readback_mismatch"),
    (lambda p: p["rows"][0]["payload"].__setitem__("publication_timestamp_ms", 1), "source_envelope_readback_mismatch"),
    (lambda p: p.__setitem__("page_exhausted", False), "missing_source_records_or_more_pages"),
    (lambda p: p["evidence"][0]["evidence"]["pages"][0].__setitem__("next_cursor", "more"), "unproven_pagination_chain"),
    (lambda p: p["observations"].clear(), "missing_trade_observations"),
])
def test_source_tampering_refused(mutation, reason):
    page, _, _ = make_page()
    mutation(page)
    with pytest.raises(ValueError, match=reason):
        project(page)


def test_missing_http_page_member_refused():
    page, _, _ = make_page([source_row(), source_row("b", "102", "1", "Sell")])
    page["rows"].pop()
    page["observations"].pop()
    with pytest.raises(ValueError, match="missing_page_members_or_duplicate_ids"):
        project(page)


def test_late_knowledge_refused_no_lookahead():
    page, _, _ = make_page()
    with pytest.raises(ValueError, match="late_source_knowledge"):
        project_data_page(page, category="linear", symbol="BTCUSDT",
                          start_ms=START, end_ms=END, as_of_ms=START + 60000)


def test_wrong_dataset_and_market_refused():
    page, _, _ = make_page()
    page["scope"]["dataset"] = "funding"
    with pytest.raises(ValueError, match="data_page_unavailable_or_wrong_scope"):
        project(page)
    page, _, _ = make_page()
    with pytest.raises(ValueError, match="inverse_or_unsupported_quantity_unit"):
        project_data_page(page, category="inverse", symbol="BTCUSDT",
                          start_ms=START, end_ms=END)


def test_unknown_side_never_synthesized():
    with pytest.raises(ValueError, match="invalid taker side"):
        make_page([source_row(side="Unknown")])


def test_zero_volume_refused_at_data_contract_boundary():
    with pytest.raises(ValueError, match="invalid size"):
        make_page([source_row(size="0")])


def test_duplicate_exchange_identity_refused():
    page, _, _ = make_page([source_row("a", "100"), source_row("a", "101")])
    with pytest.raises(ValueError, match="duplicate_source_record_key"):
        project(page)


def test_missing_unit_cannot_be_guessed():
    page, _, _ = make_page(quantity_unit="contracts_unknown")
    with pytest.raises(ValueError, match="unknown_or_incompatible_quantity_unit"):
        project(page)


def test_actual_data72_decoder_when_composed():
    """Runs on MASTER's combined DATA+RESEARCH candidate; otherwise explicit SKIP."""
    import importlib.util
    from datetime import datetime

    if importlib.util.find_spec("src.ingest.public_auxiliary") is None:
        pytest.skip("PR #72 module not yet composed into this independent PR")
    from src.ingest.public_auxiliary import decode

    page, raw_envelopes, receipt = make_page()
    row = source_row()
    expected = decode(
        "trades", "linear", "BTCUSDT",
        {"retCode": 0, "result": {"list": [row]}},
        fetched_at=datetime.fromisoformat(OBSERVED),
        url=receipt["source_url"], ingestion_run=receipt["ingestion_run"],
        evidence_ref=page["observations"][0]["source"]["evidence_refs"][0],
    )
    assert expected == raw_envelopes
    assert project(page)["admitted_to_f"] is False
