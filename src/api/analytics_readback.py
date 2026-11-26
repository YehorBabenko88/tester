"""Read-only, fail-closed DATA->RESEARCH analytics observation boundary.

This additive route adapter never constructs its own market reader, calculator
or provenance proof. A trusted caller must supply a reader with an already
validated ownership/DB binding. Until then a 503 response is required.
"""
from __future__ import annotations

import json
import re
import time
from threading import BoundedSemaphore
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from src.analytics.core import quantity_unit
from src.analytics.research_metrics import CURRENT_METHOD
from src.ingest.execution_contract import SOURCE_QUANTITY_UNITS
from src.integration.research_ui_projection import read_verified_ui_observation

MAX_WINDOW_MS = 7 * 86_400_000
MAX_ROWS = 99  # Below DATA's 100-row cap; exactly full page is not complete.
MAX_INTERVAL_MS = 86_400_000
ALLOWED_INTERVALS_MS = (60_000, 180_000, 300_000, 900_000, 1_800_000,
                        3_600_000, 7_200_000, 14_400_000, 21_600_000,
                        43_200_000, 86_400_000)
MAX_RESPONSE_BYTES = 128 * 1024
MAX_WALL_MS = 10_000
MAX_CONCURRENT_READS = 2


def _clock():
    return time.monotonic()


_HEX = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class ReadbackScope(BaseModel):
    """Strict external query; forbid unknown knobs, client DSNs or identity."""
    model_config = ConfigDict(extra="forbid", strict=True)
    category: Literal["linear", "inverse"]
    symbol: str = Field(min_length=2, max_length=40, pattern=r"^[A-Z0-9]{2,40}$")
    start_ms: StrictInt = Field(ge=0, le=253402300799000)
    end_ms: StrictInt = Field(gt=0, le=253402300799000)
    interval_ms: StrictInt = Field(ge=60_000, le=MAX_INTERVAL_MS)
    tick_size: str = Field(min_length=1, max_length=32, pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
    as_of_ms: StrictInt = Field(ge=0, le=253402300799000)
    limit: StrictInt = Field(default=MAX_ROWS, ge=1, le=MAX_ROWS)

    @model_validator(mode="before")
    @classmethod
    def deny_extra(cls, value):
        if isinstance(value, dict) and any(k in value for k in (
                "installation_id", "source_sha", "database_url", "dsn")):
            raise ValueError("client_owned_source_authority_forbidden")
        return value

    @model_validator(mode="after")
    def bounded(self):
        from decimal import Decimal
        if not (self.interval_ms in ALLOWED_INTERVALS_MS
                and 0 < self.end_ms - self.start_ms <= MAX_WINDOW_MS
                and self.start_ms % self.interval_ms == 0
                and self.end_ms % self.interval_ms == 0
                and self.end_ms <= self.as_of_ms
                and (self.end_ms - self.start_ms) // self.interval_ms <= 100):
            raise ValueError("invalid_bounded_analytics_window")
        if Decimal(self.tick_size) <= 0:
            raise ValueError("invalid_tick_size")
        return self


class ReadbackDenied(Exception):
    """Fixed public refusal (never surface SQL, receipt, or source exceptions)."""


def _not_ready(reason: str) -> dict:
    return {
        "schema_version": "tester.analytics-readback/1",
        "status": "UNVERIFIED",
        "reason": reason,
        "quality": {"status": "UNVERIFIED", "coverage": "UNKNOWN",
                    "reasons": [reason], "admitted_to_f": False},
        "admitted_to_f": False,
    }


def observed_readback(scope: ReadbackScope, *, trusted_store=None, trusted_identity=None):
    """Call only DATA persisted-proof reader, then canonical C calculator via RESEARCH.

    The identity must be injected by server-side owner-verified runtime code,
    never from the request; no ambient DSN or self-authentication fallback.
    """
    if (trusted_store is None or not callable(getattr(trusted_store, "read_bounded", None))
            or not isinstance(trusted_identity, dict)):
        return _not_ready("trusted_storage_binding_unavailable")
    if trusted_identity.get("verified") is not True:
        return _not_ready("trusted_storage_binding_unavailable")
    ident = trusted_identity.get("installation_id")
    source_sha = trusted_identity.get("source_sha")
    try:
        canonical_uuid = str(UUID(ident))
    except (TypeError, ValueError, AttributeError):
        return _not_ready("trusted_storage_binding_unavailable")
    if (ident != canonical_uuid or not isinstance(
            source_sha, str) or not _HEX.fullmatch(source_sha)):
        return _not_ready("trusted_storage_binding_unavailable")
    try:
        observed = read_verified_ui_observation(
            trusted_store, category=scope.category, symbol=scope.symbol,
            start_ms=scope.start_ms, end_ms=scope.end_ms,
            interval_ms=scope.interval_ms, tick_size=scope.tick_size,
            as_of_ms=scope.as_of_ms, limit=scope.limit,
        )
        if (observed.get("schema_version") != "tester.research-ui-observation/1"
                or observed.get("status") != "OBSERVED_UNVERIFIED"
                or observed.get("f_admission") != "DENIED_UNKNOWN_COVERAGE"
                or observed.get("source_coverage") != "UNKNOWN"
                or observed.get("publication_time") != "UNKNOWN"
                or observed.get("source_verification") != "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS"
                or observed.get("historical_known_at_ms", "MISSING") is not None
                or observed.get("delta_complete") is not False
                or observed.get("http_route") is not None
                or observed.get("method") != CURRENT_METHOD
                or observed.get("quantity_unit") != quantity_unit(scope.category)
                or observed.get("source_quantity_unit") != SOURCE_QUANTITY_UNITS[scope.category]
                or observed.get("vwap_unit") != "quote_per_base"
                or not isinstance(observed.get("metrics_contract"), dict)
                or observed["metrics_contract"].get("status") != "CURRENT"
                or observed["metrics_contract"].get("method") != CURRENT_METHOD
                or observed["metrics_contract"].get("quantity_unit") != quantity_unit(scope.category)
                or observed.get("g_e_execution") != "NOT_AUTHORIZED"
                or observed.get("cvd_prior_history_known") is not False
                or observed.get("http_route_status") != "NOT_REGISTERED"
                or observed.get("utc_window") != {
                    "start_ms": scope.start_ms, "end_ms": scope.end_ms
                }):
            raise ReadbackDenied()
        bars = observed.get("bars")
        expected_bars = (scope.end_ms - scope.start_ms) // scope.interval_ms
        if not isinstance(bars, list) or len(bars) != expected_bars:
            raise ReadbackDenied()
        for index, bar in enumerate(bars):
            if (not isinstance(bar, dict)
                    or type(bar.get("start_ms")) is not int
                    or type(bar.get("end_ms")) is not int
                    or bar["start_ms"] != scope.start_ms + index * scope.interval_ms
                    or bar["end_ms"] != scope.start_ms + (index + 1) * scope.interval_ms):
                raise ReadbackDenied()
        for key in ("source_receipts_digest", "data_execution_digest",
                    "metrics_digest", "observed_sample_fingerprint"):
            if not isinstance(observed.get(key), str) or not _SHA256.fullmatch(observed[key]):
                raise ReadbackDenied()
        # Provider/DB values remain immutable exact strings. A source observation
        # is not complete history; client cannot turn it into strategy admission.
        result = {
            "schema_version": "tester.analytics-readback/1",
            "status": "OBSERVED_UNVERIFIED",
            "reason": "historical_completeness_not_proven",
            "market": {"venue": "bybit", "category": scope.category,
                       "symbol": scope.symbol},
            "window": {"start_ms": scope.start_ms, "end_ms": scope.end_ms,
                       "interval_ms": scope.interval_ms, "as_of_ms": scope.as_of_ms},
            "metric_kind": "execution_metrics",
            "metric_method": CURRENT_METHOD,
            "metric_method_version": "2",
            "method": observed["method"],
            "historical_data_complete": False,
            "strategy_admission": "DENIED",
            "http_route_registered": False,
            "quantity_unit": observed["quantity_unit"],
            "source_quantity_unit": observed["source_quantity_unit"],
            # The synthetic callback UUID/SHA are *not* source-attestation
            # evidence. Do not expose them as trusted identity in this wire
            # contract; owner-side D verification stays out of this readback.
            "source": {"reader": "src.integration.research_ui_projection.read_verified_ui_observation",
                       "observed_sample_fingerprint": observed["observed_sample_fingerprint"],
                       "sample_sha256": observed["observed_sample_fingerprint"],
                       "source_receipts_digest": observed["source_receipts_digest"],
                       "receipts_sha256": observed["source_receipts_digest"],
                       "data_sha256": observed["data_execution_digest"],
                       "metrics_sha256": observed["metrics_digest"],
                       "dataset_revision": None,
                       "historical_coverage": "UNKNOWN",
                       "publication_time": "UNKNOWN"},
            "quality": {"status": "UNVERIFIED", "coverage": "UNKNOWN",
                        "historical_data_complete": False,
                        "reasons": ["historical_coverage_unknown", "publication_time_unknown"],
                        "admitted_to_f": False},
            "metrics": {"method": observed["method"], "status": "DERIVED_UNVERIFIED",
                        "quantity_unit": observed["quantity_unit"],
                        "bars": bars, "admitted_to_f": False},
            "admitted_to_f": False,
            "limits": {"max_rows": MAX_ROWS, "max_range_ms": MAX_WINDOW_MS,
                       "max_bars": 100, "max_response_bytes": MAX_RESPONSE_BYTES},
        }
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ReadbackDenied()
        return result
    except Exception:
        return _not_ready("source_readback_not_verified")


def create_readback_router(*, trusted_binding=None):
    """Optional additive endpoint; dependency injects already-trusted tuple.

    Do not mount in production until D/A authorize a protected runtime binding.
    """
    router = APIRouter()
    slots = BoundedSemaphore(MAX_CONCURRENT_READS)

    @router.get("/api/analytics/readback", include_in_schema=False)
    def readback(request: Request, category: str, symbol: str,
                 start_ms: int, end_ms: int, interval_ms: int,
                 tick_size: str, as_of_ms: int, limit: int = MAX_ROWS):
        # FastAPI converts query integer strings before strict internal validation.
        # Unexpected fields, including a client-supplied DSN/UUID, are refused.
        allowed = {"category", "symbol", "start_ms", "end_ms",
                   "interval_ms", "tick_size", "as_of_ms", "limit"}
        # Unlike ordinary public market metadata, this optional source-backed
        # read is explicitly denied to cross-site callers. The existing
        # TrustedHostMiddleware checks host before this route in product app.
        origin = request.headers.get("origin")
        host_port = request.url.port
        allowed_origins = {
            f"http://127.0.0.1:{host_port}",
            f"http://localhost:{host_port}",
            f"http://[::1]:{host_port}",
        }
        if ((origin is not None and origin not in allowed_origins)
                or request.headers.get("sec-fetch-site") == "cross-site"):
            raise HTTPException(403, detail="analytics_cross_origin_denied")
        if (set(request.query_params) - allowed or
                any(len(request.query_params.getlist(key)) != 1
                    for key in request.query_params)):
            raise HTTPException(422, detail="invalid_readback_scope")
        try:
            scope = ReadbackScope(
                category=category, symbol=symbol, start_ms=start_ms,
                end_ms=end_ms, interval_ms=interval_ms,
                tick_size=tick_size, as_of_ms=as_of_ms, limit=limit)
        except (ValidationError, ValueError):
            raise HTTPException(422, detail="invalid_readback_scope") from None
        if trusted_binding is None:
            raise HTTPException(503, detail="analytics_source_not_configured")
        if not slots.acquire(blocking=False):
            raise HTTPException(429, detail="analytics_readback_concurrency_limit")
        started = _clock()
        try:
            try:
                binding = trusted_binding()
            except Exception:
                raise HTTPException(503, detail="trusted_storage_binding_unavailable") from None
            if not isinstance(binding, tuple) or len(binding) != 2:
                raise HTTPException(503, detail="trusted_storage_binding_unavailable")
            store, identity = binding
            # A single-use connection budget is limited by the same trusted
            # canonical Store. Keep the DATA path and Store read implementation.
            # A Python post-read wall check alone cannot interrupt PostgreSQL.
            from src.storage.store import Store
            if isinstance(store, Store):
                from psycopg.conninfo import conninfo_to_dict, make_conninfo
                elapsed_ms = int((_clock() - started) * 1000)
                remaining_ms = MAX_WALL_MS - elapsed_ms
                if remaining_ms < 2000:
                    # libpq rounds its connect_timeout to whole seconds and
                    # cannot honor a sub-two-second remaining admission slot.
                    raise HTTPException(503, detail="analytics_readback_timeout")
                try:
                    parts = conninfo_to_dict(store._dsn)
                    port = parts.get("port", "")
                    if (
                        parts.get("host") != "127.0.0.1"
                        or parts.get("hostaddr", "127.0.0.1") != "127.0.0.1"
                        or not port.isdecimal()
                        or not 1024 <= int(port) <= 65535
                        or int(port) == 5432
                        or not parts.get("dbname")
                    ):
                        raise ValueError("untrusted_local_storage_endpoint")
                    # Store.connect enforces these very same strict budgets;
                    # no SQL/reader implementation is duplicated here.
                    parts["connect_timeout"] = str(min(5, remaining_ms // 1000))
                    parts["options"] = (
                        "-c timezone=UTC -c statement_timeout="
                        + str(min(remaining_ms, 5000))
                        + " -c lock_timeout=" + str(min(remaining_ms, 5000))
                    )
                    store = Store(make_conninfo(**parts))
                except Exception:
                    raise HTTPException(
                        503, detail="trusted_storage_binding_unavailable"
                    ) from None
            result = observed_readback(scope, trusted_store=store, trusted_identity=identity)
            if (_clock() - started) * 1000 > MAX_WALL_MS:
                raise HTTPException(503, detail="analytics_readback_timeout")
            if result["status"] != "OBSERVED_UNVERIFIED":
                raise HTTPException(503, detail="source_readback_not_verified")
            return {"schema_version": "1.0", "data": result}
        finally:
            slots.release()

    return router
