"""Strict Bybit V5 public anonymous execution (recent-trade) adapter.

No private endpoints, no trader identities, no completeness inference.
Feed the resulting envelopes and evidence to Store.ingest in one transaction.

Funding and open interest are NOT acquired here: their single canonical writer
is src.ingest.rest.Backfill -> src.ingest.journal/handoff -> Store.ingest. A
second writer with a different payload for the same record_key would raise
DataConflict and mark ranges corrupt (issue #105).
"""
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit

from src.storage.models import normalize

PATHS = {"trades": "/recent-trade"}
LIMITS = {"trades": 1000}


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
    return value.astimezone(UTC).isoformat()


def request(kind, category, symbol, *, limit=None):
    if kind not in PATHS or category not in ("linear", "inverse"):
        raise ValueError("unsupported public dataset/category")
    if not isinstance(symbol, str) or not symbol.isascii() or not symbol.isalnum() or not 1 <= len(symbol) <= 40:
        raise ValueError("invalid symbol")
    cap = LIMITS[kind]
    limit = cap if limit is None else limit
    if type(limit) is not int or not 1 <= limit <= cap:
        raise ValueError("invalid limit")
    return PATHS[kind], {"category": category, "symbol": symbol, "limit": limit}


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
        timestamp = _ms(row.get("time"), "time")
        side = row.get("side")
        if side not in ("Buy", "Sell", "Unknown", None):
            raise ValueError("invalid source taker-side field")
        side = side or "Unknown"
        if not isinstance(row.get("execId"), str) or not row["execId"]:
            raise ValueError("public execution requires exchange execId")
        payload = {"price": _exact(row.get("price"), "price", positive=True),
                   "size": _exact(row.get("size"), "size", positive=True),
                   "side": side, "trade_id": row.get("execId"),
                   "unit": "source_native_linear_size" if category == "linear" else "source_native_inverse_contract",
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
        records.append(normalize({"schema_version": "1.0", "venue": "bybit",
                                  "category": category, "symbol": symbol,
                                  "dataset": kind, "interval": "",
                                  "timestamp_ms": timestamp,
                                  "timestamp_precision": "ms", "timestamp_ns": None,
                                  "payload": payload, "source": source}))
    return records


def fetch_and_ingest(store, api, *, kind, category, symbol, run_id, job_key,
                     limit=None, observed_at=None, return_receipt=False):
    """One bounded official public REST page, never an implied full backfill.

    api is src.ingest.rest.API over the canonical Transport (or a
    contract-compatible replay adapter). Original HTTP body proof is required.
    """
    import base64

    from src.storage.models import digest
    path, params = request(kind, category, symbol, limit=limit)
    result, url = api.call(path, params)
    parsed_url = urlsplit(url)
    actual_params = parse_qs(parsed_url.query, keep_blank_values=True)
    if (parsed_url.scheme != "https" or parsed_url.netloc != "api.bybit.com"
            or parsed_url.path != "/v5/market" + path or parsed_url.fragment
            or actual_params != {key: [str(value)] for key, value in params.items()}):
        raise ValueError("public source endpoint/request binding mismatch")
    if not isinstance(result, dict):
        raise ValueError("missing result")
    response = {"retCode": 0, "result": result}
    observed_at = observed_at or datetime.now(UTC)
    http = getattr(api, "last_response", None)
    if not isinstance(http, dict) or http.get("hash_scope") != "original_http_body":
        raise ValueError("original HTTP response proof required")
    if not isinstance(http.get("sha256"), str) or len(http["sha256"]) != 64:
        raise ValueError("missing original HTTP SHA256")
    if http.get("url") != url or http.get("status_code") != 200:
        raise ValueError("HTTP receipt request/status mismatch")
    if not isinstance(http.get("bytes_base64"), str) or len(http["bytes_base64"]) > 4 * 1024 * 1024:
        raise ValueError("original HTTP body missing or exceeds bounded evidence budget")
    body = base64.b64decode(http["bytes_base64"], validate=True)
    if len(body) > 3 * 1024 * 1024:
        raise ValueError("decoded HTTP evidence exceeds budget")
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
                  url=url, ingestion_run=run_id, evidence_ref=evidence_ref)
    if not rows:
        raise ValueError("empty recent-trade response cannot establish time bounds")
    # recent-trade has no request range: the page's own rows bound the evidence.
    start_ms = min(row["timestamp_ms"] for row in rows)
    end_ms = max(row["timestamp_ms"] for row in rows) + 1
    if result.get("nextPageCursor"):
        raise ValueError("recent-trade has no pagination cursor contract")
    evidence = {
        "venue": "bybit", "category": category, "symbol": symbol,
        "dataset": kind, "interval": "",
        "start_ms": start_ms, "end_ms": end_ms,
        "status": "UNVERIFIED",
        "coverage_status": "UNKNOWN", "ingestion_run": run_id,
        "source_url": url, "observed_at_utc": _utc(observed_at),
        "http_sha256": http["sha256"],
        "page_sha256": hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest(),
        "evidence_ref": evidence_ref,
        "http_hash_scope": "original_http_body",
        "http_bytes_base64": http["bytes_base64"],
        "cursor_in": None,
        "pages": [{"params": params, "cursor": None, "next_cursor": None,
                   "count": len(rows), "http_sha256": http["sha256"]}],
    }
    published = store.ingest(rows, evidence, job_key, run_id=run_id)
    if return_receipt:
        return {"published": published, "next_cursor": None, "records": len(rows),
                "http_sha256": http["sha256"], "coverage": "UNKNOWN",
                "oldest_ms": start_ms}
    return published
