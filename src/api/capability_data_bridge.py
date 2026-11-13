"""Optional read-only DATA #72 execution observation probe.

No invented analytics and no F admission. Only a bounded DATA readback from a
configured, composed Store is eligible for an observed-sample evidence digest.
Absence of DATA, of a source store, or of a complete page witness fails closed.
"""

import hashlib
import importlib
import json
import re

from .capability_source_contracts import DATA_EXECUTION_VERSION

_MAX_EVENTS = 100
_MAX_WINDOW_MS = 7 * 86400000
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_REQUIRED_RECEIPTS = ("evidence_id", "page_ref", "http_sha256", "page_sha256", "row_sha256")


def _unverified(reason):
    return {
        "status": "UNVERIFIED",
        "reason": reason,
        "contract_version": DATA_EXECUTION_VERSION,
        "historical_coverage": "UNKNOWN",
        "publication_time": "UNKNOWN",
        "admitted_to_f": False,
    }


def observed_execution_evidence(reader, query, *, read_events=None):
    """Return only a source-proof summary; never an HTTP resource capability."""
    if query.category != "linear":
        return _unverified("linear_quantity_required")
    if query.symbol is None:
        return _unverified("instrument_required")
    if query.end_ms - query.start_ms > _MAX_WINDOW_MS:
        return _unverified("data72_bounded_window_required")
    store = getattr(reader, "store", None)
    if store is None:
        return _unverified("data72_storage_reader_not_connected")
    if read_events is None:
        try:
            producer = importlib.import_module("src.ingest.execution_contract")
        except ModuleNotFoundError as error:
            if error.name != "src.ingest.execution_contract":
                return _unverified("data72_source_import_failed")
            return _unverified("data72_not_composed")
        if getattr(producer, "VERSION", None) != DATA_EXECUTION_VERSION:
            return _unverified("data72_contract_version_mismatch")
        read_events = getattr(producer, "read_execution_events", None)
        if not callable(read_events):
            return _unverified("data72_reader_unavailable")
    try:
        rows = read_events(
            store, category=query.category, symbol=query.symbol,
            start_ms=query.start_ms, end_ms=query.end_ms, limit=_MAX_EVENTS,
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return _unverified("data72_source_readback_unverified")
    except Exception:
        return _unverified("data72_source_readback_unavailable")
    if not isinstance(rows, list):
        return _unverified("data72_event_contract_invalid")
    if len(rows) >= _MAX_EVENTS:
        return _unverified("data72_full_page_boundary_unproven")
    if not rows:
        return _unverified("data72_no_execution_evidence")
    observed_times = []
    seen_ids = set()
    for event in rows:
        if not isinstance(event, dict):
            return _unverified("data72_event_contract_invalid")
        event_ms, observed_ms = event.get("event_ms"), event.get("observed_ms")
        exec_id = event.get("exec_id")
        if (
            event.get("contract_version") != DATA_EXECUTION_VERSION
            or event.get("venue") != "bybit"
            or event.get("category") != query.category
            or event.get("symbol") != query.symbol
            or event.get("quantity_unit") != "source_native_linear_size"
            or event.get("taker_side") not in {"Buy", "Sell", "Unknown"}
            or not isinstance(exec_id, str) or not exec_id or exec_id in seen_ids
            or type(event_ms) is not int or type(observed_ms) is not int
            or not query.start_ms <= event_ms < query.end_ms
            or observed_ms < event_ms
            or event.get("publication_ms", "MISSING") is not None
            or event.get("source_coverage", {}).get("status") != "UNKNOWN"
            or event.get("availability", {}).get("status") != "UNVERIFIED"
        ):
            return _unverified("data72_event_contract_invalid")
        if (query.information_cutoff_ms is not None
                and observed_ms > query.information_cutoff_ms):
            return _unverified("data72_late_source_knowledge")
        receipts = event.get("source_receipts")
        if not isinstance(receipts, list) or not receipts:
            return _unverified("data72_receipt_missing")
        for receipt in receipts:
            if not isinstance(receipt, dict):
                return _unverified("data72_receipt_missing")
            if any(not isinstance(receipt.get(k), str) or not receipt[k]
                   for k in ("evidence_id", "page_ref")):
                return _unverified("data72_receipt_missing")
            if any(not isinstance(receipt.get(k), str)
                   or not _SHA256.fullmatch(receipt[k])
                   for k in ("http_sha256", "page_sha256", "row_sha256")):
                return _unverified("data72_receipt_missing")
        seen_ids.add(exec_id)
        observed_times.append(observed_ms)
    evidence_revision = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False)
        .encode("utf-8")
    ).hexdigest()
    return {
        **_unverified("data72_observed_sample_not_historical_visibility"),
        "observed_events": len(rows),
        "first_observed_ms": min(observed_times),
        "last_observed_ms": max(observed_times),
        "evidence_revision": "sample:" + evidence_revision,
        "revision_kind": "observed_execution_sample_digest",
        "sample_full_page_witness": False,
    }
