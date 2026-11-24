"""The API candle DTO uses one database snapshot for all linked storage facts."""

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from src.api.models import Query
from src.api.providers import ProviderDataUnavailable
from src.integration import lab_market
from src.storage.migrations import migrate
from src.storage.models import normalize
from src.storage.store import Store


def test_actual_candle_read_is_one_snapshot_across_concurrent_corruption(monkeypatch):
    admin = os.getenv("TESTER_TEST_ADMIN_DSN")
    if not admin:
        pytest.skip("Explicit disposable TESTER_TEST_ADMIN_DSN required")
    database = "tester_snapshot_" + uuid4().hex[:16]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    dsn = make_conninfo(admin, dbname=database)
    try:
        with psycopg.connect(dsn) as conn:
            migrate(conn)
            conn.execute("CREATE SCHEMA tester_lab")
            conn.execute(
                "CREATE TABLE tester_lab.projections(record_key text PRIMARY KEY REFERENCES tester_storage.raw_records(record_key),record jsonb NOT NULL)"
            )
        raw = dict(
            schema_version="1.0",
            venue="bybit",
            category="linear",
            symbol="BTCUSDT",
            dataset="candles",
            interval="1",
            timestamp_ms=60000,
            payload=dict(open="1", high="2", low="1", close="2", volume="3"),
            source=dict(
                source_kind="rest",
                url="https://api.bybit.com/v5/market/kline",
                fetched_at="2026-11-08T00:00:00+00:00",
                sha256="a" * 64,
                ingestion_run="snapshot-fixture",
            ),
        )
        evidence = dict(
            venue="bybit",
            category="linear",
            symbol="BTCUSDT",
            dataset="candles",
            interval="1",
            start_ms=60000,
            end_ms=120000,
            status="UNVERIFIED",
            pages=[dict(params={}, cursor=None, next_cursor=None, count=1)],
            available_range=None,
        )
        store = Store(dsn)
        store.ingest([raw], evidence, "snapshot-fixture", run_id="snapshot-fixture")
        key = normalize(raw)["record_key"]
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "INSERT INTO tester_lab.projections VALUES(%s,%s)", (key, Jsonb({"view": "before"}))
            )
        original_read, changed = Store.read, []

        def read_then_concurrent_commit(self, *args, **kwargs):
            rows = original_read(self, *args, **kwargs)
            if not changed:
                changed.append(True)
                store.mark_range(evidence, "corrupt", "concurrent snapshot regression")
                with psycopg.connect(dsn) as conn:
                    conn.execute(
                        "UPDATE tester_lab.projections SET record=%s WHERE record_key=%s",
                        (Jsonb({"view": "after"}), key),
                    )
            return rows

        monkeypatch.setattr(Store, "read", read_then_concurrent_commit)
        monkeypatch.setattr(
            lab_market,
            "project_candle",
            lambda row, original, *a: {"view": original["view"], "provenance": []},
        )
        query = Query(symbol="BTCUSDT", interval="1", start_ms=60000, end_ms=120000)
        reader = lab_market.LabReader(store)
        assert reader.read("candles", query)["data"] == [{"view": "before", "provenance": []}]
        with pytest.raises(ProviderDataUnavailable):
            reader.read("candles", query)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))

