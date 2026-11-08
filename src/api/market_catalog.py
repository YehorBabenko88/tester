"""Opt-in Issue 40 catalog foundation; never mounted or started implicitly."""

import asyncio
import hashlib
import json
import random
import threading
import time
from copy import deepcopy
from decimal import Decimal, InvalidOperation

import requests
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

CATEGORIES = ("linear", "inverse")
METRICS = {
    "price": "lastPrice",
    "price_change_24h": "price24hPcnt",
    "volume_24h": "volume24h",
    "turnover_24h": "turnover24h",
    "open_interest": "openInterest",
}
UNAVAILABLE = ("volatility", "price_change_1h", "oi_change_1h", "oi_change_24h", "market_cap")


class SnapshotConflict(ValueError):
    """The caller must restart pagination against the new revision."""


class AcquisitionError(ValueError):
    pass


class BybitCatalogSource:
    """One bounded public HTTP attempt; scheduler owns retries, not browsers."""

    def call(self, endpoint, params):
        if endpoint not in {"/instruments-info", "/tickers"}:
            raise ValueError("Unsupported public catalog endpoint")
        url = "https://api.bybit.com/v5/market" + endpoint
        with requests.get(url, params=params, timeout=(5, 15), stream=True) as response:
            if response.status_code == 429:
                raise AcquisitionError("rate_limited")
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if len(content) + len(chunk) > 8_000_000:
                    raise AcquisitionError("response_budget")
                content.extend(chunk)
            body = json.loads(content)
            if body.get("retCode") == 10006:
                raise AcquisitionError("rate_limited")
            if body.get("retCode") != 0 or not isinstance(body.get("result"), dict):
                raise AcquisitionError("invalid_response")
            result = dict(body["result"])
            result["_source_time_ms"] = body.get("time")
            return result, response.url


def _category(category):
    if category not in CATEGORIES:
        raise ValueError("Unsupported category")


def _number(value):
    if not isinstance(value, str) or len(value) > 100:
        return None
    try:
        return value if Decimal(value).is_finite() else None
    except InvalidOperation:
        return None


class CatalogService:
    def __init__(self, source, *, clock=time.time, max_pages=100, max_rows=100000):
        if not 1 <= max_pages <= 100 or not 1 <= max_rows <= 100000:
            raise ValueError("Invalid acquisition budget")
        self.source, self.clock = source, clock
        self.max_pages, self.max_rows = max_pages, max_rows
        self._lock = threading.Lock()
        self._refresh = {category: threading.Lock() for category in CATEGORIES}
        self._snapshots, self._attempts = {}, {}
        self._cancel = threading.Event()
        self._wake = None
        self._task = None
        self._manual = set()

    def refresh(self, category):
        _category(category)
        if not self._refresh[category].acquire(blocking=False):
            return False
        rows, pages, cursor, seen = {}, 0, None, set()
        completed = False
        try:
            while pages < self.max_pages:
                if self._cancel.is_set():
                    raise AcquisitionError("cancelled")
                params = {"category": category, "status": "Trading", "limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                result, url = self.source.call("/instruments-info", params)
                batch = result.get("list")
                if result.get("category") != category or not isinstance(batch, list):
                    raise AcquisitionError("invalid_response")
                pages += 1
                for raw in batch:
                    if not isinstance(raw, dict) or not isinstance(raw.get("symbol"), str):
                        raise AcquisitionError("invalid_instrument")
                    symbol = raw["symbol"]
                    if not symbol or len(symbol) > 100 or raw.get("status") != "Trading":
                        raise AcquisitionError("invalid_instrument")
                    if symbol in rows:
                        raise AcquisitionError("duplicate_instrument")
                    rows[symbol] = {
                        "symbol": symbol,
                        "category": category,
                        "status": raw["status"],
                        "contract_type": raw.get("contractType"),
                        "settle_coin": raw.get("settleCoin"),
                        "source": url,
                    }
                    if len(rows) > self.max_rows:
                        raise AcquisitionError("row_budget")
                if "nextPageCursor" not in result or not isinstance(result["nextPageCursor"], str):
                    raise AcquisitionError("cursor_missing")
                cursor = result["nextPageCursor"]
                if not cursor:
                    completed = True
                    break
                if cursor in seen:
                    raise AcquisitionError("repeated_cursor")
                seen.add(cursor)
            if not completed:
                raise AcquisitionError("page_budget")
            if self._cancel.is_set():
                raise AcquisitionError("cancelled")
            ticker_result, ticker_url = self.source.call("/tickers", {"category": category})
            if self._cancel.is_set():
                raise AcquisitionError("cancelled")
            tickers = ticker_result.get("list")
            if ticker_result.get("category") != category or not isinstance(tickers, list):
                raise AcquisitionError("invalid_tickers")
            indexed = {}
            for item in tickers:
                if not isinstance(item, dict) or not isinstance(item.get("symbol"), str):
                    raise AcquisitionError("invalid_tickers")
                if item["symbol"] in indexed:
                    raise AcquisitionError("duplicate_ticker")
                indexed[item["symbol"]] = item
            now = self.clock()
            for symbol, row in rows.items():
                ticker = indexed.get(symbol, {})
                row["metrics"] = {}
                for name, field in METRICS.items():
                    value = _number(ticker.get(field))
                    row["metrics"][name] = {
                        "value": value,
                        "state": "OBSERVED" if value is not None else "UNAVAILABLE",
                        "source": ticker_url,
                        "observed_at": now,
                        "source_time_ms": ticker_result.get("_source_time_ms"),
                        "reason": None if value is not None else "not_provided",
                    }
                for name in UNAVAILABLE:
                    row["metrics"][name] = {
                        "value": None,
                        "state": "UNAVAILABLE",
                        "source": None,
                        "observed_at": None,
                        "source_time_ms": None,
                        "reason": "independent_source_required"
                        if name == "market_cap"
                        else "history_not_observed",
                    }
            snapshot = {"rows": list(rows.values()), "last_success": now, "pages": pages}
            snapshot["revision"] = hashlib.sha256(
                json.dumps(snapshot, sort_keys=True).encode()
            ).hexdigest()
            with self._lock:
                self._snapshots[category] = snapshot
                self._attempts[category] = {
                    "completeness": "EXHAUSTED",
                    "loaded": len(rows),
                    "last_error": None,
                    "at": now,
                }
            return True
        except Exception as exc:
            reason = str(exc) if isinstance(exc, AcquisitionError) else "source_failure"
            with self._lock:
                self._attempts[category] = {
                    "completeness": "PARTIAL" if rows else "UNKNOWN",
                    "loaded": len(rows),
                    "last_error": reason,
                    "at": self.clock(),
                }
            return False
        finally:
            self._refresh[category].release()

    def query(
        self,
        category,
        *,
        search="",
        status="Trading",
        contract_type=None,
        settle_coin=None,
        sort="symbol",
        direction="none",
        offset=0,
        limit=100,
        revision=None,
    ):
        _category(category)
        fields = {"symbol", "category", "status", "contract_type", "settle_coin"}
        if sort not in fields | METRICS.keys() | set(UNAVAILABLE) or direction not in {
            "asc",
            "desc",
            "none",
        }:
            raise ValueError("Unsupported sort")
        if not 1 <= limit <= 500 or not 0 <= offset <= 100000 or len(search) > 100:
            raise ValueError("Invalid page")
        with self._lock:
            snapshot = deepcopy(self._snapshots.get(category))
            attempt = deepcopy(
                self._attempts.get(
                    category,
                    {"completeness": "UNKNOWN", "loaded": 0, "last_error": None, "at": None},
                )
            )
        current_revision = snapshot["revision"] if snapshot else None
        if revision is not None and revision != current_revision:
            raise SnapshotConflict("Snapshot changed; restart pagination")
        stale = bool(
            snapshot and (attempt["last_error"] or self.clock() - snapshot["last_success"] >= 300)
        )
        rows = snapshot["rows"] if snapshot else []
        for row in rows:
            if stale:
                for metric in row["metrics"].values():
                    if metric["state"] == "OBSERVED":
                        metric["state"] = "STALE"
        rows = [
            row
            for row in rows
            if search.upper() in row["symbol"].upper()
            and (not status or row["status"] == status)
            and (not contract_type or row["contract_type"] == contract_type)
            and (not settle_coin or row["settle_coin"] == settle_coin)
        ]
        rows.sort(key=lambda row: (row["category"], row["symbol"]))
        if direction != "none":

            def value(row):
                return row.get(sort) if sort in fields else row["metrics"][sort]["value"]

            present = [row for row in rows if value(row) is not None]
            absent = [row for row in rows if value(row) is None]
            present.sort(
                key=lambda row: value(row) if sort in fields else Decimal(value(row)),
                reverse=direction == "desc",
            )
            rows = present + absent
        return {
            "schema_version": "1.0",
            "revision": current_revision,
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "rows": rows[offset : offset + limit],
            "attempt": attempt,
            "catalog": {
                "loaded": len(snapshot["rows"]) if snapshot else 0,
                "expected_total": None,
                "completeness": "EXHAUSTED" if snapshot else "UNKNOWN",
                "state": "STALE" if stale else "CURRENT" if snapshot else "UNAVAILABLE",
                "last_success": snapshot["last_success"] if snapshot else None,
                "last_error": attempt["last_error"],
                "scope": "Trading",
                "historical_listing_coverage": "UNKNOWN",
            },
        }

    async def start(self):
        if self._task is not None:
            raise RuntimeError("Catalog poller already started")
        self._cancel.clear()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._poll())

    async def stop(self):
        self._cancel.set()
        if self._wake:
            self._wake.set()
        if self._task:
            await self._task  # In-flight bounded public request finishes; never kill a worker.
        self._task = self._wake = None

    def request_refresh(self, category):
        _category(category)
        if self._wake is None:
            raise RuntimeError("Catalog poller not running")
        self._manual.add(category)
        self._wake.set()

    async def _poll(self):
        due = dict.fromkeys(CATEGORIES, 0.0)
        failures = dict.fromkeys(CATEGORIES, 0)
        while not self._cancel.is_set():
            self._wake.clear()
            for category in CATEGORIES:
                if self._cancel.is_set():
                    return
                now = time.monotonic()
                manual = category in self._manual
                self._manual.discard(category)
                # A manual refresh coalesces and respects a 30-second source budget.
                if now >= due[category] or (manual and now >= due[category] - 270):
                    ok = await asyncio.to_thread(self.refresh, category)
                    failures[category] = 0 if ok else min(3, failures[category] + 1)
                    due[category] = (
                        time.monotonic()
                        + min(1800, 300 * 2 ** failures[category])
                        + random.uniform(0, 15)
                    )
            timeout = max(0.1, min(due.values()) - time.monotonic())
            try:
                await asyncio.wait_for(self._wake.wait(), timeout)
            except TimeoutError:
                pass


def create_market_router(service):
    """Explicit composition only. Caller owns service.start/stop in app lifespan."""
    router = APIRouter(prefix="/api/market-catalog", tags=["market-catalog"])

    @router.get("/{category}")
    async def catalog(
        request: Request,
        category: str,
        search: str = "",
        sort: str = "symbol",
        direction: str = "none",
        offset: int = 0,
        limit: int = Query(100, ge=1, le=500),
        revision: str | None = None,
        contract_type: str | None = None,
        settle_coin: str | None = None,
    ):
        try:
            data = service.query(
                category,
                search=search,
                sort=sort,
                direction=direction,
                offset=offset,
                limit=limit,
                revision=revision,
                contract_type=contract_type,
                settle_coin=settle_coin,
            )
        except SnapshotConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Freshness/error state participates; unchanged source revision alone is insufficient.
        etag = '"' + hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest() + '"'
        headers = {"ETag": etag, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse({"schema_version": "1.0", "data": data}, headers=headers)

    return router
