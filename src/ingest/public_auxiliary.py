"""Strict Bybit V5 public auxiliary market data adapter.

No private endpoints, no trader identities, no completeness inference.
Feed the resulting envelopes and evidence to Store.ingest in one transaction.
"""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from src.storage.models import normalize

PATHS = {
    "funding": "/funding/history",
    "open_interest": "/open-interest",
    "trades": "/recent-trade",
}
LIMITS = {"funding": 200, "open_interest": 200, "trades": 1000}
OI_INTERVALS = frozenset(("5min", "15min", "30min", "1h", "4h", "1d"))


def _ms(value, field):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{field} requires an original millisecond timestamp")
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"invalid {field}") from exc
    if result < 0 or str(result) != str(value):
        raise ValueError(f"invalid {field}")
    return result


def _exact(value, field, *, positive=False):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a source decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {field}") from exc
    if not number.is_finite() or (positive and number <= 0):
        raise ValueError(f"invalid {field}")
    return value


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware observation timestamp required")
    return value.astimezone(timezone.utc).isoformat()


def request(kind, category, symbol, *, start_ms=None, end_ms=None, interval=None, limit=None):
    if kind not in PATHS or category not in ("linear", "inverse"):
        raise ValueError("unsupported public dataset/category")
    if not isinstance(symbol, str) or not symbol.isascii() or not symbol.isalnum() or not 1 <= len(symbol) <= 40:
        raise ValueError("invalid symbol")
    cap = LIMITS[kind]
    limit = cap if limit is None else limit
    if type(limit) is not int or not 1 <= limit <= cap:
        raise ValueError("invalid limit")
    params = {"category": category, "symbol": symbol, "limit": limit}
    if kind == "open_interest":
        if interval not in OI_INTERVALS:
            raise ValueError("OI intervalTime required")
        params["intervalTime"] = interval
    elif interval is not None:
        raise ValueError("unexpected interval")
    if kind == "trades":
        if start_ms is not None or end_ms is not None:
            raise ValueError("recent trades do not support historical range")
    elif start_ms is not None or end_ms is not None:
        if start_ms is None or end_ms is None or _ms(start_ms, "start") >= _ms(end_ms, "end"):
            raise ValueError("invalid requested range")
        params.update(startTime=start_ms, endTime=end_ms)
    return PATHS[kind], params


def decode(kind, category, symbol, response, *, fetched_at, url, ingestion_run, evidence_ref):
    """Normalize source rows; retain exact original fields and time roles.

    Return envelopes ready for tester_storage.Store.ingest. HTTP body and
    pagination receipts must be persisted separately as atomic evidence.
    """
    if kind not in PATHS or category not in ("linear", "inverse"):
        raise ValueError("unsupported dataset/category")
    if not isinstance(response, dict) or response.get("retCode") != 0:
        raise ValueError("unsuccessful Bybit response")
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("list"), list):
        raise ValueError("missing result.list")
    observed = _utc(fetched_at)
    if not isinstance(ingestion_run, str) or not ingestion_run or not isinstance(evidence_ref, str) or not evidence_ref:
        raise ValueError("explicit provenance required")
    records = []
    for row in result["list"]:
        if not isinstance(row, dict):
            raise ValueError("unexpected source row")
        if row.get("symbol") != symbol:
            raise ValueError("source symbol mismatch")
        if kind == "funding":
            timestamp = _ms(row.get("fundingRateTimestamp"), "fundingRateTimestamp")
            payload = {"value": _exact(row.get("fundingRate"), "fundingRate"),
                       "unit": "fraction_per_funding_event",
                       "event_timestamp_ms": timestamp,
                       "publication_timestamp_ms": None,
                       "observed_at_utc": observed,
                       "source_fields": row}
        elif kind == "open_interest":
            timestamp = _ms(row.get("timestamp"), "timestamp")
            payload = {"value": _exact(row.get("openInterest"), "openInterest"),
                       "unit": "contracts_source_native",
                       "observation_timestamp_ms": timestamp,
                       "publication_timestamp_ms": None,
                       "observed_at_utc": observed,
                       "source_fields": row}
        else:
            timestamp = _ms(row.get("time"), "time")
            side = row.get("side")
            if side not in ("Buy", "Sell"):
                raise ValueError("unknown taker side")
            payload = {"price": _exact(row.get("price"), "price", positive=True),
                       "size": _exact(row.get("size"), "size", positive=True),
                       "side": side, "trade_id": row.get("execId"),
                       "unit": "source_native_quantity",
                       "event_timestamp_ms": timestamp,
                       "publication_timestamp_ms": None,
                       "observed_at_utc": observed,
                       "anonymous_public_execution": True,
                       "source_fields": row}
        raw = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        source = {"url": url, "fetched_at": observed,
                  "sha256": hashlib.sha256(raw).hexdigest(),
                  "hash_scope": "canonical_row_json",
                  "source_kind": "rest",
                  "source_timestamp": str(timestamp),
                  "ingestion_run": ingestion_run,
                  "evidence_refs": [evidence_ref]}
        if kind == "trades" and not payload["trade_id"]:
            source["record_key"] = hashlib.sha256(raw).hexdigest()
        records.append(normalize({"schema_version": "1.0", "venue": "bybit",
                                  "category": category, "symbol": symbol,
                                  "dataset": kind, "interval": request_params.get("intervalTime", "") if kind == "open_interest" else "",
                                  "timestamp_ms": timestamp,
                                  "timestamp_precision": "ms", "timestamp_ns": None,
                                  "payload": payload, "source": source}))
    return records


def ingest_page(store, *, kind, category, symbol, response, fetched_at,
                url, run_id, job_key, evidence_ref, start_ms, end_ms,
                request_params, cursor_in=None):
    """Atomic PostgreSQL publication using existing immutable storage ledger.

    A page is only a bounded observation; its receipt never proves full history.
    """
    records = decode(kind, category, symbol, response, fetched_at=fetched_at,
                     url=url, ingestion_run=run_id, evidence_ref=evidence_ref)
    if type(start_ms) is not int or type(end_ms) is not int or start_ms >= end_ms:
        raise ValueError("invalid evidence bounds")
    if any(not start_ms <= r["timestamp_ms"] < end_ms for r in records):
        raise ValueError("row outside evidence window")
    cursor_out = response["result"].get("nextPageCursor") or None
    if cursor_out is not None and (not isinstance(cursor_out, str) or cursor_out == cursor_in):
        raise ValueError("invalid pagination cursor")
    evidence = {
        "venue": "bybit", "category": category, "symbol": symbol,
        "dataset": kind, "interval": request_params.get("intervalTime", "") if kind == "open_interest" else "", "start_ms": start_ms, "end_ms": end_ms,
        "status": "PARTIAL" if cursor_out else "UNVERIFIED",
        "pages": [{"params": request_params, "cursor": cursor_in,
                   "next_cursor": cursor_out, "count": len(records)}],
        "ingestion_run": run_id,
        "coverage_status": "UNKNOWN",
        "source_url": url,
        "observed_at_utc": _utc(fetched_at),
        "evidence_ref": evidence_ref,
    }
    return store.ingest(records, evidence, job_key, run_id=run_id)
