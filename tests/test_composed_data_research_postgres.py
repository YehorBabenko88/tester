"""Composed DATA #72 + pinned RESEARCH #71 disposable PostgreSQL E2E.

This test requires the test-only RESEARCH checkout in GitHub Actions.
No production database, branch merge, Bybit HTTP request or live trading.
"""
import base64
import hashlib
import json
import os
import unittest
from datetime import datetime, timezone
from urllib.parse import urlencode

import psycopg

from src.ingest.public_auxiliary import fetch_and_ingest
from src.storage.migrations import migrate
from src.storage.store import Store

DSN = os.environ.get("TESTER_AUX_TEST_DSN")
STAMP = 1600000000000


@unittest.skipUnless(DSN, "isolated disposable PostgreSQL required")
class ComposedDataResearchPostgresTests(unittest.TestCase):
    def test_http_postgres_readback_research_metrics(self):
        # Import must fail if exact pinned RESEARCH code was not composed.
        from src.integration.data_research_adapter import (read_data_metrics, require_strategy_lab_f_input)

        with psycopg.connect(DSN) as connection:
            migrate(connection)

        symbol = "BTCUSDT"
        exchange_id = "data72-composed-research-disposable-001"
        raw = {
            "symbol": symbol, "execId": exchange_id, "time": str(STAMP),
            "price": "10234.56000", "size": "0.125000", "side": "Buy",
        }
        result = {"list": [raw]}
        params = {"category": "linear", "symbol": symbol, "limit": 1000}
        url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(params)
        http_body = json.dumps({"retCode": 0, "result": result}).encode("utf-8")

        class RecordedOriginalHttp:
            last_response = {
                "url": url, "status_code": 200,
                "hash_scope": "original_http_body",
                "sha256": hashlib.sha256(http_body).hexdigest(),
                "bytes_base64": base64.b64encode(http_body).decode("ascii"),
            }

            def call(self, path, request_params):
                if path != "/recent-trade" or request_params != params:
                    raise AssertionError("unexpected public source request")
                return result, url

        store = Store(DSN)
        ingestion = dict(
            kind="trades", category="linear", symbol=symbol,
            run_id="data72-composed-research-ci-v1",
            job_key="data72-composed-research-ci-page-v1",
            observed_at=datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc),
        )
        for _ in range(2):
            receipt = fetch_and_ingest(store, RecordedOriginalHttp(), **ingestion)
            self.assertEqual(receipt["count"], 1)
            self.assertIs(receipt["verified"], False)

        # Independently reopen the database. The RESEARCH consumer MUST read
        # original persisted HTTP/page/row proof through DATA's read-only bridge.
        reopened = Store(DSN)
        scope = dict(
            category="linear", symbol=symbol,
            start_ms=STAMP - 1, end_ms=STAMP + 1,
            interval_ms=2, tick_size="0.5", limit=10,
        )
        preview = read_data_metrics(reopened, **scope)
        self.assertEqual(preview["schema_version"], "tester.data-research-preview/2")
        self.assertEqual(preview["data_contract_version"], "tester-public-execution/1")
        self.assertEqual(preview["source_verification"],
                         "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS")
        self.assertEqual(preview["coverage"], "UNKNOWN")
        self.assertIsNone(preview["persisted_dataset_revision"])
        self.assertIsNone(preview["historical_known_at_ms"])
        self.assertEqual(len(preview["observed_sample_fingerprint"]), 64)
        with self.assertRaisesRegex(ValueError, "real_historical_visibility_unknown_f_denied"):
            require_strategy_lab_f_input(preview)
        self.assertEqual(preview["publication_time"], "UNKNOWN")
        self.assertEqual(preview["status"], "OBSERVED_UNVERIFIED")
        self.assertIs(preview["admitted_to_f"], False)
        self.assertIsNone(preview["f_input"])
        self.assertIsNone(preview["g_e_result"])
        bar = preview["metrics"]["bars"][0]
        self.assertEqual(bar["trade_count"], 1)
        self.assertEqual(bar["volume"], "0.125")
        self.assertEqual(bar["delta_known"], "0.125")
        self.assertEqual(bar["cvd_known"], "0.125")
        self.assertEqual(bar["vwap"], "10234.56")
        self.assertEqual(bar["poc"], "10234.5")
        self.assertIs(bar["coverage_certified"], False)

        with self.assertRaisesRegex(ValueError, "late_source_knowledge"):
            read_data_metrics(
                reopened, **scope,
                as_of_ms=STAMP + 60_000,  # before actual HTTP observation
            )
        with self.assertRaisesRegex(ValueError, "requires_pagination"):
            read_data_metrics(reopened, **{**scope, "limit": 1})
        with reopened.connect() as connection:
            count = connection.execute(
                "SELECT count(*) FROM tester_storage.raw_records "
                "WHERE venue='bybit' AND category='linear' "
                "AND symbol=%s AND dataset='trades' AND trade_id=%s",
                (symbol, exchange_id),
            ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
