"""Real disposable PostgreSQL source/provenance roundtrip; CI only."""
import os
import unittest
from datetime import datetime, timezone

import psycopg

from src.ingest.public_auxiliary import ingest_page
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


if __name__ == "__main__":
    unittest.main()
