"""Fail-closed DATA #72 public execution -> canonical RESEARCH projection.

Uses one B Store.read_page repeatable-read result. A row SHA is necessary but
insufficient: require matching persisted raw envelope, original HTTP body,
run/observation/evidence linkage and exact public endpoint. Recent trades are
an OBSERVED SAMPLE, never certified historical executions for F.
"""
import base64
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from src.analytics.core import deduplicate, validate_trade
from src.analytics.research_metrics import calculate
from src.storage.models import canonical, digest, normalize

DATA_CONTRACT_HEAD = "6322e84d1e89c9d436ebf763064cf27bf47158de"
MAX_PAGE_ROWS = 1000
MAX_HTTP_BYTES = 3 * 1024 * 1024


def _original_http(evidence):
    body = evidence.get("evidence")
    if not isinstance(body, dict) or body.get("coverage_status") != "UNKNOWN":
        raise ValueError("unqualified_data_evidence")
    pages = body.get("pages")
    if not isinstance(pages, list) or len(pages) != 1:
        raise ValueError("missing_or_multiple_source_pages")
    p = pages[0]
    if p.get("cursor") is not None or p.get("next_cursor") is not None:
        raise ValueError("unproven_pagination_chain")
    url = body.get("source_url")
    parsed = urlsplit(url) if isinstance(url, str) else None
    if not parsed or (parsed.scheme, parsed.netloc, parsed.path) != (
        "https", "api.bybit.com", "/v5/market/recent-trade"
    ) or parsed.fragment:
        raise ValueError("unexpected_public_source_url")
    params = p.get("params")
    if not isinstance(params, dict) or parse_qs(parsed.query, keep_blank_values=True) != {
        k: [str(v)] for k, v in params.items()
    } or set(params) != {"category", "symbol", "limit"}:
        raise ValueError("http_request_parameters_mismatch")
    if not isinstance(params["limit"], int) or not 1 <= params["limit"] <= MAX_PAGE_ROWS:
        raise ValueError("invalid_source_page_limit")
    if body.get("http_hash_scope") != "original_http_body":
        raise ValueError("missing_http_body_proof")
    encoded = body.get("http_bytes_base64")
    if not isinstance(encoded, str) or len(encoded) > 4 * MAX_HTTP_BYTES // 3 + 4:
        raise ValueError("missing_or_oversized_http_body")
    try:
        raw = base64.b64decode(encoded, validate=True)
        doc = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid_http_body") from exc
    if len(raw) > MAX_HTTP_BYTES or hashlib.sha256(raw).hexdigest() != body.get("http_sha256"):
        raise ValueError("http_body_sha256_mismatch")
    if not isinstance(doc, dict) or doc.get("retCode") != 0:
        raise ValueError("unsuccessful_original_response")
    result = doc.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("list"), list):
        raise ValueError("missing_original_executions")
    rows = result["list"]
    if len(rows) != p.get("count") or len(rows) != evidence.get("record_count") or len(rows) > params["limit"]:
        raise ValueError("source_page_count_mismatch")
    if p.get("http_sha256") != body.get("http_sha256") or body.get("cursor_in") is not None:
        raise ValueError("source_page_receipt_mismatch")
    if body.get("dataset") != "trades" or body.get("interval") != "":
        raise ValueError("wrong_source_dataset")
    return body, rows


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("source_observation_time_missing")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("source_observation_time_invalid") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("source_observation_time_naive")
    delta = dt.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def project_data_page(page, *, category, symbol, start_ms, end_ms, as_of_ms=None):
    """Verify DATA producer's single bounded B snapshot, then project trades.

    No source timestamp is converted to publication/known-at time. Inverse
    source-native contracts are deliberately rejected until a separate unit
    contract and conversion evidence exist.
    """
    if category != "linear":
        raise ValueError("inverse_or_unsupported_quantity_unit")
    if not isinstance(symbol, str) or not symbol or type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms < end_ms:
        raise ValueError("invalid_requested_scope")
    if as_of_ms is not None and (type(as_of_ms) is not int or as_of_ms < 0):
        raise ValueError("invalid_as_of_ms")
    scope = dict(venue="bybit", category=category, symbol=symbol, dataset="trades", interval="")
    if not isinstance(page, dict) or page.get("outcome") != "OK" or page.get("scope") != scope:
        raise ValueError("data_page_unavailable_or_wrong_scope")
    if page.get("page_exhausted") is not True or page.get("next_cursor") is not None:
        raise ValueError("missing_source_records_or_more_pages")
    coverage = page.get("coverage")
    if (not isinstance(coverage, dict) or coverage.get("start_ms") != start_ms
            or coverage.get("end_ms") != end_ms):
        raise ValueError("missing_requested_coverage_bounds")
    if coverage.get("complete") is not False or coverage.get("state") != "UNKNOWN":
        raise ValueError("unexpected_recent_trade_history_certification")
    rows = page.get("rows")
    observations = page.get("observations")
    evidences = page.get("evidence")
    if not isinstance(rows, list) or not isinstance(observations, list) or not isinstance(evidences, list) or not 1 <= len(rows) <= MAX_PAGE_ROWS:
        raise ValueError("missing_or_oversized_data_page")
    if len(observations) > 4096 or len(evidences) > 256:
        raise ValueError("source_linkage_budget_exceeded")
    by_evidence = {}
    for e in evidences:
        if not isinstance(e, dict) or not isinstance(e.get("evidence_id"), str) or e["evidence_id"] in by_evidence:
            raise ValueError("duplicate_or_invalid_source_evidence")
        body, original_rows = _original_http(e)
        if (e.get("start_ms"), e.get("end_ms")) != (body.get("start_ms"), body.get("end_ms")):
            raise ValueError("evidence_range_mismatch")
        if (body.get("venue"), body.get("category"), body.get("symbol")) != (
            "bybit", category, symbol
        ):
            raise ValueError("evidence_market_mismatch")
        by_evidence[e["evidence_id"]] = (e, body, original_rows)
    records = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("dataset") != "trades" or any(row.get(k) != v for k, v in scope.items()):
            raise ValueError("data_record_scope_mismatch")
        ts = row.get("timestamp_ms")
        if type(ts) is not int or not start_ms <= ts < end_ms:
            raise ValueError("data_record_time_mismatch")
        key = row.get("record_key")
        if not isinstance(key, str) or key in records:
            raise ValueError("duplicate_source_record_key")
        records[key] = row
    canonical_trades = {}
    seen_source_members = {}
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError("invalid_source_observation")
        key = observation.get("record_key")
        if key not in records:
            continue  # A B proof may reference support outside the requested range.
        if observation.get("authentic_run") is not True or observation.get("source_run_status") != "matched" or observation.get("run_origin") != "caller":
            raise ValueError("unproven_source_run")
        eid = observation.get("evidence_id")
        if eid not in by_evidence:
            raise ValueError("missing_source_evidence")
        e, body, original_rows = by_evidence[eid]
        source = observation.get("source")
        raw = observation.get("raw_envelope")
        if not isinstance(source, dict) or not isinstance(raw, dict):
            raise ValueError("missing_raw_source_envelope")
        normalized = normalize(raw)
        row = records[key]
        if (normalized["record_key"], normalized["content_hash"]) != (key, row.get("content_hash")):
            raise ValueError("source_record_content_mismatch")
        if any(normalized.get(k) != row.get(k) for k in (
            "venue", "category", "symbol", "dataset", "interval", "timestamp_ms",
            "trade_id", "id_origin", "timestamp_precision", "payload"
        )):
            raise ValueError("source_envelope_readback_mismatch")
        if normalized["source"] != source or observation.get("source_kind") != "rest":
            raise ValueError("wrong_provenance_source_kind")
        if source.get("hash_scope") != "canonical_row_json" or source.get("url") != body.get("source_url"):
            raise ValueError("source_hash_scope_or_url_mismatch")
        if (source.get("ingestion_run"), observation.get("run_id"), body.get("ingestion_run")) != (
            observation.get("run_id"), observation.get("run_id"), observation.get("run_id")
        ) or observation.get("job_key") != e.get("job_key"):
            raise ValueError("source_run_job_mismatch")
        expected_ref = digest([observation["run_id"], observation["job_key"], source["url"], body["http_sha256"]])
        if source.get("evidence_refs") != [expected_ref]:
            raise ValueError("source_page_linkage_mismatch")
        if (source.get("observed_at_utc") != body.get("observed_at_utc")
                or source.get("fetched_at") != body.get("observed_at_utc")):
            raise ValueError("observation_receipt_time_mismatch")
        observed_ms = _timestamp(source.get("observed_at_utc"))
        if observed_ms < row["timestamp_ms"]:
            raise ValueError("source_observed_before_execution")
        if as_of_ms is not None and observed_ms > as_of_ms:
            raise ValueError("late_source_knowledge")
        p = row.get("payload")
        if not isinstance(p, dict) or p.get("unit") != "source_native_quantity":
            raise ValueError("unknown_or_incompatible_quantity_unit")
        if "publication_timestamp_ms" not in p or p["publication_timestamp_ms"] is not None:
            raise ValueError("missing_or_fabricated_publication_time")
        if p.get("anonymous_public_execution") is not True:
            raise ValueError("nonanonymous_or_invalid_public_execution")
        if p.get("event_timestamp_ms") != row["timestamp_ms"] or row.get("timestamp_precision") != "ms" or row.get("timestamp_ns") is not None:
            raise ValueError("event_time_precision_mismatch")
        if row.get("id_origin") != "exchange" or row.get("trade_id") != p.get("trade_id"):
            raise ValueError("original_exchange_id_required")
        if p.get("side") not in ("Buy", "Sell"):
            raise ValueError("unknown_taker_side_not_source_proven")
        original = p.get("source_fields")
        if not isinstance(original, dict) or original.get("side") != p["side"] or original.get("execId") != p["trade_id"] or original.get("symbol") != symbol:
            raise ValueError("source_fields_mismatch")
        if (str(original.get("time")) != str(row["timestamp_ms"])
                or source.get("source_timestamp") != str(row["timestamp_ms"])
                or original.get("price") != p.get("price") or original.get("size") != p.get("size")):
            raise ValueError("source_execution_economics_mismatch")
        raw_row = canonical(original).encode("utf-8")
        if hashlib.sha256(raw_row).hexdigest() != source.get("sha256"):
            raise ValueError("source_row_sha256_mismatch")
        if original not in original_rows:
            raise ValueError("missing_original_http_row")
        if not e.get("evidence", {}).get("start_ms") <= row["timestamp_ms"] < e.get("evidence", {}).get("end_ms"):
            raise ValueError("source_evidence_does_not_cover_event")
        identity = (eid, p["trade_id"])
        if identity in seen_source_members and seen_source_members[identity] != source["sha256"]:
            raise ValueError("conflicting_duplicate_source_id")
        seen_source_members[identity] = source["sha256"]
        canonical_trade = {
            "schema_version": "1.0", "venue": "bybit", "category": category, "symbol": symbol,
            "timestamp_ms": row["timestamp_ms"], "timestamp_ns": None, "timestamp_precision": "ms",
            "trade_id": p["trade_id"], "id_origin": "exchange",
            "price": p["price"], "quantity": p["size"], "taker_side": p["side"],
            "provenance": [{
                "source_kind": "bybit_rest", "source_ref": source["url"],
                "source_hash": source["sha256"], "ingestion_run": source["ingestion_run"],
                "timestamp_precision": "ms", "source_timestamp": str(original["time"]),
                "parent_refs": [eid, expected_ref, body["http_sha256"]],
            }],
            "availability": {"status": "UNVERIFIED", "reason": "recent trades are an observed sample"},
            "coverage": {"status": "UNKNOWN", "start_ms": start_ms, "end_ms": end_ms,
                         "evidence_refs": [eid]},
        }
        validate_trade(canonical_trade)
        canonical_trades.setdefault(key, []).append(canonical_trade)
    if set(canonical_trades) != set(records):
        raise ValueError("missing_trade_observations")
    for eid, (_, _, original_rows) in by_evidence.items():
        expected = {
            str(r["execId"]) for r in original_rows
            if isinstance(r, dict) and str(r.get("time", "")).isdigit()
            and start_ms <= int(r["time"]) < end_ms
        }
        actual = {
            trade["trade_id"] for variants in canonical_trades.values() for trade in variants
            if eid in trade["coverage"]["evidence_refs"]
        }
        if expected != actual:
            raise ValueError("missing_page_members_or_duplicate_ids")
    trades = deduplicate([trade for variants in canonical_trades.values() for trade in variants])
    return {
        "source": "DATA_72_PUBLIC_RECENT_EXECUTIONS", "contract_head": DATA_CONTRACT_HEAD,
        "certification": "UNVERIFIED", "coverage": "UNKNOWN",
        "quantity_unit": "source_native_quantity", "publication_time": "UNKNOWN",
        "admitted_to_f": False,
        "observed_window": {
            "requested_start_ms": start_ms, "requested_end_ms": end_ms,
            "first_event_ms": min(t["timestamp_ms"] for t in trades),
            "last_event_ms": max(t["timestamp_ms"] for t in trades),
            "observed_unique_trades": len(trades),
            "source_pages_checked": len(by_evidence),
            "complete_market_history": False,
        },
        "trades": [{k: v for k, v in trade.items() if k != "_observations"} for trade in trades],
        "evidence_digest": digest({"scope": scope, "rows": rows, "observations": observations,
                                  "evidence": evidences}),
    }


def read_data_metrics(store, *, category, symbol, start_ms, end_ms, interval_ms,
                      tick_size, as_of_ms=None):
    """One read-only B transaction and deterministic observed-sample calculations."""
    page = store.read_page(
        "trades", "bybit", category, symbol, start_ms, end_ms, "",
        limit=MAX_PAGE_ROWS, max_bytes=8 * 1024 * 1024, max_archive_bytes=0
    )
    sample = project_data_page(
        page, category=category, symbol=symbol, start_ms=start_ms,
        end_ms=end_ms, as_of_ms=as_of_ms
    )
    metrics = calculate(sample["trades"], start_ms=start_ms, end_ms=end_ms,
                        interval_ms=interval_ms, tick_size=tick_size)
    return {
        "schema_version": "tester.data-research-preview/1",
        "source": sample["source"], "quantity_unit": sample["quantity_unit"],
        "status": "OBSERVED_UNVERIFIED", "coverage": "UNKNOWN",
        "data_evidence_digest": sample["evidence_digest"],
        "data_execution_digest": digest(sample["trades"]),
        "observed_window": sample["observed_window"],
        "metrics_digest": digest(metrics),
        "metrics": metrics, "f_input": None, "g_e_result": None,
        "admitted_to_f": False,
        "reasons": ["real_historical_coverage_not_proven", "historical_visibility_unknown",
                    "recent_execution_page_not_full_history"],
    }
