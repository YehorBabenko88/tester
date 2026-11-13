"""Actual PostgreSQL DATA-to-RESEARCH roundtrip; isolated CI database ONLY."""
import base64
import hashlib
import json
import os
import unittest
from datetime import datetime, timezone
from urllib.parse import urlencode

import psycopg
from psycopg.types.json import Jsonb

from src.analytics.core import validate_trade
from src.ingest.execution_contract import (ExecutionContractError, read_execution_events, read_research_trades)
from src.ingest.public_auxiliary import fetch_and_ingest
from src.storage.migrations import migrate
from src.storage.store import Store

DSN = os.environ.get("TESTER_AUX_TEST_DSN")
STAMP = 1600000000000


@unittest.skipUnless(DSN, "dedicated disposable PostgreSQL unavailable")
class ExecutionBridgePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DSN) as connection:
            migrate(connection)

    def test_original_http_to_pg_reconnect_to_research(self):
        row = {
            "symbol": "BTCUSDT",
            "execId": "data72-bridge-disposable-ci-v1",
            "time": str(STAMP),
            "price": "10234.56000",
            "size": "0.125000",
            "side": "Buy",
        }
        result = {"list": [row]}
        url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(
            {"category": "linear", "symbol": "BTCUSDT", "limit": 1000}
        )
        body = json.dumps({"retCode": 0, "result": result}).encode("utf-8")

        class OriginalHttpReplay:
            last_response = {
                "url": url,
                "status_code": 200,
                "hash_scope": "original_http_body",
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes_base64": base64.b64encode(body).decode("ascii"),
            }

            def call(self, path, params):
                if path != "/recent-trade" or params != {
                    "category": "linear", "symbol": "BTCUSDT", "limit": 1000
                }:
                    raise AssertionError("Unexpected public HTTP request")
                return result, url

        store = Store(DSN)
        kwargs = dict(
            kind="trades",
            category="linear",
            symbol="BTCUSDT",
            run_id="data72-disposable-bridge-v1",
            job_key="data72-bridge-replay-v1",
            observed_at=datetime(2026, 11, 13, 20, tzinfo=timezone.utc),
        )
        for _ in range(2):
            receipt = fetch_and_ingest(store, OriginalHttpReplay(), **kwargs)
            self.assertFalse(receipt["verified"])

        # A new DB connection must independently reconstruct the complete
        # original HTTP/page/row proof and cannot infer market completeness.
        reopened = Store(DSN)
        scope = dict(
            category="linear", symbol="BTCUSDT",
            start_ms=STAMP - 1, end_ms=STAMP + 1, limit=10
        )
        events = read_execution_events(reopened, **scope)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["quantity"], "0.125")
        self.assertEqual(event["price"], "10234.56")
        self.assertEqual(event["taker_side"], "Buy")
        self.assertEqual(event["source_coverage"]["status"], "UNKNOWN")
        self.assertIsNone(event["publication_ms"])
        self.assertTrue(event["source_receipts"][0]["http_sha256"])
        canonical = read_research_trades(reopened, **scope)
        self.assertEqual(len(canonical), 1)
        validate_trade(canonical[0])
        self.assertEqual(canonical[0]["availability"]["status"], "UNVERIFIED")
        self.assertEqual(canonical[0]["coverage"]["status"], "UNKNOWN")
        with reopened.connect() as connection:
            count = connection.execute(
                "SELECT count(*) AS record_count FROM tester_storage.raw_records "
                "WHERE venue='bybit' AND category='linear' "
                "AND symbol='BTCUSDT' AND dataset='trades' "
                "AND trade_id='data72-bridge-disposable-ci-v1'"
            ).fetchone()["record_count"]
        self.assertEqual(count, 1)

    def test_tampered_persisted_page_receipt_fails_after_reconnect(self):
        # Uses only the disposable CI database. Restore original evidence even
        # when the fail-closed assertion itself raises unexpectedly.
        self.test_original_http_to_pg_reconnect_to_research()
        job = "data72-bridge-replay-v1"
        with psycopg.connect(DSN) as connection:
            original = connection.execute(
                "SELECT evidence FROM tester_storage.pagination_evidence "
                "WHERE job_key=%s", (job,),
            ).fetchone()[0]
            damaged = {**original, "http_sha256": "0" * 64}
            connection.execute(
                "UPDATE tester_storage.pagination_evidence "
                "SET evidence=%s WHERE job_key=%s",
                (Jsonb(damaged), job),
            )
        try:
            reopened = Store(DSN)
            with self.assertRaisesRegex(ExecutionContractError, "checksum"):
                read_execution_events(
                    reopened, category="linear", symbol="BTCUSDT",
                    start_ms=STAMP - 1, end_ms=STAMP + 1, limit=10,
                )
        finally:
            with psycopg.connect(DSN) as connection:
                connection.execute(
                    "UPDATE tester_storage.pagination_evidence "
                    "SET evidence=%s WHERE job_key=%s",
                    (Jsonb(original), job),
                )



if __name__ == "__main__":
    unittest.main()
