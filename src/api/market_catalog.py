"""Opt-in Issue 40 catalog foundation; never mounted or started implicitly."""

import asyncio
import hashlib
import json
import random
import threading
import time
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from .bybit_catalog import AcquisitionError, acquire_instruments
from .bybit_catalog import BybitCatalogSource as BybitCatalogSource

CATEGORIES = ("linear", "inverse")
METRICS = {
    "price": "lastPrice",
    "price_change_24h": "price24hPcnt",
    "volume_24h": "volume24h",
    "turnover_24h": "turnover24h",
    "open_interest": "openInterest",
}
NONNEGATIVE_METRICS = frozenset({"price", "volume_24h", "turnover_24h", "open_interest"})
UNAVAILABLE = ("volatility", "price_change_1h", "oi_change_1h", "oi_change_24h", "market_cap")


class SnapshotConflict(ValueError):
    """The caller must restart pagination against the new revision."""


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


def _negative_unsigned(name, value):
    return name in NONNEGATIVE_METRICS and _number(value) is not None and Decimal(value) < 0


def _source_freshness(stamp, now):
    if stamp is None:
        return "UNKNOWN", "source_time_missing"
    if type(stamp) is not int or not 0 < stamp < 2**63:
        return "UNKNOWN", "source_time_invalid"
    if stamp > now * 1000:
        return "UNKNOWN", "source_time_future"
    if now * 1000 - stamp >= 300000:
        return "STALE", "source_time_stale"
    return "OBSERVED", None


def _metric_unit(row, name):
    base, quote = row.get("base_coin"), row.get("quote_coin")
    if name in {"price_change_24h", "price_change_1h", "oi_change_1h", "oi_change_24h"}:
        return "ratio"
    if name == "price":
        return f"{quote}/{base}" if quote and base else "UNKNOWN"
    if name in {"volume_24h", "open_interest"}:
        return (base if row["category"] == "linear" else quote) or "UNKNOWN"
    if name == "turnover_24h":
        return (quote if row["category"] == "linear" else base) or "UNKNOWN"
    return "UNKNOWN"


class CatalogService:
    def __init__(
        self,
        source,
        *,
        clock=time.time,
        monotonic=time.monotonic,
        jitter=lambda: random.uniform(0, 15),
        max_pages=100,
        max_rows=100000,
    ):
        if not 1 <= max_pages <= 100 or not 1 <= max_rows <= 100000:
            raise ValueError("Invalid acquisition budget")
        self.source, self.clock = source, clock
        self.monotonic, self.jitter = monotonic, jitter
        self.max_pages, self.max_rows = max_pages, max_rows
        self._lock = threading.Lock()
        self._refresh = {category: threading.Lock() for category in CATEGORIES}
        self._snapshots, self._attempts = {}, {}
        self._cancel = threading.Event()
        self._wake = None
        self._task = None
        self._event_loop = None
        self._lifecycle = asyncio.Lock()
        self._manual = set()

    def refresh(self, category):
        _category(category)
        if not self._refresh[category].acquire(blocking=False):
            return False
        rows, pages, acquired = {}, 0, None
        stage = "catalog"
        try:
            acquired = acquire_instruments(
                self.source,
                category,
                max_pages=self.max_pages,
                max_rows=self.max_rows,
                cancel=self._cancel,
                clock=self.clock,
            )
            rows = {row["symbol"]: row for row in acquired["rows"]}
            pages = acquired["pages"]
            catalog_observed_at = self.clock()
            stage = "tickers"
            if self._cancel.is_set():
                raise AcquisitionError("cancelled")
            with self._lock:
                previous = deepcopy(self._snapshots.get(category))
            ticker_error = None
            now = self.clock()
            try:
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
                source_stamp = ticker_result.get("_source_time_ms")
                source_state, source_reason = _source_freshness(source_stamp, now)
                source_time_error = source_reason if source_state == "UNKNOWN" else None
                if source_reason in {"source_time_missing", "source_time_invalid"}:
                    source_stamp = (
                        None  # Do not multiply arbitrary malformed source payloads per metric.
                    )
                for symbol, row in rows.items():
                    ticker = indexed.get(symbol, {})
                    row["metrics"] = {}
                    for name, field in METRICS.items():
                        value = _number(ticker.get(field))
                        negative = _negative_unsigned(name, value)
                        if negative:
                            value = None
                        row["metrics"][name] = {
                            "value": value,
                            "state": source_state if value is not None else "UNAVAILABLE",
                            "source": ticker_url,
                            "observed_at": now,
                            "source_time_ms": source_stamp,
                            "reason": "negative_value"
                            if negative
                            else source_reason
                            if value is not None
                            else "not_provided",
                        }
                        if negative:
                            row["metrics"][name]["raw_value"] = ticker[field]
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
            except Exception as exc:
                if self._cancel.is_set():
                    raise AcquisitionError("cancelled") from exc
                ticker_error = str(exc) if isinstance(exc, AcquisitionError) else "source_failure"
                now = self.clock()
                old_rows = {r["symbol"]: r for r in previous["rows"]} if previous else {}
                source_stamp = previous.get("source_time_ms") if previous else None
                source_time_error = previous.get("source_time_error") if previous else None
                for symbol, row in rows.items():
                    old = old_rows.get(symbol)
                    if old and any(
                        old.get(key) != row.get(key)
                        for key in (
                            "category",
                            "contract_type",
                            "base_coin",
                            "quote_coin",
                            "settle_coin",
                        )
                    ):
                        old = None
                    row["metrics"] = {}
                    for name in (*METRICS, *UNAVAILABLE):
                        metric = deepcopy(old["metrics"].get(name)) if old else None
                        if metric is None:
                            metric = {
                                "value": None,
                                "state": "UNAVAILABLE",
                                "source": None,
                                "observed_at": None,
                                "source_time_ms": None,
                                "reason": "ticker_not_observed",
                            }
                        elif metric["value"] is not None:
                            metric["state"] = "STALE"
                            metric["reason"] = "ticker_refresh_failed"
                        row["metrics"][name] = metric
            for row in rows.values():
                for name, metric in row["metrics"].items():
                    metric["unit"] = _metric_unit(row, name)
            catalog_stamps = [
                scope.get("source_time_ms") for scope in acquired["status_coverage"].values()
            ]
            catalog_state = "OBSERVED"
            if not catalog_stamps or any(
                _source_freshness(t, now)[0] != "OBSERVED" for t in catalog_stamps
            ):
                catalog_state = (
                    "UNKNOWN"
                    if not catalog_stamps
                    or any(_source_freshness(t, now)[0] == "UNKNOWN" for t in catalog_stamps)
                    else "STALE"
                )
            old_ticker = previous.get("ticker_source", {}) if previous else {}
            previous_ticker_success = old_ticker.get(
                "last_success", previous.get("last_success") if previous else None
            )
            ticker_source = {
                "state": ("STALE" if previous_ticker_success is not None else "UNAVAILABLE")
                if ticker_error
                else source_state,
                "last_success": previous_ticker_success if ticker_error else now,
                "last_error": ticker_error,
                "source": old_ticker.get("source") if ticker_error else ticker_url,
                "source_time_ms": source_stamp,
                "observed_at": old_ticker.get(
                    "observed_at", previous.get("last_success") if previous else None
                )
                if ticker_error
                else now,
            }
            snapshot = {
                "rows": list(rows.values()),
                "last_success": catalog_observed_at,
                "catalog_source": {
                    "state": catalog_state,
                    "observed_at": catalog_observed_at,
                    "last_success": catalog_observed_at,
                    "last_error": None,
                    "source": "https://api.bybit.com/v5/market/instruments-info",
                    "source_time_ms": min(catalog_stamps)
                    if catalog_stamps and all(type(t) is int and t > 0 for t in catalog_stamps)
                    else None,
                },
                "ticker_source": ticker_source,
                "pages": pages,
                "status_coverage": acquired["status_coverage"],
                "source_time_ms": source_stamp,
                "source_time_error": source_time_error,
            }
            snapshot["revision"] = hashlib.sha256(
                json.dumps(snapshot, sort_keys=True).encode()
            ).hexdigest()
            with self._lock:
                # stop() uses this same lock: publication cannot cross its cancellation barrier.
                if self._cancel.is_set():
                    raise AcquisitionError("cancelled")
                self._snapshots[category] = snapshot
                self._attempts[category] = {
                    "completeness": "EXHAUSTED",
                    "loaded": len(rows),
                    "last_error": ticker_error,
                    "stage": "tickers" if ticker_error else "published",
                    "pages": pages,
                    "status_coverage": acquired["status_coverage"],
                    "at": now,
                }
            return ticker_error is None
        except Exception as exc:
            reason = str(exc) if isinstance(exc, AcquisitionError) else "source_failure"
            with self._lock:
                self._attempts[category] = {
                    "completeness": "PARTIAL"
                    if rows or (isinstance(exc, AcquisitionError) and exc.loaded)
                    else "UNKNOWN",
                    "loaded": exc.loaded
                    if isinstance(exc, AcquisitionError) and exc.loaded
                    else len(rows),
                    "pages": pages
                    if acquired
                    else (exc.pages if isinstance(exc, AcquisitionError) else pages),
                    "status_coverage": acquired["status_coverage"]
                    if acquired
                    else (exc.status_coverage if isinstance(exc, AcquisitionError) else {}),
                    "stage": stage,
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
        contract_kind=None,
        watchlist=None,
        contract_type=None,
        settle_coin=None,
        sort="symbol",
        direction="none",
        offset=0,
        limit=100,
        revision=None,
    ):
        _category(category)
        fields = {"symbol", "category", "status", "contract_type", "contract_kind", "settle_coin"}
        if contract_kind not in {None, "perpetual", "dated", "unknown"}:
            raise ValueError("Unsupported contract kind")
        if status not in {"", "Trading", "PreLaunch", "PendingOpen", "Delivering", "Closed"}:
            raise ValueError("Unsupported status")
        if watchlist is not None:
            if len(watchlist) > 1000 or any(
                not isinstance(item, str)
                or len(item) > 110
                or item.count(":") != 1
                or item.split(":")[0] not in CATEGORIES
                or not item.split(":")[1]
                for item in watchlist
            ):
                raise ValueError("Invalid watchlist")
            watchlist = set(watchlist)
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
        now = self.clock()
        catalog_source = snapshot.get("catalog_source", {}) if snapshot else {}
        source_state = catalog_source.get("state", "UNKNOWN")
        if source_state == "OBSERVED":
            source_state = _source_freshness(catalog_source.get("source_time_ms"), now)[0]
        catalog_failed = bool(attempt["last_error"] and attempt.get("stage") == "catalog")
        if snapshot and (catalog_failed or now - snapshot["last_success"] >= 300):
            source_state = "STALE"
        rows = snapshot["rows"] if snapshot else []
        for row in rows:
            for name, metric in row["metrics"].items():
                # Qualify old persisted facts too; query's snapshot is a deepcopy,
                # so this projection never rewrites the stored observation.
                if _negative_unsigned(name, metric["value"]):
                    metric.update(
                        raw_value=metric["value"],
                        value=None,
                        state="UNAVAILABLE",
                        reason="negative_value",
                    )
                if metric["value"] is not None:
                    state, reason = _source_freshness(metric.get("source_time_ms"), now)
                    if (
                        metric.get("state") == "STALE"
                        and metric.get("reason") == "ticker_refresh_failed"
                    ):
                        state, reason = "STALE", "ticker_refresh_failed"
                    elif metric.get("state") == "UNKNOWN" and metric.get("reason", "").startswith(
                        "source_time_"
                    ):
                        state, reason = "UNKNOWN", metric["reason"]
                    elif attempt["last_error"] and state == "OBSERVED":
                        state, reason = "STALE", "refresh_failed"
                    metric["state"], metric["reason"] = state, reason
        ticker_source = deepcopy(snapshot.get("ticker_source", {})) if snapshot else {}
        if ticker_source.get("state") == "OBSERVED":
            ticker_source["state"] = _source_freshness(ticker_source.get("source_time_ms"), now)[0]
            if attempt["last_error"]:
                ticker_source["state"] = "STALE"
        rows = [
            row
            for row in rows
            if search.upper() in row["symbol"].upper()
            and (not status or row["status"] == status)
            and (not contract_type or row["contract_type"] == contract_type)
            and (not settle_coin or row["settle_coin"] == settle_coin)
            and (not contract_kind or row.get("contract_kind", "unknown") == contract_kind)
            and (not watchlist or row["category"] + ":" + row["symbol"] in watchlist)
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
            "tickers": ticker_source
            or {"state": "UNAVAILABLE", "last_success": None, "last_error": None},
            "catalog": {
                "loaded": len(snapshot["rows"]) if snapshot else 0,
                "expected_total": None,
                "completeness": "EXHAUSTED" if snapshot else "UNKNOWN",
                "state": ("CURRENT" if source_state == "OBSERVED" else source_state)
                if snapshot
                else "UNAVAILABLE",
                "last_success": snapshot["last_success"] if snapshot else None,
                "last_error": attempt["last_error"] if catalog_failed else None,
                "source": catalog_source,
                "scope": "current_statuses",
                "status_coverage": snapshot.get("status_coverage", {}) if snapshot else {},
                "historical_listing_coverage": "UNKNOWN",
            },
        }

    async def start(self):
        async with self._lifecycle:
            if self._task is not None:
                raise RuntimeError("Catalog poller already started")
            with self._lock:
                self._cancel.clear()
            self._manual.clear()
            self._wake = asyncio.Event()
            self._event_loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._poll())

    async def stop(self):
        async with self._lifecycle:
            with self._lock:
                self._cancel.set()
            if self._wake:
                self._wake.set()
            if self._task:
                await self._task  # In-flight public request finishes; never kill a worker.

            # Also await explicit refresh() callers before any subsequent start can clear cancel.
            def drain_refreshes():
                for lock in self._refresh.values():
                    with lock:
                        pass

            await asyncio.to_thread(drain_refreshes)
            self._manual.clear()
            self._task = self._wake = None
            self._event_loop = None

    def request_refresh(self, category):
        _category(category)
        if self._wake is None:
            raise RuntimeError("Catalog poller not running")
        self._manual.add(category)
        self._event_loop.call_soon_threadsafe(self._wake.set)

    async def _poll(self):
        self._event_loop = asyncio.get_running_loop()
        due = dict.fromkeys(CATEGORIES, 0.0)
        eligible = dict.fromkeys(CATEGORIES, 0.0)
        failures = dict.fromkeys(CATEGORIES, 0)
        while not self._cancel.is_set():
            self._wake.clear()
            for category in CATEGORIES:
                if self._cancel.is_set():
                    return
                now = self.monotonic()
                manual = category in self._manual
                # A manual refresh coalesces and respects a 30-second source budget.
                if now >= due[category] or (manual and now >= eligible[category]):
                    self._manual.discard(category)
                    ok = await asyncio.to_thread(self.refresh, category)
                    failures[category] = 0 if ok else min(3, failures[category] + 1)
                    finished = self.monotonic()
                    delay = min(1800, 300 * 2 ** failures[category])
                    due[category] = finished + delay + self.jitter()
                    eligible[category] = finished + (30 if ok else delay)
            deadlines = [
                min(due[category], eligible[category])
                if category in self._manual
                else due[category]
                for category in CATEGORIES
            ]
            timeout = max(0.1, min(deadlines) - self.monotonic())
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
        status: str = "Trading",
        contract_kind: str | None = None,
        watchlist: list[str] | None = Query(None),
    ):
        try:
            data = await asyncio.to_thread(
                service.query,
                category,
                search=search,
                sort=sort,
                direction=direction,
                offset=offset,
                limit=limit,
                revision=revision,
                contract_type=contract_type,
                settle_coin=settle_coin,
                status=status,
                contract_kind=contract_kind,
                watchlist=watchlist,
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

    @router.post("/{category}/refresh", status_code=202)
    async def refresh(category: str):
        try:
            await asyncio.to_thread(service.request_refresh, category)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, "Catalog scheduler is unavailable") from exc
        return {"schema_version": "1.0", "data": {"category": category, "queued": True}}

    return router
