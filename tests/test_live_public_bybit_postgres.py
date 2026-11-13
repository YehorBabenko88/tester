"""Live public Bybit V5 → disposable PG16 → durable DATA → RESEARCH.

Run only in explicitly enabled isolated CI job. Public HTTP, no API keys,
no real orders, no permanent machine or existing DB access.
"""
import os
import unittest
from datetime import datetime, timezone

import psycopg

from src.ingest.public_auxiliary import (
    fetch_and_ingest, fetch_funding_pages, fetch_open_interest_pages,
)
from src.ingest.rest import API
from src.ingest.transport import Transport
from src.ingest.execution_contract import read_execution_events, read_research_trades
from src.storage.migrations import migrate
from src.storage.store import Store

DSN = os.environ.get("TESTER_AUX_TEST_DSN")
ALLOWED = os.environ.get("TESTER_AUX_LIVE_BYBIT") == "yes"
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "LINKUSDT", "LTCUSDT", "AVAXUSDT", "BCHUSDT",
]


@unittest.skipUnless(ALLOWED and DSN, "explicit public live CI + disposable PG required")
class LiveBybitPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.ingest.public_auxiliary_runner import isolated_dsn
        isolated_dsn(DSN)
        with psycopg.connect(DSN) as conn:
            migrate(conn)
        cls.store = Store(DSN)
        cls.api = API(Transport(attempts=2, rate=1))

    def test_one_then_ten_public_executions_and_research_readback(self):
        from src.integration.data_research_adapter import read_data_metrics

        results = []
        for index, symbol in enumerate(SYMBOLS):
            name = f"live-public-data72-{index}-{symbol}"
            publication = fetch_and_ingest(
                self.store, self.api, kind="trades",
                category="linear", symbol=symbol,
                run_id=name, job_key=name, limit=10,
            )
            self.assertFalse(publication["verified"])
            self.assertGreater(publication["count"], 0)
            # Reload from a new database connection and verify original HTTP,
            # row, page and source receipt hashes independently of the fetcher.
            reopened = Store(DSN)
            rows = reopened.read(
                "trades", "bybit", "linear", symbol,
                0, 253402300799000, "", limit=100,
                compact_provenance=True,
            )
            timestamps = [r["timestamp_ms"] for r in rows]
            self.assertTrue(timestamps)
            start_ms = min(timestamps) // 60000 * 60000
            end_ms = (max(timestamps) // 60000 + 1) * 60000
            read_scope = dict(
                category="linear", symbol=symbol,
                start_ms=start_ms, end_ms=end_ms, limit=100,
            )
            events = read_execution_events(reopened, **read_scope)
            canonical = read_research_trades(reopened, **read_scope)
            self.assertEqual(len(events), len(canonical))
            self.assertEqual(len(events), publication["count"])
            self.assertTrue(all(e["source_coverage"]["status"] == "UNKNOWN" for e in events))
            self.assertTrue(all(e["publication_ms"] is None for e in events))
            preview = read_data_metrics(
                reopened, **read_scope, interval_ms=60000, tick_size="0.5",
            )
            self.assertEqual(preview["coverage"], "UNKNOWN")
            self.assertEqual(preview["publication_time"], "UNKNOWN")
            self.assertFalse(preview["admitted_to_f"])
            self.assertIsNone(preview["f_input"])
            self.assertEqual(preview["status"], "OBSERVED_UNVERIFIED")
            self.assertEqual(preview["source_verification"],
                             "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS")
            results.append(symbol)
            if index == 0:
                self.assertEqual(results, ["BTCUSDT"])
        self.assertEqual(results, SYMBOLS)
        self.assertEqual(len(set(results)), 10)

    def test_public_funding_and_oi_bounded_pages(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        funding = fetch_funding_pages(
            self.store, self.api, category="linear", symbol="BTCUSDT",
            run_id="live-data72-funding", job_prefix="live-data72-funding",
            start_ms=now_ms - 4 * 86400000, end_ms=now_ms,
            max_pages=2, limit=2,
        )
        interest = fetch_open_interest_pages(
            self.store, self.api, category="linear", symbol="BTCUSDT",
            run_id="live-data72-oi", job_prefix="live-data72-oi",
            start_ms=now_ms - 2 * 3600000, end_ms=now_ms,
            interval="5min", max_pages=2, limit=10,
        )
        for result in (funding, interest):
            self.assertEqual(result["coverage"], "UNKNOWN")
            self.assertLessEqual(len(result["pages"]), 2)
        reopened = Store(DSN)
        funding_rows = reopened.read(
            "funding", "bybit", "linear", "BTCUSDT",
            now_ms - 4 * 86400000, now_ms, "", limit=50,
            compact_provenance=True,
        )
        oi_rows = reopened.read(
            "open_interest", "bybit", "linear", "BTCUSDT",
            now_ms - 2 * 3600000, now_ms, "5min", limit=50,
            compact_provenance=True,
        )
        self.assertGreater(len(funding_rows), 0)
        self.assertGreater(len(oi_rows), 0)
        for record in funding_rows + oi_rows:
            self.assertTrue(record["provenance"]["authentic"])
            self.assertIsNone(record["payload"]["publication_timestamp_ms"])
            self.assertIsInstance(record["payload"]["value"], str)


if __name__ == "__main__":
    unittest.main()
