"""RESEARCH consumer for DATA #72 versioned public execution events.

When composed, DATA alone verifies original HTTP/page/row evidence and performs
bounded PostgreSQL readback. RESEARCH does not re-invent that proof authority.
Neither a versioned event nor a successful projection certifies full history,
publication time, Bid/Ask, or admission to F Strategy Lab.
"""
import importlib
import re

from src.analytics.core import validate_trade
from src.analytics.research_metrics import calculate
from src.storage.models import digest

DATA_CONTRACT_VERSION = "tester-public-execution/1"
REVIEWED_DATA_HEAD = "9193622249a5c659d53b9848c13fd363a86f3b4a"
MAX_EVENTS = 100
MAX_WINDOW_MS = 7 * 86400000
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_SYMBOL = re.compile(r"[A-Z0-9]{2,40}\Z")


def _scope(category, symbol, start_ms, end_ms, limit, as_of_ms):
    if category != "linear":
        raise ValueError("inverse_quantity_unit_not_comparable")
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise ValueError("invalid_market_symbol")
    if any(type(v) is not int for v in (start_ms, end_ms, limit)):
        raise ValueError("integer_time_window_required")
    if not (0 <= start_ms < end_ms and end_ms - start_ms <= MAX_WINDOW_MS):
        raise ValueError("bounded_source_window_required")
    if not 1 <= limit <= MAX_EVENTS:
        raise ValueError("source_event_limit_exceeded")
    if as_of_ms is not None and (type(as_of_ms) is not int or as_of_ms < 0):
        raise ValueError("invalid_as_of_ms")


def _trade(event, *, category, symbol, start_ms, end_ms, as_of_ms):
    """Validate the typed DATA event; DOES NOT independently verify its HTTP bytes."""
    if not isinstance(event, dict) or event.get("contract_version") != DATA_CONTRACT_VERSION:
        raise ValueError("unknown_data_execution_contract")
    if any(event.get(k) != v for k, v in (
        ("venue", "bybit"), ("category", category), ("symbol", symbol),
        ("quantity_unit", "source_native_linear_size"),
    )):
        raise ValueError("market_or_quantity_unit_mismatch")
    event_ms, observed_ms = event.get("event_ms"), event.get("observed_ms")
    if (type(event_ms) is not int or type(observed_ms) is not int
            or not start_ms <= event_ms < end_ms or observed_ms < event_ms):
        raise ValueError("invalid_event_or_observation_time")
    if as_of_ms is not None and observed_ms > as_of_ms:
        raise ValueError("late_source_knowledge")
    if event.get("publication_ms", "MISSING") is not None:
        raise ValueError("publication_time_not_proven")
    scope = {"start_ms": start_ms, "end_ms": end_ms}
    if event.get("utc_interval") != scope or event.get("source_coverage") != {
        "status": "UNKNOWN", **scope
    }:
        raise ValueError("historical_scope_or_coverage_mismatch")
    availability = event.get("availability")
    if (not isinstance(availability, dict)
            or availability.get("status") != "UNVERIFIED"
            or not isinstance(availability.get("reason"), str)
            or not availability["reason"]):
        raise ValueError("source_availability_not_proven")
    side = event.get("taker_side")
    if side not in ("Buy", "Sell", "Unknown"):
        raise ValueError("invalid_or_inferred_taker_side")
    receipts = event.get("source_receipts")
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("original_http_page_row_receipts_required")
    evidence_ids = set()
    for receipt in receipts:
        if not isinstance(receipt, dict) or not isinstance(receipt.get("evidence_id"), str) or not receipt["evidence_id"]:
            raise ValueError("invalid_source_receipt")
        if not isinstance(receipt.get("page_ref"), str) or not _HEX.fullmatch(receipt["page_ref"]):
            raise ValueError("invalid_page_reference")
        for field in ("http_sha256", "page_sha256", "row_sha256"):
            if not isinstance(receipt.get(field), str) or not _HEX.fullmatch(receipt[field]):
                raise ValueError("unqualified_source_sha256")
        evidence_ids.add(receipt["evidence_id"])
    provenance = event.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        raise ValueError("source_provenance_required")
    for receipt in receipts:
        expected_parents = {
            receipt["evidence_id"], receipt["page_sha256"], receipt["http_sha256"]
        }
        if not any(
            isinstance(p, dict) and p.get("source_kind") == "bybit_rest"
            and p.get("source_hash") == receipt["row_sha256"]
            and isinstance(p.get("parent_refs"), list)
            and expected_parents.issubset(p["parent_refs"])
            for p in provenance
        ):
            raise ValueError("source_receipt_provenance_mismatch")
    result = {
        "schema_version": "1.0", "venue": "bybit", "category": category,
        "symbol": symbol, "timestamp_ms": event_ms, "timestamp_ns": None,
        "timestamp_precision": "ms", "trade_id": event.get("exec_id"),
        "id_origin": "exchange", "price": event.get("price"),
        "quantity": event.get("quantity"), "taker_side": side,
        "provenance": event.get("provenance"), "availability": availability,
        "coverage": {"status": "UNKNOWN", **scope,
                     "evidence_refs": sorted(evidence_ids)},
    }
    return validate_trade(result)


def project_events(events, *, category, symbol, start_ms, end_ms,
                   interval_ms, tick_size, as_of_ms=None, limit=MAX_EVENTS):
    """Pure arithmetic fixture seam; does NOT attest the caller's source records.

    For actual source-backed calculations call read_data_metrics(), which reads
    via DATA's verified PostgreSQL event repository. Always F-fail-closed.
    """
    _scope(category, symbol, start_ms, end_ms, limit, as_of_ms)
    if not isinstance(events, list) or len(events) > limit:
        raise ValueError("events_exceed_bounded_read")
    trades = []
    seen = set()
    for event in events:
        trade = _trade(event, category=category, symbol=symbol,
                       start_ms=start_ms, end_ms=end_ms, as_of_ms=as_of_ms)
        identity = trade["trade_id"]
        if identity in seen:
            raise ValueError("duplicate_exchange_exec_id")
        seen.add(identity)
        trades.append(trade)
    trades.sort(key=lambda t: (t["timestamp_ms"], t["trade_id"]))
    metrics = calculate(trades, start_ms=start_ms, end_ms=end_ms,
                        interval_ms=interval_ms, tick_size=tick_size)
    window = {
        "start_ms": start_ms, "end_ms": end_ms,
        "first_event_ms": trades[0]["timestamp_ms"] if trades else None,
        "last_event_ms": trades[-1]["timestamp_ms"] if trades else None,
        "observed_unique_trades": len(trades),
        "hit_bounded_event_limit": len(trades) == limit,
        "complete_market_history": False,
    }
    return {
        "schema_version": "tester.data-research-preview/2",
        "data_contract_version": DATA_CONTRACT_VERSION,
        "status": "OBSERVED_UNVERIFIED" if trades else "NO_DATA",
        "coverage": "UNKNOWN", "quantity_unit": "source_native_linear_size",
        "publication_time": "UNKNOWN", "source_verification": "CALLER_UNATTESTED",
        "observed_window": window,
        "data_execution_digest": digest(trades), "metrics_digest": digest(metrics),
        "metrics": metrics, "admitted_to_f": False, "f_input": None, "g_e_result": None,
        "reasons": ["historical_visibility_not_proven", "recent_trade_not_full_history"],
    }


def read_data_metrics(store, *, category, symbol, start_ms, end_ms, interval_ms,
                      tick_size, as_of_ms=None, limit=MAX_EVENTS):
    """Read DATA's authenticated PostgreSQL contract; no writes or F execution.

    An independent RESEARCH-only PR is expected to refuse this call until DATA
    PR #72 is composed by MASTER. Never silently downgrade to an unverified
    direct storage row or to mocked HTTP requests.
    """
    _scope(category, symbol, start_ms, end_ms, limit, as_of_ms)
    try:
        data = importlib.import_module("src.ingest.execution_contract")
    except ModuleNotFoundError as exc:
        if getattr(exc, "name", None) not in ("src.ingest.execution_contract",):
            raise
        raise ValueError("data72_versioned_contract_not_composed") from exc
    if data.VERSION != DATA_CONTRACT_VERSION:
        raise ValueError("data_execution_contract_version_skew")
    events = data.read_execution_events(
        store, category=category, symbol=symbol,
        start_ms=start_ms, end_ms=end_ms, limit=limit
    )
    # DATA verifies the original HTTP bytes/page/row, storage hashes, B
    # source/evidence linkage and run identity. Independently confirm the
    # projected analytics shape, never invent source availability.
    preview = project_events(
        events, category=category, symbol=symbol,
        start_ms=start_ms, end_ms=end_ms, interval_ms=interval_ms,
        tick_size=tick_size, as_of_ms=as_of_ms, limit=limit
    )
    for event in events:
        trade = _trade(event, category=category, symbol=symbol,
                       start_ms=start_ms, end_ms=end_ms, as_of_ms=as_of_ms)
        if data.to_research_trade(event) != trade:
            raise ValueError("data_research_projection_contract_mismatch")
    preview["source_verification"] = "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS"
    preview["data_source_receipts_digest"] = digest([
        event["source_receipts"] for event in sorted(
            events, key=lambda e: (e["event_ms"], e["exec_id"])
        )
    ])
    return preview
