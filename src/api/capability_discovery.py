"""Read-only, evidence-bound capability discovery for the terminal.

A route existing is not evidence of an available dataset. Probe at most one
small, bounded page through the configured reader. Never write or download.
"""

import hashlib
import json
import logging
import re
from urllib.parse import parse_qs, urlsplit

from contracts.v1.validate import ContractError

from .capability_source_contracts import dependencies_for_panel
from .models import Query
from .providers import ProviderUnavailable, UnavailableReader
from .scope import ScopeError, SourceIntervalError, bound_response, resource_query

PANELS = (
    "candles",
    "volume",
    "footprint",
    "delta",
    "cvd",
    "profile",
    "poc",
    "vwap",
    "open_interest",
    "funding",
    "mark",
    "index",
)
INTERVALS = frozenset(("1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M"))
# Do not list footprint: its financial query requires a separate frozen cutoff
# and explicit price-step selection. No /api/analytics route is implemented.
READABLE = {
    "candles": ("candles", "/api/candles", "candle"),
    "volume": ("candles", "/api/candles", "candle"),
    "open_interest": ("open-interest", "/api/open-interest", "observation"),
    "funding": ("funding", "/api/funding", "observation"),
    "mark": ("price-candles", "/api/price-candles", "price"),
    "index": ("price-candles", "/api/price-candles", "price"),
}
DERIVATIVES = frozenset(("open_interest", "funding", "mark", "index"))


def _unavailable(panel, status, reason):
    record = dict(panel=panel, status=status, reason=reason)
    dependencies = dependencies_for_panel(panel)
    if dependencies:
        record["source_contract_dependencies"] = dependencies
        record["historical_coverage"] = "UNKNOWN"
        record["admitted_to_f"] = False
    return record


def _source_ref(provenance, panel, query):
    """Require declared Bybit-origin evidence; reject synthetic source kinds."""
    expected_price_endpoint = {
        "mark": "/v5/market/mark-price-kline",
        "index": "/v5/market/index-price-kline",
    }.get(panel)
    if not isinstance(provenance, list) or not provenance:
        return None
    refs = []
    for p in provenance:
        if not isinstance(p, dict) or p.get("source_kind") not in {
            "bybit_rest",
            "bybit_archive",
        }:
            return None
        if (
            not isinstance(p.get("source_hash"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", p["source_hash"])
            or not isinstance(p.get("ingestion_run"), str)
            or not p["ingestion_run"]
        ):
            return None
        ref = p.get("source_ref")
        if not isinstance(ref, str) or not 1 <= len(ref) <= 512:
            return None
        if expected_price_endpoint:
            url = urlsplit(ref)
            args = parse_qs(url.query)
            if (
                p.get("source_kind") != "bybit_rest"
                or (url.scheme, url.netloc, url.path)
                != ("https", "api.bybit.com", expected_price_endpoint)
                or url.fragment
                or any(
                    args.get(key) != [value]
                    for key, value in {
                        "category": query.category,
                        "symbol": query.symbol,
                        "interval": query.interval,
                    }.items()
                )
            ):
                return None
        elif "://" in ref and not (
            ref.startswith("https://api.bybit.com/") or ref.startswith("https://public.bybit.com/")
        ):
            return None
        refs.append(ref)
    return sorted(refs)[0] if refs else None


def _verified_page(reader, panel, query):
    resource, route, record_kind = READABLE[panel]
    source_query = query.model_copy(
        update={
            "limit": 10,
            "cursor": None,
            "source_interval": None,
            "candle_kind": (
                "official_mark"
                if panel == "mark"
                else "official_index"
                if panel == "index"
                else "official_trade"
            ),
        }
    )
    if resource == "price-candles":
        # This is an explicitly opt-in 1.1 reader; 1.0 /api/candles cannot
        # return mark/index, even when its query accepts candle_kind.
        from src.integration.reference_prices import PriceEnvelope, ReferencePriceReader

        if not isinstance(reader, ReferencePriceReader):
            return None, "reference_price_source_not_connected"
        payload = reader.read(resource, source_query)
        PriceEnvelope.model_validate(payload)
        if payload.get("schema_version") != "1.1":
            return None, "price_contract_mismatch"
        rows = payload["data"]
        for row in rows:
            if (
                row.get("kind") != source_query.candle_kind
                or row.get("category") != query.category
                or row.get("symbol") != query.symbol
                or row.get("interval") != query.interval
                or not query.start_ms <= row["start_ms"] < row["end_ms"] <= query.end_ms
                or row.get("is_closed") is not True
            ):
                return None, "price_scope_mismatch"
            row_sources = row.get("provenance")
            if (
                not isinstance(row_sources, list)
                or not row_sources
                or any(
                    source not in payload["provenance"]
                    or source.get("timestamp_precision") != "ms"
                    or source.get("source_timestamp") != str(row["timestamp_ms"])
                    for source in row_sources
                )
            ):
                return None, "price_source_timestamp_mismatch"
    else:
        if resource == "funding":
            source_query = source_query.model_copy(update={"source_interval": "event"})
        elif resource == "open-interest":
            source_query = source_query.model_copy(
                update={"source_interval": query.source_interval or "5min"}
            )
        source_query = resource_query(resource, source_query)
        payload = reader.read(resource, source_query)
        bound_response(resource, record_kind, payload, source_query)
        rows = payload["data"]
    if not rows:
        return None, "no_verified_observations_in_range"
    if payload.get("availability", {}).get("status") not in ("AVAILABLE", "PARTIAL"):
        return None, "source_availability_not_qualified"
    if any(
        row.get("availability", {}).get("status") not in ("AVAILABLE", "PARTIAL") for row in rows
    ):
        return None, "row_availability_not_qualified"
    provenance = payload.get("provenance", [])
    ref = _source_ref(provenance, panel, query)
    if not ref:
        return None, "source_provenance_not_qualified"
    # This digest is a content address for the bounded, verified observation
    # page. It is deliberately NOT described as whole-history coverage.
    digest = hashlib.sha256(
        json.dumps(
            {
                "query": query.model_dump(mode="json", exclude_none=True),
                "rows": rows,
                "provenance": provenance,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    return (
        dict(
            panel=panel,
            status="PARTIAL",
            reason="bounded_observations_only",
            resource=route,
            revision="page:" + digest,
            revision_kind="observed_page_digest",
            source_ref=ref,
            schema_version="1.1" if resource == "price-candles" else "1.0",
            observed_rows=len(rows),
            coverage_status="UNKNOWN",
            historical_dataset_revision="UNPINNED",
            history_complete=False,
            publication_known=False,
            admitted_to_f=False,
            source_contract_version="1.1" if resource == "price-candles" else "1.0",
            source_contract_ref=(
                "contracts/v1_1/price_candle.schema.json"
                if resource == "price-candles"
                else "contracts/v1/market.schema.json"
            ),
            **({"candle_kind": source_query.candle_kind} if panel in ("mark", "index") else {}),
        ),
        None,
    )


def discover(reader, query: Query, *, authorized=False, price_route_enabled=False):
    """Never promote a source based on an unverified reader advertisement."""
    result = {}
    for panel in PANELS:
        if not query.symbol:
            result[panel] = _unavailable(panel, "UNVERIFIED", "instrument_required")
        elif query.interval not in INTERVALS:
            result[panel] = _unavailable(panel, "UNAVAILABLE", "timeframe_not_supported")
        elif query.category == "spot" and panel in DERIVATIVES:
            result[panel] = _unavailable(panel, "UNAVAILABLE", "not_applicable_to_spot")
        elif panel not in READABLE:
            result[panel] = _unavailable(panel, "UNAVAILABLE", "endpoint_contract_not_supported")
        elif not authorized:
            result[panel] = _unavailable(panel, "UNVERIFIED", "source_not_authorized")
        elif panel in ("mark", "index") and not price_route_enabled:
            result[panel] = _unavailable(panel, "UNVERIFIED", "reference_price_route_not_enabled")
        elif isinstance(reader, UnavailableReader):
            result[panel] = _unavailable(panel, "UNVERIFIED", "provider_not_connected")
        else:
            try:
                qualified, reason = _verified_page(reader, panel, query)
                result[panel] = qualified or _unavailable(panel, "UNVERIFIED", reason)
            except ProviderUnavailable:
                result[panel] = _unavailable(panel, "UNVERIFIED", "source_not_connected")
            except (
                ContractError,
                ScopeError,
                SourceIntervalError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
            ):
                result[panel] = _unavailable(panel, "UNVERIFIED", "source_contract_not_verified")
            except Exception as exc:
                logging.getLogger("tester.api").warning(
                    "capability_source_probe_failed",
                    extra={"panel": panel, "exception_type": type(exc).__name__},
                )
                result[panel] = _unavailable(panel, "UNVERIFIED", "source_probe_unavailable")
    return {"panels": result}
