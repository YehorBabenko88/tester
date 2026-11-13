"""DATA -> RESEARCH execution-event contract v1; fail-closed read-only bridge.

Accept only immutable Bybit public REST rows with the *original* HTTP response
stored in tester_storage evidence. Never certify historical completeness.
"""
import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit

VERSION = "tester-public-execution/1"
MAX_ROWS = 100
MAX_WINDOW_MS = 7 * 86_400_000
MAX_HTTP_BYTES = 3 * 1024 * 1024
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
HEX = re.compile(r"[0-9a-f]{64}\Z")
SYMBOL = re.compile(r"[A-Z0-9]{2,40}\Z")


class ExecutionContractError(ValueError):
    """Incomplete or contradictory source proof; refuse projection."""


def _fail(reason):
    raise ExecutionContractError(reason)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _decimal(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value
    ):
        _fail("positive decimal source string required")
    try:
        number = Decimal(value)
    except InvalidOperation:
        _fail("invalid source decimal")
    if not number.is_finite() or number <= 0:
        _fail("nonpositive source decimal")
    return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")


def _utc_ms(value):
    if not isinstance(value, str):
        _fail("source observation UTC required")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("invalid source observation time")
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        _fail("naive source observation time")
    return (stamp.astimezone(timezone.utc) - EPOCH) // timedelta(milliseconds=1)


def _bounded_scope(category, symbol, start_ms, end_ms, limit):
    if category not in {"linear", "inverse"} or not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        _fail("invalid market scope")
    if any(type(x) is not int for x in (start_ms, end_ms, limit)) or not (
        0 <= start_ms < end_ms and end_ms - start_ms <= MAX_WINDOW_MS and 1 <= limit <= MAX_ROWS
    ):
        _fail("invalid explicit bounded execution read")


def project_storage_execution(row, *, category, symbol, start_ms, end_ms):
    """Verified tester_storage Store.read row -> versioned DATA event.

    No network access and no SQL writes; evidence MUST be persisted in PG.
    """
    if not isinstance(row, dict) or any(row.get(k) != v for k, v in (
        ("venue", "bybit"), ("category", category), ("symbol", symbol),
        ("dataset", "trades"), ("interval", ""),
        ("timestamp_precision", "ms"), ("id_origin", "exchange"),
    )):
        _fail("record scope/precision/identity mismatch")
    stamp = row.get("timestamp_ms")
    if type(stamp) is not int or not start_ms <= stamp < end_ms:
        _fail("execution event outside requested UTC bounds")
    p = row.get("payload")
    if not isinstance(p, dict) or p.get("event_timestamp_ms") != stamp or p.get("publication_timestamp_ms", "MISSING") is not None:
        _fail("missing event timestamp or invented publication time")
    identity = row.get("trade_id")
    if not isinstance(identity, str) or not identity or p.get("trade_id") != identity:
        _fail("missing original exchange execId")
    expected_key = _sha(_canonical(["bybit", category, symbol, "trades", identity]))
    if row.get("record_key") != expected_key:
        _fail("immutable exchange record identity mismatch")
    content = {k: row.get(k) for k in (
        "schema_version", "timestamp_ms", "timestamp_ns", "timestamp_precision",
        "payload", "trade_id", "id_origin",
    )}
    content["timestamp_us"] = row.get("timestamp_us")
    if row.get("content_hash") != _sha(_canonical(content)):
        _fail("immutable market content hash mismatch")
    fields = p.get("source_fields")
    if not isinstance(fields, dict) or fields.get("execId") != identity or fields.get("time") != str(stamp):
        _fail("original execution fields inconsistent")
    if fields.get("symbol", symbol) != symbol or _decimal(fields.get("price")) != _decimal(p.get("price")) or _decimal(fields.get("size")) != _decimal(p.get("size")):
        _fail("original market/price/quantity mismatch")
    if fields.get("side") not in {"Buy", "Sell", "Unknown", None}:
        _fail("unrecognized original side")
    side = fields.get("side") or "Unknown"
    if p.get("side") != side or not p.get("anonymous_public_execution"):
        _fail("fabricated taker side or identity")
    unit = "source_native_linear_size" if category == "linear" else "source_native_inverse_contract"
    if p.get("unit") != unit:
        _fail("missing or inconsistent source quantity unit")
    price, quantity = _decimal(p.get("price")), _decimal(p.get("size"))
    provenance = row.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("authentic") is not True:
        _fail("durable authentic provenance required")
    observations = provenance.get("observations")
    if not isinstance(observations, list) or not observations:
        _fail("missing durable source observations")
    canonical_sources = []
    receipts = []
    times = []
    for observation in observations:
        if not isinstance(observation, dict) or observation.get("source_kind") != "rest" or observation.get("timestamp_precision") != "ms" or observation.get("authentic_run") is not True or observation.get("source_run_status") != "matched":
            _fail("unauthenticated REST source observation")
        source = observation.get("source")
        evidence = observation.get("evidence")
        if not isinstance(source, dict) or not isinstance(evidence, dict):
            _fail("missing original source/HTTP proof")
        run = observation.get("run_id")
        job = observation.get("job_key")
        evidence_id = observation.get("evidence_id")
        if any(not isinstance(v, str) or not v for v in (run, job, evidence_id)) or source.get("ingestion_run") != run or evidence.get("ingestion_run") != run:
            _fail("source run/evidence identity mismatch")
        if any(evidence.get(k) != v for k, v in (
            ("venue", "bybit"), ("category", category), ("symbol", symbol),
            ("dataset", "trades"), ("interval", ""),
        )) or evidence.get("coverage_status") != "UNKNOWN" or evidence.get("status") != "UNVERIFIED":
            _fail("unqualified source scope or false coverage")
        lo, hi = evidence.get("start_ms"), evidence.get("end_ms")
        if type(lo) is not int or type(hi) is not int or not lo <= stamp < hi or not start_ms <= stamp < end_ms:
            _fail("missing original source request bounds")
        http_hash, page_hash, row_hash = (evidence.get("http_sha256"), evidence.get("page_sha256"), source.get("sha256"))
        if any(not isinstance(x, str) or not HEX.fullmatch(x) for x in (http_hash, page_hash, row_hash)):
            _fail("missing SHA256 chain")
        if source.get("hash_scope") != "canonical_row_json" or evidence.get("http_hash_scope") != "original_http_body" or row_hash != _sha(_canonical(fields)):
            _fail("original row SHA mismatch")
        encoded = evidence.get("http_bytes_base64")
        if not isinstance(encoded, str) or len(encoded) > 4 * 1024 * 1024:
            _fail("missing or oversized original HTTP proof")
        try:
            body = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            _fail("invalid original HTTP evidence")
        if len(body) > MAX_HTTP_BYTES or _sha(body) != http_hash:
            _fail("original HTTP body SHA mismatch")
        try:
            doc = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            _fail("invalid original HTTP JSON")
        if not isinstance(doc, dict) or doc.get("retCode") != 0 or not isinstance(doc.get("result"), dict) or not isinstance(doc["result"].get("list"), list):
            _fail("invalid source response envelope")
        page = doc["result"]
        if _sha(_canonical(page)) != page_hash or fields not in page["list"]:
            _fail("HTTP page/row chain mismatch")
        pages = evidence.get("pages")
        if not isinstance(pages, list) or len(pages) != 1 or pages[0].get("count") != len(page["list"]):
            _fail("missing exact page receipt")
        url = source.get("url")
        if not isinstance(url, str):
            _fail("missing source URL")
        parsed = urlsplit(url)
        params = pages[0].get("params")
        if parsed.scheme != "https" or parsed.netloc != "api.bybit.com" or parsed.path != "/v5/market/recent-trade" or parsed.fragment or not isinstance(params, dict) or parse_qs(parsed.query) != {k: [str(v)] for k, v in params.items()} or params.get("category") != category or params.get("symbol") != symbol:
            _fail("request URL/scope mismatch")
        if evidence.get("source_url") != url or source.get("evidence_refs") != [evidence.get("evidence_ref")]:
            _fail("source page reference mismatch")
        expected_ref = _sha(_canonical([run, job, url, http_hash]))
        if evidence.get("evidence_ref") != expected_ref:
            _fail("unbound page evidence reference")
        original_ts = source.get("source_timestamp")
        if original_ts != str(stamp):
            _fail("original millisecond timestamp mismatch")
        observed_at = source.get("observed_at_utc")
        if observed_at != source.get("fetched_at") or observed_at != evidence.get("observed_at_utc"):
            _fail("unbound observed UTC time")
        observed_ms = _utc_ms(observed_at)
        canonical_sources.append(dict(source_kind="bybit_rest", source_ref=url, source_hash=row_hash,
                                      ingestion_run=run, timestamp_precision="ms", source_timestamp=original_ts,
                                      parent_refs=[evidence_id, page_hash, http_hash], method_version=VERSION))
        receipts.append(dict(evidence_id=evidence_id, page_ref=expected_ref, http_sha256=http_hash,
                             page_sha256=page_hash, row_sha256=row_hash))
        times.append(observed_ms)
    return dict(contract_version=VERSION, venue="bybit", category=category, symbol=symbol,
                exec_id=identity, taker_side=side, price=price, quantity=quantity,
                quantity_unit=unit, event_ms=stamp, observed_ms=min(times), publication_ms=None,
                utc_interval={"start_ms": start_ms, "end_ms": end_ms},
                source_coverage={"status": "UNKNOWN", "start_ms": start_ms, "end_ms": end_ms},
                availability={"status": "UNVERIFIED", "reason": "Public executions have unknown historical visibility"},
                provenance=canonical_sources, source_receipts=receipts)


def to_research_trade(event):
    """Build RESEARCH canonical trade WITHOUT authorizing F or cross-unit metrics."""
    if not isinstance(event, dict) or event.get("contract_version") != VERSION or event.get("category") != "linear" or event.get("quantity_unit") != "source_native_linear_size":
        _fail("inverse/cross-unit or unversioned execution not comparable")
    scope = event.get("utc_interval")
    if not isinstance(scope, dict) or event.get("source_coverage") != {"status": "UNKNOWN", **scope} or event.get("publication_ms", "MISSING") is not None:
        _fail("missing UTC scope or unknown publication visibility")
    if not event.get("source_receipts") or not event.get("provenance"):
        _fail("unproven execution evidence")
    return dict(schema_version="1.0", venue=event["venue"], category=event["category"],
                symbol=event["symbol"], timestamp_ms=event["event_ms"], timestamp_ns=None,
                timestamp_precision="ms", trade_id=event["exec_id"], id_origin="exchange",
                price=event["price"], quantity=event["quantity"], taker_side=event["taker_side"],
                provenance=event["provenance"], availability=event["availability"],
                coverage={"status": "UNKNOWN", "start_ms": scope["start_ms"],
                          "end_ms": scope["end_ms"],
                          "evidence_refs": sorted({r["evidence_id"] for r in event["source_receipts"]})})


def read_execution_events(store, *, category, symbol, start_ms, end_ms, limit=MAX_ROWS):
    """Bounded SELECT-only PostgreSQL adapter; caller supplies Store(dsn)."""
    _bounded_scope(category, symbol, start_ms, end_ms, limit)
    rows = store.read("trades", "bybit", category, symbol, start_ms, end_ms, "",
                      limit=limit, compact_provenance=False)
    if not isinstance(rows, list) or len(rows) > limit:
        _fail("source read violates row budget")
    seen = {}
    events = []
    for row in rows:
        event = project_storage_execution(row, category=category, symbol=symbol,
                                          start_ms=start_ms, end_ms=end_ms)
        key = event["exec_id"]
        if key in seen:
            if seen[key] != (event["price"], event["quantity"], event["taker_side"], event["event_ms"]):
                _fail("duplicate execId with conflicting economics")
            _fail("duplicate exchange identity in bounded read")
        seen[key] = (event["price"], event["quantity"], event["taker_side"], event["event_ms"])
        events.append(event)
    return events


def read_research_trades(store, *, category, symbol, start_ms, end_ms, limit=MAX_ROWS):
    """Strict read-only RESEARCH projection; always UNKNOWN and never F-admitted."""
    if category != "linear":
        _fail("research metrics require comparable linear source-native size")
    from src.analytics.core import validate_trade

    return [validate_trade(to_research_trade(event)) for event in read_execution_events(
        store, category=category, symbol=symbol, start_ms=start_ms,
        end_ms=end_ms, limit=limit,
    )]