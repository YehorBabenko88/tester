"""Bounded, read-only UI envelope for canonical DATA->RESEARCH observations.

This module uses the existing PostgreSQL-backed observation reader; it never
creates a separate Store, DB connection, network client, or HTTP route.
Historical coverage and publication knowledge remain UNKNOWN. No F admission.
"""

import json
import re
from copy import deepcopy

from src.analytics.core import quantity_unit
from src.ingest.execution_contract import MAX_ROWS, MAX_WINDOW_MS, SOURCE_QUANTITY_UNITS, SYMBOL

SCHEMA = "tester.analytics-readback/1"
EXECUTION_METHOD = "tester.execution-metrics/2"
OBSERVATION_SCHEMA = "tester.research-ui-observation/1"
PANELS = ("vwap", "footprint", "delta", "cvd", "poc")
MAX_BARS = 1000
MAX_RESPONSE_BYTES = 131072
_HEX64 = re.compile(r"[a-f0-9]{64}\Z")


class ReadbackRefused(ValueError):
    """Fixed reason code only; never expose source input or connection strings."""


def _deny(reason):
    raise ReadbackRefused(reason)


def validate_scope(*, category, symbol, start_ms, end_ms, interval_ms, as_of_ms, limit):
    """Closed, bounded UTC scope consistent with the canonical DATA contract."""
    if category not in SOURCE_QUANTITY_UNITS or not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        _deny("unsupported_market_scope")
    if any(type(value) is not int for value in (start_ms, end_ms, interval_ms, as_of_ms, limit)):
        _deny("integer_utc_scope_required")
    if not (0 <= start_ms < end_ms <= as_of_ms and end_ms - start_ms <= MAX_WINDOW_MS):
        _deny("closed_utc_window_required")
    if not (interval_ms > 0 and (end_ms - start_ms) % interval_ms == 0):
        _deny("invalid_interval_alignment")
    if not (1 <= (end_ms - start_ms) // interval_ms <= MAX_BARS and 1 <= limit <= MAX_ROWS):
        _deny("readback_budget_exceeded")
    return {"category": category, "symbol": symbol, "start_ms": start_ms,
            "end_ms": end_ms, "interval_ms": interval_ms, "as_of_ms": as_of_ms, "limit": limit}


def package_observation(observation, *, scope):
    """Format canonical readback; NEVER establish source trust from caller data.

    Input must come from read_verified_ui_observation, not an HTTP client object.
    Even a syntactically valid forged input cannot grant completeness or F access.
    """
    required = ("category", "symbol", "start_ms", "end_ms", "interval_ms", "as_of_ms", "limit")
    if not isinstance(scope, dict) or set(scope) != set(required):
        _deny("invalid_server_scope")
    scope = validate_scope(**scope)
    if not isinstance(observation, dict):
        _deny("canonical_observation_missing")
    if (observation.get("schema_version") != OBSERVATION_SCHEMA
        or observation.get("status") != "OBSERVED_UNVERIFIED"
        or observation.get("source_verification") != "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS"
        or observation.get("source_coverage") != "UNKNOWN"
        or observation.get("publication_time") != "UNKNOWN"
        or observation.get("historical_known_at_ms", "MISSING") is not None
        or observation.get("f_admission") != "DENIED_UNKNOWN_COVERAGE"
        or observation.get("g_e_execution") != "NOT_AUTHORIZED"
        or observation.get("http_route_status") != "NOT_REGISTERED"):
        _deny("canonical_source_authority_invalid")
    if observation.get("utc_window") != {"start_ms": scope["start_ms"], "end_ms": scope["end_ms"]}:
        _deny("source_window_mismatch")
    if not isinstance(observation.get("metrics_contract"), dict):
        _deny("method_or_native_unit_mismatch")
    if (observation.get("method") != EXECUTION_METHOD
        or observation["metrics_contract"].get("status") != "CURRENT"
        or observation.get("quantity_unit") != quantity_unit(scope["category"])
        or observation.get("source_quantity_unit") != SOURCE_QUANTITY_UNITS[scope["category"]]):
        _deny("method_or_native_unit_mismatch")
    for field in ("source_receipts_digest", "data_execution_digest", "observed_sample_fingerprint", "metrics_digest"):
        value = observation.get(field)
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            _deny("source_evidence_digest_missing")
    bars = observation.get("bars")
    if not isinstance(bars, list) or not 1 <= len(bars) <= MAX_BARS:
        _deny("source_bars_outside_budget")
    expected_bars = (scope["end_ms"] - scope["start_ms"]) // scope["interval_ms"]
    if len(bars) != expected_bars or any(
        not isinstance(bar, dict)
        or bar.get("start_ms") != scope["start_ms"] + index * scope["interval_ms"]
        or bar.get("end_ms") != scope["start_ms"] + (index + 1) * scope["interval_ms"]
        for index, bar in enumerate(bars)
    ):
        _deny("source_bars_noncontiguous")
    if (type(observation.get("delta_complete")) is not bool
        or observation["delta_complete"] is not False
        or observation.get("cvd_prior_history_known") is not False):
        _deny("source_completion_claim_invalid")
    # No copying of self-attested install UUID/DSN or caller-provided quality.
    envelope = {
        "schema_version": SCHEMA,
        "metric_kind": "execution_metrics",
        "metric_method": EXECUTION_METHOD,
        "panels": list(PANELS),
        "market": {"venue": "bybit", "category": scope["category"], "symbol": scope["symbol"]},
        "window": {name: scope[name] for name in ("start_ms", "end_ms", "interval_ms", "as_of_ms")},
        "quantity_unit": quantity_unit(scope["category"]),
        "source_quantity_unit": SOURCE_QUANTITY_UNITS[scope["category"]],
        "source": {
            "reader": "src.integration.research_ui_projection.read_verified_ui_observation",
            "receipts_sha256": observation["source_receipts_digest"],
            "data_sha256": observation["data_execution_digest"],
            "metrics_sha256": observation["metrics_digest"],
            "sample_sha256": observation["observed_sample_fingerprint"],
            "dataset_revision": None,
            "publication_time": "UNKNOWN",
            "historical_coverage": "UNKNOWN",
        },
        "quality": {"status": "UNVERIFIED", "reasons": ["historical_coverage_unknown", "publication_time_unknown"]},
        "historical_data_complete": False,
        "admitted_to_f": False,
        "strategy_admission": "DENIED",
        "http_route_registered": False,
        "observed_bars": deepcopy(bars),
    }
    try:
        raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        _deny("invalid_observation_serialization")
    if len(raw) > MAX_RESPONSE_BYTES:
        _deny("readback_response_bytes_exceeded")
    return envelope


def read_observed_execution_envelope(store, *, category, symbol, start_ms, end_ms,
                                     interval_ms, tick_size, as_of_ms, limit=MAX_ROWS):
    """Read through existing canonical store-backed projection; no SQL or HTTP.

    Store is supplied by trusted runtime, not constructed from a user DSN.
    The API engineer owns endpoint registration; it is deliberately absent.
    """
    scope = validate_scope(category=category, symbol=symbol, start_ms=start_ms,
                           end_ms=end_ms, interval_ms=interval_ms, as_of_ms=as_of_ms, limit=limit)
    from src.integration.research_ui_projection import read_verified_ui_observation
    observation = read_verified_ui_observation(
        store, category=category, symbol=symbol, start_ms=start_ms, end_ms=end_ms,
        interval_ms=interval_ms, tick_size=tick_size, as_of_ms=as_of_ms, limit=limit,
    )
    return package_observation(observation, scope=scope)
