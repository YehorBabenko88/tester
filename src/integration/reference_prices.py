"""Explicit mark/index source profile; existing trade-candle v1.0 stays unchanged.

No invented volume, funding/OI/trades, historical completeness or analytics.
Acquisition, original HTTP receipts and atomic fenced publication remain A/B/E.
"""

import copy
import json
from decimal import Decimal
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, RootModel, model_validator

from contracts.v1.validate import validate_record
from contracts.v1_1.price_candle import _schema as price_schema
from contracts.v1_1.price_candle import validate_price_candle
from src.api.config import load_settings
from src.api.financial_query import HttpTime
from src.api.models import Query
from src.api.providers import ProviderDataUnavailable, ProviderUnavailable
from src.api.scope import CursorCodec, CursorError
from src.ingest.rest import next_time
from src.storage.models import normalize
from src.storage.store import Store

from .catalog_market import CatalogHandler, CatalogReader, catalog_lookup, validate_catalog_download
from .lab_market import envelope
from .storage_api import IntegrationUnavailable, provenance_from_storage

KINDS = {"mark": "official_mark", "index": "official_index"}


def _reference_sources(provenance, row):
    if not provenance:
        raise ValueError("Original price source required")
    for source in provenance:
        url = urlsplit(source["source_ref"])
        params = parse_qs(url.query)
        if (
            source["source_kind"] != "bybit_rest"
            or url.scheme != "https"
            or url.netloc != "api.bybit.com"
            or url.fragment
            or url.path
            != "/v5/market/"
            + ("mark-price-kline" if row["dataset"] == "mark" else "index-price-kline")
            or any(params.get(k) != [row[k]] for k in ("category", "symbol", "interval"))
            or source["timestamp_precision"] != "ms"
            or source["source_timestamp"] != str(row["timestamp_ms"])
        ):
            raise ValueError("Reference endpoint/scope/original precision differs")


class PriceRecord(RootModel[dict]):
    @model_validator(mode="after")
    def validate_source_price(self):
        validate_price_candle(self.root)
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = copy.deepcopy(price_schema())
        definitions = schema.pop("$defs")

        def expand(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    return expand(definitions[value["$ref"].rsplit("/", 1)[1]])
                return {k: expand(v) for k, v in value.items()}
            if isinstance(value, list):
                return [expand(v) for v in value]
            return value

        return expand(schema)


class PriceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.1"]
    data: list[PriceRecord]
    next_cursor: str | None
    availability: dict
    coverage: dict
    provenance: list[dict]

    @model_validator(mode="after")
    def qualify_metadata(self):
        validate_record("availability", self.availability)
        validate_record("coverage", self.coverage)
        for value in self.provenance:
            validate_record("provenance", value)
        if self.coverage["status"] != "UNKNOWN":
            raise ValueError("Reference-price profile cannot promote history")
        if self.availability["status"] != ("PARTIAL" if self.data else "UNAVAILABLE"):
            raise ValueError("Reference-price availability must describe observed data only")
        return self


def validate_reference_download(request, lookup):
    if request.kind not in {"candles", *KINDS}:
        raise ProviderUnavailable("Reference source profile supports candles/mark/index only")
    # Exact existing identity, category, interval and one-day/93-day budgets.
    validate_catalog_download(request.model_copy(update={"kind": "candles"}), lookup)


def original_price_projection(item):
    raw, stored = item["raw_record"], item["storage_record"]
    if (
        not stored
        or stored["dataset"] not in KINDS
        or item.get("quarantined")
        or item["availability"]["status"] not in {"AVAILABLE", "PARTIAL"}
    ):
        raise ValueError("Missing unquarantined mark/index storage source")
    _reference_sources(item["provenance"], stored)
    end = next_time(raw["timestamp_ms"], raw["interval"])
    if (
        raw.get("is_closed") is not True
        or type(raw.get("closure_server_ms")) is not int
        or raw["closure_server_ms"] < end
        or raw["dataset"] != stored["dataset"]
    ):
        raise ValueError("Original exchange-time closure evidence required")
    value = {k: stored[k] for k in ("venue", "category", "symbol", "timestamp_ms", "interval")}
    value.update(
        schema_version="1.1",
        start_ms=raw["timestamp_ms"],
        end_ms=end,
        kind=KINDS[stored["dataset"]],
        is_closed=True,
        **{k: stored["payload"][k] for k in ("open", "high", "low", "close")},
        availability=dict(
            status="PARTIAL", reason="Observed closed reference price; history not certified"
        ),
        coverage={**copy.deepcopy(item["coverage"]), "status": "UNKNOWN"},
        provenance=copy.deepcopy(item["provenance"]),
    )
    return validate_price_candle(value)


def project_price_candle(row, original, start, end):
    try:
        value = copy.deepcopy(original)
        validate_price_candle(value)
        _reference_sources(value["provenance"], row)
        provenance = provenance_from_storage(row["provenance"], require_declared_run=True)
        if (
            row["dataset"] not in KINDS
            or value["kind"] != KINDS[row["dataset"]]
            or value["availability"]["status"] not in {"AVAILABLE", "PARTIAL"}
            or value["is_closed"] is not True
            or not start <= value["start_ms"] < value["end_ms"] <= end
            or any(
                row[k] != value[k]
                for k in ("venue", "category", "symbol", "interval", "timestamp_ms")
            )
            or not value["provenance"]
            or any(p not in provenance for p in value["provenance"])
            or any(
                Decimal(row["payload"][k]) != Decimal(value[k])
                for k in ("open", "high", "low", "close")
            )
        ):
            raise ValueError("Reference projection differs from canonical source")
        value["availability"] = dict(
            status="PARTIAL", reason="Observed closed reference price; history not certified"
        )
        value["coverage"] = dict(
            status="UNKNOWN",
            start_ms=start,
            end_ms=end,
            evidence_refs=value["coverage"]["evidence_refs"],
        )
        return validate_price_candle(value)
    except (ValueError, KeyError, TypeError, IntegrationUnavailable) as error:
        raise ProviderDataUnavailable("source_provenance_unavailable") from error


class ReferencePriceHandler(CatalogHandler):
    def __init__(self, settings, *, lookup=None):
        super().__init__(settings, lookup=lookup)
        resolver = lookup or catalog_lookup(settings)
        self.validator = lambda request: validate_reference_download(request, resolver)

    def _acquisition_kind(self, request):
        return request.kind if request.kind in KINDS else super()._acquisition_kind(request)

    def _originals(self, bundle):
        originals = super()._originals(bundle)
        for item in bundle["records"]:
            stored = item.get("storage_record")
            if stored and stored["dataset"] in KINDS:
                originals[normalize(stored)["record_key"]] = original_price_projection(item)
        return originals

    def _verify_batch(self, store, connection, batch):
        evidence = batch["evidence"]
        if evidence["dataset"] not in KINDS:
            return super()._verify_batch(store, connection, batch)
        expected = {r["record_key"]: r for r in map(normalize, batch["records"])}
        rows = store.read(
            evidence["dataset"],
            evidence["venue"],
            evidence["category"],
            evidence["symbol"],
            evidence["start_ms"],
            evidence["end_ms"],
            evidence["interval"],
            limit=10000,
            compact_provenance=True,
        )
        rows = {r["record_key"]: r for r in rows if r["record_key"] in expected}
        projections = dict(
            connection.execute(
                "SELECT record_key,record FROM tester_lab.projections WHERE record_key=ANY(%s)",
                (list(expected),),
            ).fetchall()
        )
        if rows.keys() != expected.keys() or projections.keys() != expected.keys():
            raise ValueError("Published price records missing")
        for key, raw in expected.items():
            if rows[key]["content_hash"] != raw["content_hash"]:
                raise ValueError("Published price values differ")
            project_price_candle(
                rows[key], projections[key], evidence["start_ms"], evidence["end_ms"]
            )
        return {key: raw["content_hash"] for key, raw in expected.items()}


class ReferencePriceReader(CatalogReader):
    def read(self, resource, query):
        if resource == "sources":
            value = super().read(resource, query)
            for source in value["data"]:
                source.update(
                    kinds=["candles", "mark", "index"],
                    price_candle_version="1.1",
                    price_candle_path="/api/price-candles",
                    not_integrated=[
                        "funding",
                        "open_interest",
                        "trades",
                        "analytics",
                        "D_price_panel",
                    ],
                )
            return value
        if resource != "price-candles":
            return super().read(resource, query)
        reverse = {v: k for k, v in KINDS.items()}
        if (
            query.candle_kind not in reverse
            or not query.symbol
            or query.category not in {"linear", "inverse"}
        ):
            raise ProviderUnavailable("Only explicit mark/index price-only data is supported")
        from .catalog_market import INTERVALS

        if query.interval not in INTERVALS or query.source_interval not in {None, query.interval}:
            raise ProviderUnavailable("Reference prices require original source cadence")
        after = json.loads(query.cursor) if query.cursor else None
        page = self.store.read_page(
            reverse[query.candle_kind],
            query.venue,
            query.category,
            query.symbol,
            query.start_ms,
            query.end_ms,
            query.interval,
            limit=query.limit,
            after=after,
            max_archive_bytes=0,
        )
        expected_scope = dict(
            dataset=reverse[query.candle_kind],
            venue=query.venue,
            category=query.category,
            symbol=query.symbol,
            interval=query.interval,
        )
        if page["outcome"] != "OK" or page["scope"] != expected_scope:
            raise ProviderDataUnavailable("storage_contract_unavailable")
        quality = page["coverage"].get("intervals", {})
        if quality.get("corrupt") or quality.get("unavailable"):
            raise ProviderDataUnavailable("storage_integrity_unavailable")
        rows = copy.deepcopy(page["rows"])
        with self.store.connect() as connection:
            projections = {
                r["record_key"]: r["record"]
                for r in connection.execute(
                    "SELECT record_key,CASE WHEN octet_length(record::text)<=65536 THEN record "
                    "ELSE NULL END AS record FROM tester_lab.projections WHERE record_key=ANY(%s)",
                    ([r["record_key"] for r in rows],),
                ).fetchall()
            }
        for row in rows:
            observations = [
                copy.deepcopy(o)
                for o in page["observations"]
                if o["record_key"] == row["record_key"]
            ]
            for o in observations:
                source = o.get("source")
                o["source_ingestion_run"] = (
                    source.get("ingestion_run") if isinstance(source, dict) else None
                )
            row["provenance"] = dict(
                observations=observations,
                unlinked_sources=[],
                authentic=bool(observations)
                and all(o["authentic_run"] is True for o in observations),
            )
        values = [
            project_price_candle(r, projections.get(r["record_key"]), query.start_ms, query.end_ms)
            for r in rows
        ]
        value = envelope(
            values,
            query,
            [p for r in values for p in r["provenance"]],
            json.dumps(page["next_cursor"], separators=(",", ":")) if page["next_cursor"] else None,
        )
        value["schema_version"] = "1.1"
        return value


def create_price_router(reader):
    """Explicit v1.1 endpoint; no mutation of the existing v1.0 candle endpoint."""
    router, codec = APIRouter(), CursorCodec()

    @router.get("/api/price-candles", response_model=PriceEnvelope)
    def prices(
        category: Literal["linear", "inverse"],
        symbol: str,
        start_ms: HttpTime,
        end_ms: HttpTime,
        candle_kind: Literal["official_mark", "official_index"],
        interval: str = "1",
        limit: int = 100,
        cursor: str | None = None,
        schema_version: Literal["1.1"] = "1.1",
    ):
        try:
            query = Query(
                category=category,
                symbol=symbol,
                start_ms=start_ms,
                end_ms=end_ms,
                candle_kind=candle_kind,
                interval=interval,
                limit=limit,
                cursor=cursor,
            )
            raw_cursor = codec.decode("price-candles/1.1", query)
        except (ValueError, CursorError) as error:
            raise HTTPException(422, "Invalid price-only request or continuation") from error
        try:
            value = reader.read("price-candles", query.model_copy(update={"cursor": raw_cursor}))
            if (
                value["schema_version"] != "1.1"
                or value["coverage"]["status"] != "UNKNOWN"
                or (value["coverage"]["start_ms"], value["coverage"]["end_ms"])
                != (start_ms, end_ms)
                or len(value["data"]) > limit
            ):
                raise ValueError("Invalid price response scope/version")
            for row in value["data"]:
                validate_price_candle(row)
                if (
                    row["category"] != category
                    or row["symbol"] != symbol
                    or row["interval"] != interval
                    or row["kind"] != candle_kind
                    or not start_ms <= row["start_ms"] < row["end_ms"] <= end_ms
                ):
                    raise ValueError("Invalid price response binding")
            value["next_cursor"] = codec.encode("price-candles/1.1", query, value["next_cursor"])
            return PriceEnvelope.model_validate(value)
        except ValueError as error:
            raise ProviderDataUnavailable("storage_contract_unavailable") from error

    return router


def create_reader():
    settings = load_settings()
    from .catalog_market import _enabled

    _enabled(settings)
    return ReferencePriceReader(Store(settings.dsn, storage_root=settings.data_dir))


def create_handler():
    return ReferencePriceHandler(load_settings())
