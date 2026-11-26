"""Real disposable PostgreSQL source/provenance roundtrip; CI only."""
import os
import base64
import hashlib
import json
import unittest
from datetime import datetime, timezone
from urllib.parse import urlencode

import psycopg

from src.ingest.public_auxiliary import fetch_and_ingest
from src.storage.store import DataConflict
from src.storage.migrations import migrate
from src.storage.store import Store

DSN = os.environ.get("TESTER_AUX_TEST_DSN")


@unittest.skipUnless(DSN, "isolated TESTER_AUX_TEST_DSN required")
class AuxiliaryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DSN) as conn:
            migrate(conn)

    def test_store_transaction_fault_after_checkpoint_rolls_back_all_pg_publication(self):
        """E-ROUND4: real disposable PostgreSQL SQL failure after the checkpoint write.

        This proves the shared PostgreSQL Store transaction, distinct from the
        SQLite REST acquisition journal. The fault is raised by PostgreSQL
        itself (division by zero) after all raw/evidence/checkpoint statements
        but before commit. It must preserve the previous committed window.
        """
        from uuid import uuid4
        from psycopg.conninfo import conninfo_to_dict
        from tests.storage.test_models import trade

        config = conninfo_to_dict(DSN)
        if (config.get("host") != "127.0.0.1"
                or config.get("port") != "55479"
                or config.get("dbname") != "tester_aux_roundtrip"):
            self.skipTest("only dedicated disposable CI PostgreSQL 55479 is permitted")
        symbol = "E" + uuid4().hex[:15].upper()
        key = "E-R4-atomic-" + symbol
        start, end = 1700000000000, 1700000060000
        def record(n):
            r = trade(symbol=symbol, timestamp_ms=start + n * 1000)
            r["payload"]["trade_id"] = "exchange-" + str(n)
            return r
        def evidence(count):
            return {
                "venue": "bybit", "category": "linear", "symbol": symbol,
                "dataset": "trades", "interval": "",
                "start_ms": start, "end_ms": end, "status": "EXHAUSTED",
                "pages": [{"params": {"category": "linear", "symbol": symbol},
                           "cursor": None, "next_cursor": None, "count": count}],
            }
        store = Store(DSN)
        store.ingest([record(1)], evidence(1), key, checkpoint={"sequence": 1})

        def snapshot():
            with store.connect() as c:
                counts = {}
                for table in ("raw_records", "record_sources", "source_observations",
                              "pagination_evidence", "coverage_intervals",
                              "ingestion_checkpoints", "download_jobs"):
                    counts[table] = c.execute(
                        "SELECT count(*) AS n FROM tester_storage." + table +
                        " WHERE " + ("job_key" if table in (
                            "pagination_evidence", "ingestion_checkpoints", "download_jobs"
                        ) else "symbol") + " = %s",
                        (key if table in ("pagination_evidence", "ingestion_checkpoints",
                                          "download_jobs") else symbol,),
                    ).fetchone()["n"]
                return counts

        before = snapshot()
        assert store.checkpoint(key) == {"sequence": 1}

        class InjectedSqlFaultStore(Store):
            @staticmethod
            def _audit(c, e, action, detail):
                # The transaction already wrote checkpoint and source rows.
                # This is a real PostgreSQL statement exception, not a mock.
                c.execute("SELECT 1 / 0")

        with self.assertRaises(psycopg.DivisionByZero):
            InjectedSqlFaultStore(DSN).ingest(
                [record(2), record(3)], evidence(2), key,
                checkpoint={"sequence": 2},
            )
        assert snapshot() == before
        assert store.checkpoint(key) == {"sequence": 1}
        rows = store.read("trades", "bybit", "linear", symbol, start, end)
        assert len(rows) == 1
        assert rows[0]["trade_id"] == "exchange-1"

        # A clean retry succeeds without rewriting earlier confirmed data.
        result = store.ingest([record(2), record(3)], evidence(2), key,
                              checkpoint={"sequence": 2})
        assert result["count"] == 2
        assert store.checkpoint(key) == {"sequence": 2}
        assert len(store.read("trades", "bybit", "linear", symbol, start, end)) == 3

    def test_atomic_exact_readback_and_idempotent_replay(self):
        store = Store(DSN)
        row = {"symbol": "BTCUSDT", "execId": "ci-aux-unique-001",
               "time": "1794500000000", "price": "80000.00000001",
               "size": "0.00000001", "side": "Buy"}

        class Replay:
            def call(self, path, params):
                url = "https://api.bybit.com/v5/market" + path + "?" + urlencode(params)
                result = {"list": [row]}
                body = json.dumps({"retCode": 0, "result": result}).encode()
                self.last_response = {
                    "url": url, "status_code": 200, "hash_scope": "original_http_body",
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes_base64": base64.b64encode(body).decode(),
                }
                return result, url

        kwargs = dict(kind="trades", category="linear", symbol="BTCUSDT",
                      run_id="ci-aux-trades", job_key="ci-aux-trades",
                      observed_at=datetime(2026, 11, 13, tzinfo=timezone.utc))
        fetch_and_ingest(store, Replay(), **kwargs)
        fetch_and_ingest(store, Replay(), **kwargs)
        rows = store.read("trades", "bybit", "linear", "BTCUSDT",
                          1794499999999, 1794500000001, "",
                          limit=100, compact_provenance=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payload"]["price"], "80000.00000001")
        self.assertEqual(rows[0]["provenance"]["authentic"], True)

    def test_issue104_bounded_read_attaches_each_evidence_once_and_fails_closed(self):
        from src.ingest.execution_contract import READ_BUDGET
        from src.storage.bounded_read import ReadBudgetExceeded
        self.test_atomic_exact_readback_and_idempotent_replay()
        store = Store(DSN)
        scope = ("trades", "bybit", "linear", "BTCUSDT", 1794499999999, 1794500000001, "")
        rows = store.read_bounded(*scope, limit=100, budget=dict(READ_BUDGET))
        self.assertEqual(len(rows), 1)
        observations = rows[0]["provenance"]["observations"]
        self.assertTrue(observations)
        for observation in observations:
            self.assertEqual(observation["evidence"]["http_hash_scope"], "original_http_body")
            self.assertIsNone(observation["raw_envelope"])
        for key in ("max_evidence_bytes", "max_row_bytes", "max_source_bytes"):
            with self.subTest(key=key), self.assertRaises(ReadBudgetExceeded) as caught:
                store.read_bounded(*scope, limit=100, budget={**READ_BUDGET, key: 1})
            self.assertEqual(caught.exception.budget, key)
        with self.assertRaises(ValueError):
            store.read_bounded(*scope, limit=100, budget={"max_row_bytes": 1})

    def test_conflicting_exchange_exec_id_does_not_overwrite_economics(self):
        api_symbol = "XRPUSDT"
        timestamp = "1600000000000"

        class RecordedSource:
            def __init__(self, price):
                self.result = {"list": [{
                    "symbol": api_symbol, "execId": "data72-exec-conflict-v1",
                    "time": timestamp, "price": price, "size": "2.0",
                    "side": "Sell",
                }]}

            def call(self, path, params):
                url = "https://api.bybit.com/v5/market" + path + "?" + urlencode(params)
                original = json.dumps({"retCode": 0, "result": self.result}).encode()
                self.last_response = {
                    "url": url, "status_code": 200,
                    "hash_scope": "original_http_body",
                    "sha256": hashlib.sha256(original).hexdigest(),
                    "bytes_base64": base64.b64encode(original).decode(),
                }
                return self.result, url

        store = Store(DSN)
        params = dict(kind="trades", category="linear", symbol=api_symbol)
        self.assertEqual(fetch_and_ingest(
            store, RecordedSource("1.250"), run_id="data72-conflict-v1",
            job_key="data72-conflict-first-v1", **params,
        )["count"], 1)
        with self.assertRaises(DataConflict):
            fetch_and_ingest(
                store, RecordedSource("1.260"), run_id="data72-conflict-v2",
                job_key="data72-conflict-second-v1", **params,
            )
        persisted = Store(DSN).read(
            "trades", "bybit", "linear", api_symbol,
            int(timestamp) - 1, int(timestamp) + 1, "",
            limit=10, compact_provenance=True,
        )
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["payload"]["price"], "1.25")
        self.assertEqual(persisted[0]["trade_id"], "data72-exec-conflict-v1")


    def test_unknown_public_taker_side_survives_pg16_without_inventing_delta(self):
        from src.ingest.execution_contract import read_execution_events, read_research_trades
        symbol = "LTCUSDT"
        timestamp = "1600000000000"

        class UnknownSidePublicSource:
            def call(self, path, params):
                self.result = {"list": [{
                    "symbol": symbol, "execId": "data72-unknown-side-pg-v1",
                    "time": timestamp, "price": "123.4500",
                    "size": "0.007500", "side": "Unknown",
                }]}
                url = "https://api.bybit.com/v5/market" + path + "?" + urlencode(params)
                body = json.dumps({"retCode": 0, "result": self.result}).encode()
                self.last_response = {
                    "url": url, "status_code": 200,
                    "hash_scope": "original_http_body",
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes_base64": base64.b64encode(body).decode(),
                }
                return self.result, url

        store = Store(DSN)
        params = dict(kind="trades", category="linear", symbol=symbol,
                      run_id="data72-unknown-side-run-v1",
                      job_key="data72-unknown-side-job-v1")
        receipt = fetch_and_ingest(store, UnknownSidePublicSource(), **params)
        self.assertEqual(receipt["count"], 1)
        self.assertFalse(receipt["verified"])
        reopened = Store(DSN)
        scope = dict(category="linear", symbol=symbol,
                     start_ms=int(timestamp) - 1, end_ms=int(timestamp) + 1,
                     limit=10)
        events = read_execution_events(reopened, **scope)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["taker_side"], "Unknown")
        self.assertEqual(events[0]["quantity"], "0.0075")
        self.assertEqual(events[0]["price"], "123.45")
        self.assertEqual(events[0]["source_coverage"]["status"], "UNKNOWN")
        self.assertIsNone(events[0]["publication_ms"])
        self.assertTrue(events[0]["source_receipts"][0]["http_sha256"])
        research = read_research_trades(reopened, **scope)
        self.assertEqual(research[0]["taker_side"], "Unknown")
        self.assertEqual(research[0]["coverage"]["status"], "UNKNOWN")
        from src.analytics.core import footprint
        observed_footprint = footprint(research, interval="1", price_step="0.01")
        self.assertEqual(observed_footprint[0]["unknown_side_volume"], "0.0075")
        aggregate = observed_footprint[0]
        self.assertEqual(aggregate["buy_volume"], "0")
        self.assertEqual(aggregate["sell_volume"], "0")
        self.assertEqual(aggregate["unknown_side_volume"], "0.0075")
        self.assertIsNone(aggregate["delta"])
        self.assertEqual(aggregate["coverage"]["status"], "UNKNOWN")
        # Replayed original receipt must remain immutable and read back once.
        receipt2 = fetch_and_ingest(store, UnknownSidePublicSource(), **params)
        self.assertEqual(receipt2["count"], 1)
        self.assertEqual(len(read_execution_events(Store(DSN), **scope)), 1)


if __name__ == "__main__":
    unittest.main()
