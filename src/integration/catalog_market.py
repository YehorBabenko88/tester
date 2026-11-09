"""Explicit full-catalog public candle adapter; never selected by native defaults.

The catalog admits an observed instrument identity, not historical completeness.
Actual metadata and candles still come from the existing verified acquisition,
fenced storage transaction, source projection and measured acknowledgment path.
"""

from src.api.config import load_settings
from src.api.providers import ProviderUnavailable
from src.storage.store import Store

from .lab_market import LabHandler, LabReader, envelope

INTERVALS = frozenset(("1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M"))
CONTRACTS = {
    "linear": {"LinearPerpetual": "perpetual", "LinearFutures": "dated"},
    "inverse": {"InversePerpetual": "perpetual", "InverseFutures": "dated"},
}
STATUSES = frozenset(("Trading", "PreLaunch", "PendingOpen", "Delivering", "Closed"))


def validate_catalog_download(request, lookup):
    """Bound an untrusted job to its exact cached, source-observed futures row."""
    if (
        request.category not in CONTRACTS
        or request.source != "bybit"
        or request.kind != "candles"
        or request.interval not in INTERVALS
        or type(request.start_ms) is not int
        or type(request.end_ms) is not int
        or not 0 <= request.start_ms < request.end_ms
        or request.end_ms - request.start_ms
        > (93 if request.interval in ("W", "M") else 1) * 86400000
    ):
        raise ProviderUnavailable("Public futures candle scope or acquisition budget unavailable")
    item = lookup(request.category, request.symbol)
    if (
        not isinstance(item, dict)
        or item.get("category") != request.category
        or item.get("symbol") != request.symbol
        or item.get("status") not in STATUSES
        or item.get("contract_kind") not in {"perpetual", "dated"}
        or CONTRACTS[request.category].get(item.get("contract_type")) != item["contract_kind"]
    ):
        raise ProviderUnavailable("Observed futures catalog identity is unavailable")


def _enabled(settings):
    if not settings.market_catalog_enabled or not settings.market_catalog_dsn or not settings.dsn:
        raise ProviderUnavailable(
            "Explicit catalog and application database configuration required"
        )


def catalog_lookup(settings):
    from src.api.catalog_store import CatalogStore

    def lookup(category, symbol):
        store = CatalogStore(settings.market_catalog_dsn)
        try:
            snapshot = store.read(category)["snapshot"]
            if not isinstance(snapshot, dict):
                return None
            return next((item for item in snapshot["rows"] if item["symbol"] == symbol), None)
        finally:
            store.close()

    return lookup


class CatalogHandler(LabHandler):
    def __init__(self, settings, *, lookup=None):
        _enabled(settings)
        resolver = lookup or catalog_lookup(settings)
        super().__init__(
            settings, validator=lambda request: validate_catalog_download(request, resolver)
        )


class CatalogReader(LabReader):
    def read(self, resource, query):
        if resource == "sources":
            values = (
                [
                    dict(
                        id="bybit",
                        availability="partial",
                        kinds=["candles"],
                        reason="Public closed futures candles; bounded jobs. Historical coverage remains unknown.",
                    )
                ]
                if query.category in CONTRACTS
                else []
            )
            return envelope(values, query)
        return super().read(resource, query)


def create_reader():
    settings = load_settings()
    _enabled(settings)
    return CatalogReader(Store(settings.dsn, storage_root=settings.data_dir))


def create_handler():
    return CatalogHandler(load_settings())
