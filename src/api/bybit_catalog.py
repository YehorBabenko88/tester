"""Bounded, public Bybit catalog acquisition without trading credentials."""

import json
import ssl
import time
from contextlib import contextmanager

import requests


class _SystemTrustAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        # Python's default context loads the Windows ROOT store. Verification and
        # hostname checking remain required; no insecure retry is permitted.
        kwargs["ssl_context"] = ssl.create_default_context()
        return super().init_poolmanager(*args, **kwargs)


@contextmanager
def _public_get(url, **kwargs):
    with requests.Session() as session:
        session.mount("https://", _SystemTrustAdapter())
        with session.get(url, **kwargs) as response:
            yield response


class AcquisitionError(ValueError):
    def __init__(self, reason, *, loaded=0, pages=0, status_coverage=None):
        super().__init__(reason)
        self.loaded = loaded
        self.pages = pages
        self.status_coverage = status_coverage or {}


class BybitCatalogSource:
    """One bounded public HTTP attempt; scheduler owns retries, not browsers."""

    def __init__(self, *, verify=True):
        if verify is not True and (not isinstance(verify, str) or not verify):
            raise ValueError("TLS verification must use system trust or an explicit CA bundle")
        self.verify = verify

    def call(self, endpoint, params):
        if endpoint not in {"/instruments-info", "/tickers"}:
            raise ValueError("Unsupported public catalog endpoint")
        url = "https://api.bybit.com/v5/market" + endpoint
        with _public_get(
            url, params=params, timeout=(5, 15), stream=True, verify=self.verify
        ) as response:
            if response.status_code == 429:
                raise AcquisitionError("rate_limited")
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if len(content) + len(chunk) > 8_000_000:
                    raise AcquisitionError("response_budget")
                content.extend(chunk)
            body = json.loads(content)
            if not isinstance(body, dict):
                raise AcquisitionError("invalid_response")
            if body.get("retCode") == 10006:
                raise AcquisitionError("rate_limited")
            if body.get("retCode") != 0 or not isinstance(body.get("result"), dict):
                raise AcquisitionError("invalid_response")
            result = dict(body["result"])
            result["_source_time_ms"] = body.get("time")
            return result, response.url


STATUSES = ("Trading", "PreLaunch", "PendingOpen", "Delivering", "Closed")
CONTRACT_KINDS = {
    "LinearPerpetual": "perpetual",
    "InversePerpetual": "perpetual",
    "LinearFutures": "dated",
    "InverseFutures": "dated",
}


def acquire_instruments(
    source,
    category,
    *,
    statuses=STATUSES,
    max_pages=100,
    max_rows=100000,
    cancel=None,
    clock=time.time,
):
    """Exhaust each requested status; never turn partial acquisition into a snapshot.

    EXHAUSTED describes this endpoint's current status scope, not historical delisting coverage.
    Budgets are aggregate across all statuses, and retries belong to the scheduler.
    """
    if category not in {"linear", "inverse"}:
        raise ValueError("Unsupported category")
    statuses = tuple(statuses)
    if (
        not statuses
        or len(set(statuses)) != len(statuses)
        or any(s not in STATUSES for s in statuses)
    ):
        raise ValueError("Unsupported statuses")
    if not 1 <= max_pages <= 100 or not 1 <= max_rows <= 100000:
        raise ValueError("Invalid acquisition budget")
    rows, pages, coverage = {}, 0, {}
    try:
        for status in statuses:
            cursor, seen, status_pages, status_loaded = None, set(), 0, 0
            observed_statuses = {}
            coverage[status] = {"completeness": "UNKNOWN", "pages": 0, "loaded": 0}
            while True:
                if cancel is not None and cancel.is_set():
                    raise AcquisitionError("cancelled")
                if pages >= max_pages:
                    raise AcquisitionError("page_budget")
                params = {"category": category, "status": status, "limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                result, url = source.call("/instruments-info", params)
                batch = result.get("list")
                empty_scope = (
                    result.get("category") == ""
                    and batch == []
                    and result.get("nextPageCursor") == ""
                )
                if (
                    (result.get("category") != category and not empty_scope)
                    or not isinstance(batch, list)
                    or len(batch) > 1000
                ):
                    raise AcquisitionError("invalid_response")
                pages += 1
                status_pages += 1
                observed = clock()
                for raw in batch:
                    if not isinstance(raw, dict) or not isinstance(raw.get("symbol"), str):
                        raise AcquisitionError("invalid_instrument")
                    symbol = raw["symbol"]
                    if not symbol or len(symbol) > 100 or raw.get("status") not in STATUSES:
                        raise AcquisitionError("invalid_instrument")
                    if symbol in rows:
                        raise AcquisitionError("duplicate_instrument")
                    rows[symbol] = {
                        "symbol": symbol,
                        "category": category,
                        "status": raw["status"],
                        "contract_type": raw.get("contractType"),
                        "contract_kind": CONTRACT_KINDS.get(raw.get("contractType"), "unknown"),
                        "settle_coin": raw.get("settleCoin"),
                        "base_coin": raw.get("baseCoin"),
                        "quote_coin": raw.get("quoteCoin"),
                        "source": url,
                        "source_observed_at": observed,
                        "source_time_ms": result.get("_source_time_ms"),
                    }
                    status_loaded += 1
                    observed_statuses[raw["status"]] = observed_statuses.get(raw["status"], 0) + 1
                    if len(rows) > max_rows:
                        raise AcquisitionError("row_budget")
                coverage[status] = {
                    "completeness": "PARTIAL",
                    "pages": status_pages,
                    "loaded": status_loaded,
                    "observed_statuses": dict(observed_statuses),
                    "source_time_ms": result.get("_source_time_ms"),
                    "observed_at": observed,
                    "source": url,
                }
                if "nextPageCursor" not in result or not isinstance(result["nextPageCursor"], str):
                    raise AcquisitionError("cursor_missing")
                cursor = result["nextPageCursor"]
                if not cursor:
                    coverage[status]["completeness"] = "EXHAUSTED"
                    break
                if cursor in seen:
                    raise AcquisitionError("repeated_cursor")
                seen.add(cursor)
        if cancel is not None and cancel.is_set():
            raise AcquisitionError("cancelled")
        return {"rows": list(rows.values()), "pages": pages, "status_coverage": coverage}
    except Exception as exc:
        reason = str(exc) if isinstance(exc, AcquisitionError) else "source_failure"
        raise AcquisitionError(
            reason, loaded=len(rows), pages=pages, status_coverage=coverage
        ) from exc
