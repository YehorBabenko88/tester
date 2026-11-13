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
        params.update(startTime=start_ms, endTime=end_ms - 1)
    return PATHS[kind], params


def decode(kind, category, symbol, response, *, fetched_at, url, ingestion_run, evidence_ref, interval=None):
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
    if kind == "open_interest" and interval not in OI_INTERVALS:
        raise ValueError("OI interval required")
    if kind != "open_interest" and interval is not None:
        raise ValueError("unexpected metric interval")
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
                       "source_fields": row}
        elif kind == "open_interest":
            timestamp = _ms(row.get("timestamp"), "timestamp")
            payload = {"value": _exact(row.get("openInterest"), "openInterest"),
                       "unit": "contracts_source_native",
                       "observation_timestamp_ms": timestamp,
                       "publication_timestamp_ms": None,
                       "source_fields": row}
        else:
            timestamp = _ms(row.get("time"), "time")
            side = row.get("side")
            if side not in ("Buy", "Sell"):
                raise ValueError("unknown taker side")
            if not isinstance(row.get("execId"), str) or not row["execId"]:
                raise ValueError("public execution requires exchange execId")
            payload = {"price": _exact(row.get("price"), "price", positive=True),
                       "size": _exact(row.get("size"), "size", positive=True),
                       "side": side, "trade_id": row.get("execId"),
                       "unit": "source_native_quantity",
                       "event_timestamp_ms": timestamp,
                       "publication_timestamp_ms": None,
                       "anonymous_public_execution": True,
                       "source_fields": row}
        raw = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        source = {"url": url, "fetched_at": observed,
                  "sha256": hashlib.sha256(raw).hexdigest(),
                  "hash_scope": "canonical_row_json",
                  "source_kind": "rest",
                  "source_timestamp": str(timestamp),
                  "observed_at_utc": observed,
                  "ingestion_run": ingestion_run,
                  "evidence_refs": [evidence_ref]}
        if kind == "trades" and not payload["trade_id"]:
            source["record_key"] = hashlib.sha256(raw).hexdigest()
        records.append(normalize({"schema_version": "1.0", "venue": "bybit",
                                  "category": category, "symbol": symbol,
                                  "dataset": kind, "interval": interval if kind == "open_interest" else "",
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
                     url=url, ingestion_run=run_id, evidence_ref=evidence_ref, interval=request_params.get("intervalTime") if kind == "open_interest" else None)
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


def fetch_and_ingest(store, api, *, kind, category, symbol, run_id, job_key,
                     start_ms=None, end_ms=None, interval=None, limit=None,
                     observed_at=None, cursor_in=None, return_receipt=False):
    """One bounded official public REST page, never an implied full backfill.

    api is src.ingest.rest.API (or a contract-compatible replay adapter).
    Original HTTP body proof is included when the transport supplies it.
    """
    from src.storage.models import digest
    path, params = request(kind, category, symbol, start_ms=start_ms,
                           end_ms=end_ms, interval=interval, limit=limit)
    if cursor_in is not None:
        if kind != "open_interest" or not isinstance(cursor_in, str) or not cursor_in or len(cursor_in) > 4096:
            raise ValueError("cursor supported only for bounded OI pages")
        params["cursor"] = cursor_in
    result, url = api.call(path, params)
    response = {"retCode": 0, "result": result}
    observed_at = observed_at or datetime.now(timezone.utc)
    http = getattr(api, "last_response", None)
    if not isinstance(http, dict) or http.get("hash_scope") != "original_http_body":
        raise ValueError("original HTTP response proof required")
    if not isinstance(http.get("sha256"), str) or len(http["sha256"]) != 64:
        raise ValueError("missing original HTTP SHA256")
    if http.get("url") != url or http.get("status_code") != 200:
        raise ValueError("HTTP receipt request/status mismatch")
    if not isinstance(http.get("bytes_base64"), str):
        raise ValueError("original HTTP body missing")
    import base64
    body = base64.b64decode(http["bytes_base64"], validate=True)
    if hashlib.sha256(body).hexdigest() != http["sha256"]:
        raise ValueError("HTTP evidence hash mismatch")
    try:
        original_response = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid original HTTP JSON") from exc
    if not isinstance(original_response, dict) or original_response.get("retCode") != 0 or original_response.get("result") != result:
        raise ValueError("HTTP body/result mismatch")
    evidence_ref = digest([run_id, job_key, url, http["sha256"]])
    if len(result.get("list", [])) > params["limit"]:
        raise ValueError("source response exceeds requested limit")
    rows = decode(kind, category, symbol, response, fetched_at=observed_at,
                  url=url, ingestion_run=run_id, evidence_ref=evidence_ref,
                  interval=interval)
    if start_ms is None or end_ms is None:
        if kind != "trades":
            raise ValueError("funding/OI require explicit bounded time window")
        if not rows:
            raise ValueError("empty recent-trade response cannot establish time bounds")
        start_ms = min(row["timestamp_ms"] for row in rows)
        end_ms = max(row["timestamp_ms"] for row in rows) + 1
    if any(not start_ms <= row["timestamp_ms"] < end_ms for row in rows):
        raise ValueError("source returned data outside requested time window")
    cursor = result.get("nextPageCursor")
    if cursor is not None and (kind != "open_interest" or not isinstance(cursor, str) or not cursor or cursor == cursor_in):
        raise ValueError("invalid pagination cursor")
    evidence = {
        "venue": "bybit", "category": category, "symbol": symbol,
        "dataset": kind, "interval": interval if kind == "open_interest" else "",
        "start_ms": start_ms, "end_ms": end_ms,
        "status": "PARTIAL" if cursor else "UNVERIFIED",
        "coverage_status": "UNKNOWN", "ingestion_run": run_id,
        "source_url": url, "observed_at_utc": _utc(observed_at),
        "http_sha256": http["sha256"], "http_hash_scope": "original_http_body",
        "http_bytes_base64": http["bytes_base64"],
        "cursor_in": cursor_in,
        "pages": [{"params": params, "cursor": None, "next_cursor": cursor,
                   "count": len(rows), "http_sha256": http["sha256"]}],
    }
    published = store.ingest(rows, evidence, job_key, run_id=run_id)
    if return_receipt:
        return {"published": published, "next_cursor": cursor, "records": len(rows),
                "http_sha256": http["sha256"], "coverage": "UNKNOWN",
                "oldest_ms": min((r["timestamp_ms"] for r in rows), default=None)}
    return published


def fetch_open_interest_pages(store, api, *, category, symbol, run_id,
                              job_prefix, start_ms, end_ms, interval="5min",
                              max_pages=10, limit=200, stop=None):
    """Bounded cursor traversal. Each page commits its own immutable receipt.

    A missing cursor is *not* evidence of complete historical availability.
    Caller supplies an isolated PostgreSQL Store and explicit scope.
    """
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("invalid page budget")
    if not isinstance(job_prefix, str) or not job_prefix:
        raise ValueError("job prefix required")
    cursor = None
    seen = set()
    receipts = []
    for index in range(max_pages):
        if stop is not None and stop():
            return {"status": "PAUSED", "pages": receipts, "coverage": "UNKNOWN"}
        page = fetch_and_ingest(
            store, api, kind="open_interest", category=category, symbol=symbol,
            run_id=run_id, job_key=f"{job_prefix}:page:{index}",
            start_ms=start_ms, end_ms=end_ms, interval=interval, limit=limit,
            cursor_in=cursor, return_receipt=True,
        )
        receipts.append(page)
        cursor = page["next_cursor"]
        if cursor is None:
            return {"status": "OBSERVED", "pages": receipts, "coverage": "UNKNOWN"}
        if cursor in seen:
            raise ValueError("repeated pagination cursor")
        seen.add(cursor)
    return {"status": "PAGE_BUDGET_EXHAUSTED", "pages": receipts, "coverage": "UNKNOWN"}


def fetch_funding_pages(store, api, *, category, symbol, run_id, job_prefix,
                        start_ms, end_ms, max_pages=10, limit=200, stop=None):
    """Reverse-time bounded funding traversal, never a completeness certificate.

    The exchange funding-history endpoint has no cursor contract. For a full
    page, retry the strictly older inclusive endTime (oldest timestamp - 1).
    Empty/short pages are observations, not proof of available history.
    """
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("invalid page budget")
    if type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms < end_ms:
        raise ValueError("invalid funding window")
    if not isinstance(job_prefix, str) or not job_prefix:
        raise ValueError("job prefix required")
    if type(limit) is not int or not 1 <= limit <= LIMITS["funding"]:
        raise ValueError("invalid funding limit")
    upper = end_ms
    receipts = []
    for index in range(max_pages):
        if stop is not None and stop():
            return {"status": "PAUSED", "pages": receipts, "coverage": "UNKNOWN"}
        page = fetch_and_ingest(
            store, api, kind="funding", category=category, symbol=symbol,
            run_id=run_id, job_key=f"{job_prefix}:page:{index}",
            start_ms=start_ms, end_ms=upper, limit=limit, return_receipt=True,
        )
        receipts.append(page)
        oldest = page["oldest_ms"]
        if oldest is None or page["records"] < limit or oldest <= start_ms:
            return {"status": "OBSERVED", "pages": receipts, "coverage": "UNKNOWN"}
        if oldest >= upper:
            raise ValueError("funding pagination did not advance")
        upper = oldest
    return {"status": "PAGE_BUDGET_EXHAUSTED", "pages": receipts, "coverage": "UNKNOWN"}
