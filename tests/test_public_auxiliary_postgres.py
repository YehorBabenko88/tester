"""Real disposable PostgreSQL source/provenance roundtrip; CI only."""
import os
import base64
import hashlib
import json
import unittest
from datetime import datetime, timezone
from urllib.parse import urlencode

import psycopg

from src.ingest.public_auxiliary import ingest_page, fetch_and_ingest, fetch_open_interest_pages
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

    def test_atomic_exact_readback_and_idempotent_replay(self):
        store = Store(DSN)
        stamp = datetime(2026, 11, 13, tzinfo=timezone.utc)
        cases = [
            ("funding", "", {"symbol": "BTCUSDT", "fundingRate": "-0.000012345678",
                              "fundingRateTimestamp": "1794500000000"}, "-0.000012345678"),
            ("open_interest", "5min", {"symbol": "BTCUSDT",
                                      "openInterest": "123456789.000001",
                                      "timestamp": "1794500000000"}, "123456789.000001"),
            ("trades", "", {"symbol": "BTCUSDT", "execId": "ci-aux-unique-001",
                             "time": "1794500000000", "price": "80000.00000001",
                             "size": "0.00000001", "side": "Buy"}, "80000.00000001"),
        ]
        for kind, interval, row, expected in cases:
            with self.subTest(kind=kind):
                kwargs = dict(store=store, kind=kind, category="linear", symbol="BTCUSDT",
                              response={"retCode": 0, "result": {"list": [row]}},
                              fetched_at=stamp,
                              url="https://api.bybit.com/v5/market/" + kind,
                              run_id="ci-aux-" + kind, job_key="ci-aux-" + kind,
                              evidence_ref="ci-page-" + kind,
                              start_ms=1794499999999, end_ms=1794500000001,
                              request_params={"intervalTime": interval} if interval else {})
                ingest_page(**kwargs)
                ingest_page(**kwargs)
                rows = store.read(kind, "bybit", "linear", "BTCUSDT",
                                  kwargs["start_ms"], kwargs["end_ms"], interval,
                                  limit=100, compact_provenance=True)
                self.assertEqual(len(rows), 1)
                field = "price" if kind == "trades" else "value"
                self.assertEqual(rows[0]["payload"][field], expected)
                self.assertEqual(rows[0]["provenance"]["authentic"], True)
        with psycopg.connect(DSN) as conn:
            count = conn.execute(
                "SELECT count(*) FROM tester_storage.raw_records WHERE symbol='BTCUSDT'"
            ).fetchone()[0]
            self.assertEqual(count, 3)


    def test_two_real_pg_oi_pages_and_reconnect_unknown_coverage(self):
        """Two original HTTP responses must survive independent PG reconnect."""
        oldest = 1599999700000
        newest = 1600000000000
        pages = [
            {"list": [{"openInterest": "25.500000", "timestamp": str(newest)}],
             "nextPageCursor": "continuation-2"},
            {"list": [{"openInterest": "24.000001", "timestamp": str(oldest)}]},
        ]

        class OriginalPublicHttp:
            def call(self, path, params):
                self.last_response = None
                self.calls = getattr(self, "calls", 0) + 1
                result = pages[1 if params.get("cursor") == "continuation-2" else 0]
                url = "https://api.bybit.com/v5/market" + path + "?" + urlencode(params)
                original = json.dumps({"retCode": 0, "result": result}).encode("utf-8")
                self.last_response = {
                    "url": url, "status_code": 200,
                    "hash_scope": "original_http_body",
                    "sha256": hashlib.sha256(original).hexdigest(),
                    "bytes_base64": base64.b64encode(original).decode("ascii"),
                }
                return result, url

        source = OriginalPublicHttp()
        result = fetch_open_interest_pages(
            Store(DSN), source, category="linear", symbol="ETHUSDT",
            run_id="data72-oi-proof-2pages-v1", job_prefix="data72-oi-2pages",
            start_ms=oldest - 1, end_ms=newest + 1,
            interval="5min", max_pages=2, limit=2,
        )
        self.assertEqual(source.calls, 2)
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["coverage"], "UNKNOWN")

        reopened = Store(DSN)
        rows = reopened.read("open_interest", "bybit", "linear", "ETHUSDT",
                             oldest - 1, newest + 1, "5min",
                             limit=10, compact_provenance=False)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["payload"]["value"] for r in rows],
                         ["24.000001", "25.5"])
        for row in rows:
            proof = row["provenance"]
            self.assertTrue(proof["authentic"])
            self.assertTrue(proof["observations"])
            for obs in proof["observations"]:
                evidence = obs["evidence"]
                self.assertEqual(evidence["coverage_status"], "UNKNOWN")
                self.assertFalse(obs["verified"])
                original = base64.b64decode(evidence["http_bytes_base64"])
                self.assertEqual(hashlib.sha256(original).hexdigest(),
                                 evidence["http_sha256"])
                self.assertEqual(evidence["ingestion_run"], obs["run_id"])
        with reopened.connect() as connection:
            receipts = connection.execute(
                "SELECT evidence, verified FROM tester_storage.pagination_evidence "
                "WHERE job_key LIKE 'data72-oi-2pages:%' ORDER BY job_key"
            ).fetchall()
        self.assertEqual(len(receipts), 2)
        self.assertTrue(all(not r["verified"] for r in receipts))
        self.assertEqual(receipts[0]["evidence"]["pages"][0]["next_cursor"],
                         "continuation-2")
        self.assertEqual(receipts[1]["evidence"]["cursor_in"], "continuation-2")

    def test_one_full_oi_page_never_proves_complete(self):
        result = {"list": [{"openInterest": "10.00", "timestamp": "1600000000000"}],
                  "nextPageCursor": "more-data"}

        class OnePage:
            def call(self, path, params):
                url = "https://api.bybit.com/v5/market" + path + "?" + urlencode(params)
                raw = json.dumps({"retCode": 0, "result": result}).encode()
                self.last_response = {
                    "url": url, "status_code": 200, "hash_scope": "original_http_body",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes_base64": base64.b64encode(raw).decode(),
                }
                return result, url

        observation = fetch_open_interest_pages(
            Store(DSN), OnePage(), category="linear", symbol="SOLUSDT",
            run_id="data72-oi-partial-v1", job_prefix="data72-oi-partial",
            start_ms=1599999999999, end_ms=1600000000001,
            interval="5min", max_pages=1, limit=1,
        )
        self.assertEqual(observation["status"], "PAGE_BUDGET_EXHAUSTED")
        self.assertEqual(observation["coverage"], "UNKNOWN")
        reopened = Store(DSN)
        records = reopened.read("open_interest", "bybit", "linear", "SOLUSDT",
                                1599999999999, 1600000000001, "5min",
                                limit=10, compact_provenance=False)
        self.assertEqual(len(records), 1)
        evidence = records[0]["provenance"]["observations"][0]["evidence"]
        self.assertEqual(evidence["status"], "PARTIAL")
        self.assertEqual(evidence["coverage_status"], "UNKNOWN")
        from src.ingest.execution_contract import read_execution_events
        self.assertEqual(read_execution_events(
            reopened, category="linear", symbol="SOLUSDT",
            start_ms=1599999999999, end_ms=1600000000001, limit=2,
        ), [])

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
        from src.analytics.research_metrics import calculate
        metrics = calculate(research, start_ms=int(timestamp) - 1,
                            end_ms=int(timestamp) + 1, interval_ms=2, tick_size="0.01")
        bar = metrics["bars"][0]
        self.assertEqual(bar["unknown_side_volume"], "0.0075")
        self.assertEqual(bar["buy_volume"], "0")
        self.assertEqual(bar["sell_volume"], "0")
        self.assertEqual(bar["delta_known"], "0")
        self.assertFalse(bar["side_classification_complete"])
        self.assertFalse(bar["delta_complete"])
        self.assertFalse(metrics["admitted_to_f"])
        # Replayed original receipt must remain immutable and read back once.
        receipt2 = fetch_and_ingest(store, UnknownSidePublicSource(), **params)
        self.assertEqual(receipt2["count"], 1)
        self.assertEqual(len(read_execution_events(Store(DSN), **scope)), 1)


if __name__ == "__main__":
    unittest.main()
