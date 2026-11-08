"""Opt-in bounded public Bybit candles for a fresh isolated lab.

Reads never ingest or certify historical completeness. Queue publication uses the
job's fenced transaction for B raw records and original A projections together.
"""

import copy
import json
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.v1.validate import validate_record
from src.api.config import load_settings
from src.api.providers import ProviderDataUnavailable, ProviderUnavailable
from src.storage.models import normalize
from src.storage.store import DataConflict, Store

from .storage_api import IntegrationUnavailable, provenance_from_storage

SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "LTCUSDT",
)


def commit_batch(commit, write, mark_conflict, checkpoint, progress):
    try:
        commit(write, checkpoint, progress)
    except DataConflict:
        # The first outer transaction rolled back B's nested finding too. Persist
        # the damage under a fresh valid job fence, then fail the job explicitly.
        commit(mark_conflict, {**checkpoint, "conflict": True}, progress)
        raise


def bound_store(connection, settings):
    class BoundStore(Store):
        @contextmanager
        def connect(self):
            old = connection.row_factory
            connection.row_factory = dict_row
            try:
                with connection.transaction():
                    yield connection
            finally:
                connection.row_factory = old

    return BoundStore(settings.dsn, storage_root=settings.data_dir)


def validate_download(request):
    if (
        request.category != "linear"
        or request.symbol not in SYMBOLS
        or request.source != "bybit"
        or request.kind != "candles"
        or request.interval
        not in ("1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D")
        or not 0 <= request.start_ms < request.end_ms
        or request.end_ms - request.start_ms > 86400000
    ):
        raise ProviderUnavailable(
            "Lab supports ten public linear symbols, candles, at most one day"
        )


def source_wait(stop, deadline, seconds):
    if stop.is_set():
        raise InterruptedError("cancelled")
    if seconds < 0 or seconds > 10 or time.monotonic() + seconds >= deadline:
        raise ProviderUnavailable("Public source budget exhausted")
    if stop.wait(seconds):
        raise InterruptedError("cancelled")


def project_candle(row, original, start, end):
    try:
        value = copy.deepcopy(original)
        validate_record("candle", value)
        provenance = provenance_from_storage(row["provenance"], require_declared_run=True)
        if any(p not in provenance for p in value["provenance"]):
            raise ValueError("Original projection acquisition does not match B linkage")
        if (
            [row[k] for k in ("venue", "category", "symbol", "interval", "timestamp_ms")]
            != [value[k] for k in ("venue", "category", "symbol", "interval", "timestamp_ms")]
            or row["dataset"] != "candles"
            or value["kind"] != "official_trade"
            or value["is_closed"] is not True
            or not start <= value["start_ms"] < value["end_ms"] <= end
        ):
            raise ValueError("Candle identity, closure or bounds differ")
        if any(
            Decimal(row["payload"][k]) != Decimal(value[k])
            for k in ("open", "high", "low", "close", "volume")
        ):
            raise ValueError("Projected values differ from B raw data")
        value["availability"] = dict(
            status="PARTIAL",
            reason="Observed official closed candles; historical coverage not certified",
        )
        value["coverage"] = dict(status="UNKNOWN", start_ms=start, end_ms=end, evidence_refs=[])
        validate_record("candle", value)
        return value
    except (ValueError, KeyError, TypeError, IntegrationUnavailable) as exc:
        raise ProviderDataUnavailable("source_provenance_unavailable") from exc


def envelope(rows, query, provenance=None, cursor=None):
    return dict(
        schema_version="1.0",
        data=rows,
        next_cursor=cursor,
        availability=dict(
            status="PARTIAL" if rows else "UNAVAILABLE",
            reason="Bounded public observations; no historical completeness certificate",
        ),
        coverage=dict(
            status="UNKNOWN", start_ms=query.start_ms, end_ms=query.end_ms, evidence_refs=[]
        ),
        provenance=provenance or [],
    )


class LabReader:
    def __init__(self, store):
        self.store = store

    def read(self, resource, query):
        if resource == "sources":
            rows = (
                [
                    dict(
                        id="bybit",
                        availability="partial",
                        kinds=["candles"],
                        reason="Public linear closed candles, ten symbols, one-day bounded jobs",
                    )
                ]
                if query.category == "linear"
                else []
            )
            return envelope(rows, query)
        if resource == "instruments":
            with self.store.connect() as c:
                rows = c.execute(
                    """SELECT DISTINCT ON (symbol) metadata FROM tester_storage.instrument_metadata
                    WHERE venue=%s AND category=%s AND effective_ms>=%s AND effective_ms<%s
                    AND (%s::text IS NULL OR symbol=%s)
                    ORDER BY symbol,effective_ms DESC LIMIT %s""",
                    (
                        query.venue,
                        query.category,
                        query.start_ms,
                        query.end_ms,
                        query.symbol,
                        query.symbol,
                        query.limit,
                    ),
                ).fetchall()
            values = []
            for row in rows:
                value = copy.deepcopy(row["metadata"]["instrument"])
                if query.symbol is not None and value["symbol"] != query.symbol:
                    continue
                validate_record("instrument", value)
                values.append(value)
            return envelope(values, query, [p for r in values for p in r["provenance"]])
        if resource != "candles" or not query.symbol or query.candle_kind != "official_trade":
            raise ProviderUnavailable("Lab resource is not implemented")
        after = None
        if query.cursor:
            try:
                after = json.loads(query.cursor)
                if (
                    not isinstance(after, list)
                    or len(after) != 2
                    or type(after[0]) is not int
                    or not isinstance(after[1], str)
                ):
                    raise ValueError()
            except (ValueError, TypeError) as exc:
                raise ProviderDataUnavailable("continuation_unavailable") from exc
        rows = self.store.read(
            "candles",
            query.venue,
            query.category,
            query.symbol,
            query.start_ms,
            query.end_ms,
            query.interval,
            limit=query.limit + 1,
            after=after,
        )
        report = self.store.coverage(
            query.venue,
            query.category,
            query.symbol,
            "candles",
            query.interval,
            query.start_ms,
            query.end_ms,
        )
        if report.get("corrupt") or report.get("unavailable"):
            raise ProviderDataUnavailable("storage_integrity_unavailable")
        more = len(rows) > query.limit
        rows = rows[: query.limit]
        with self.store.connect() as c:
            projections = {
                r["record_key"]: r["record"]
                for r in c.execute(
                    "SELECT record_key,record FROM tester_lab.projections WHERE record_key=ANY(%s)",
                    ([r["record_key"] for r in rows],),
                ).fetchall()
            }
        values = [
            project_candle(r, projections.get(r["record_key"]), query.start_ms, query.end_ms)
            for r in rows
        ]
        cursor = (
            json.dumps([rows[-1]["timestamp_ms"], rows[-1]["record_key"]], separators=(",", ":"))
            if more
            else None
        )
        provenance = []
        for value in values:
            for p in value["provenance"]:
                if p not in provenance:
                    provenance.append(p)
        return envelope(values, query, provenance, cursor)


class LabHandler:
    def __init__(self, settings):
        self.settings = settings

    def run(self, request, checkpoint, report, stop, commit):
        validate_download(request)
        from src.ingest.journal import Journal
        from src.ingest.metadata import acquire_instruments
        from src.ingest.rest import API, Backfill
        from src.ingest.transport import Transport

        directory = self.settings.data_dir / "acquisitions" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        journal = Journal(directory / "journal.sqlite")
        try:
            deadline = time.monotonic() + 90
            api = API(
                Transport(
                    attempts=2, rate=2, sleep=lambda delay: source_wait(stop, deadline, delay)
                )
            )
            metadata = acquire_instruments(
                api, journal, "linear", max_pages=1, limit=1, max_rows=10, symbol=request.symbol
            )
            result = Backfill(api, journal).run(
                "linear",
                request.symbol,
                "kline",
                request.interval,
                request.start_ms,
                request.end_ms,
                limit=1000,
                cancel=stop.is_set,
            )
            if stop.is_set() or result["status"] == "paused":
                raise InterruptedError("cancelled")
            journal.export(directory / "handoff.json")
            bundle = json.loads((directory / "handoff.json").read_text(encoding="utf-8"))
            originals = {
                normalize(item["storage_record"])["record_key"]: item["contract_record"]
                for item in bundle["records"]
                if item["contract_kind"] == "candle" and not item["projection_errors"]
            }
            if not originals:
                raise ProviderUnavailable("No closed source candles")
            for index, batch in enumerate(bundle["storage_batches"]):
                if stop.is_set():
                    raise InterruptedError("cancelled")

                def write(connection):
                    store = bound_store(connection, self.settings)
                    result = store.ingest(
                        batch["records"],
                        batch["evidence"],
                        batch["batch_id"],
                        batch["checkpoint"],
                        run_id=batch["ingestion_run"],
                    )
                    if result["verified"]:
                        raise ValueError("Lab cannot promote completeness")
                    for item in metadata["storage_metadata"]:
                        store.save_instrument(**item)
                    for raw in batch["records"]:
                        key = normalize(raw)["record_key"]
                        original = originals.get(key)
                        if original is None:
                            raise ValueError("No original canonical source projection")
                        connection.execute(
                            "INSERT INTO tester_lab.projections(record_key,record) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                            (key, Jsonb(original)),
                        )

                def mark_conflict(connection):
                    bound_store(connection, self.settings).mark_range(
                        batch["evidence"],
                        "corrupt",
                        "conflicting source identity",
                        code="identity_conflict",
                    )

                commit_batch(
                    commit,
                    write,
                    mark_conflict,
                    dict(
                        batches=index + 1,
                        source_records=len(originals),
                        coverage="PARTIAL",
                        source="bybit",
                    ),
                    (index + 1) / len(bundle["storage_batches"]),
                )
        finally:
            journal.close()


def create_reader():
    settings = load_settings()
    if not settings.dsn:
        raise ProviderUnavailable("Dedicated lab database required")
    return LabReader(Store(settings.dsn, storage_root=settings.data_dir))


def create_handler():
    return LabHandler(load_settings())
