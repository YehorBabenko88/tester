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
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from src.integration.research_ui_projection import read_verified_ui_observation

MAX_WINDOW_MS = 7 * 86_400_000
MAX_ROWS = 99  # Below DATA's 100-row cap; exactly full page is not complete.
MAX_INTERVAL_MS = 86_400_000
MAX_RESPONSE_BYTES = 512 * 1024
MAX_WALL_MS = 10_000
MAX_CONCURRENT_READS = 2


def _clock():
    return time.monotonic()
_HEX = re.compile(r"[0-9a-f]{40}\Z")


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
        if not (0 < self.end_ms - self.start_ms <= MAX_WINDOW_MS
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
    if trusted_store is None or not callable(getattr(trusted_store, "read_bounded", None)) or not isinstance(trusted_identity, dict):
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
                or observed.get("method") != "tester.execution-metrics/2"
                or observed.get("quantity_unit") != (
                    "quote" if scope.category == "inverse" else "base")
                or observed.get("source_quantity_unit") != (
                    "source_native_inverse_contract" if scope.category == "inverse"
                    else "source_native_linear_size")
                or observed.get("utc_window") != {
                    "start_ms": scope.start_ms, "end_ms": scope.end_ms
                }):
            raise ReadbackDenied()
        bars = observed.get("bars")
        if not isinstance(bars, list) or len(bars) > 100:
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
            "method": observed["method"],
            "quantity_unit": observed["quantity_unit"],
            "source_quantity_unit": observed["source_quantity_unit"],
            "source": {"installation_id": ident, "source_sha": source_sha,
                       "observed_sample_fingerprint": observed["observed_sample_fingerprint"],
                       "source_receipts_digest": observed["source_receipts_digest"],
                       "publication_time": "UNKNOWN"},
            "quality": {"status": "UNVERIFIED", "coverage": "UNKNOWN",
                        "reasons": ["historical_completeness_not_proven"],
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
            result = observed_readback(scope, trusted_store=store, trusted_identity=identity)
            if (_clock() - started) * 1000 > MAX_WALL_MS:
                raise HTTPException(503, detail="analytics_readback_timeout")
            if result["status"] != "OBSERVED_UNVERIFIED":
                raise HTTPException(503, detail="source_readback_not_verified")
            return {"schema_version": "1.0", "data": result}
        finally:
            slots.release()

    return router
