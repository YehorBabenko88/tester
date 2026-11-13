"""Offline source-contract regression tests. No network or PC4 access."""
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

from src.ingest.public_auxiliary import decode, ingest_page, request

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
BASE = dict(fetched_at=NOW, url="https://api.bybit.com/v5/market/recent-trade",
            ingestion_run="run-test", evidence_ref="page-test")


def response(row):
    return {"retCode": 0, "result": {"list": [row]}}


class PublicAuxiliaryTests(unittest.TestCase):
    def test_funding_exact_negative_and_original_event_time(self):
        rows = decode("funding", "linear", "BTCUSDT",
                      response({"symbol": "BTCUSDT", "fundingRate": "-0.0000123400",
                                "fundingRateTimestamp": "1794560000000"}), **BASE)
        self.assertEqual(rows[0]["payload"]["value"], "-0.00001234")
        self.assertEqual(Decimal(rows[0]["payload"]["value"]), Decimal("-0.0000123400"))
        self.assertEqual(rows[0]["timestamp_ms"], 1794560000000)
        self.assertIsNone(rows[0]["payload"]["publication_timestamp_ms"])

    def test_oi_exact_decimal_and_observation_time(self):
        rows = decode("open_interest", "linear", "BTCUSDT",
                      response({"symbol": "BTCUSDT", "openInterest": "123456789.000001",
                                "timestamp": "1794560000000"}), **BASE)
        self.assertEqual(rows[0]["payload"]["value"], "123456789.000001")
        self.assertEqual(rows[0]["payload"]["observation_timestamp_ms"], 1794560000000)

    def test_anonymous_trades_exchange_id_and_taker_side(self):
        row = {"symbol": "BTCUSDT", "execId": "trade-123", "time": "1794560000000",
               "price": "80000.00000001", "size": "0.00000001", "side": "Sell"}
        a = decode("trades", "linear", "BTCUSDT", response(row), **BASE)[0]
        b = decode("trades", "linear", "BTCUSDT", response(row), **BASE)[0]
        self.assertEqual(a["record_key"], b["record_key"])
        self.assertEqual(a["trade_id"], "trade-123")
        self.assertTrue(a["payload"]["anonymous_public_execution"])
        self.assertEqual(a["payload"]["price"], "80000.00000001")

    def test_reject_bad_source_identity_and_side(self):
        row = {"symbol": "ETHUSDT", "execId": "a", "time": "1794560000000",
               "price": "1", "size": "1", "side": "Buy"}
        with self.assertRaises(ValueError):
            decode("trades", "linear", "BTCUSDT", response(row), **BASE)
        row["symbol"], row["side"] = "BTCUSDT", "Unknown"
        with self.assertRaises(ValueError):
            decode("trades", "linear", "BTCUSDT", response(row), **BASE)

    def test_reject_private_or_unbounded_history(self):
        with self.assertRaises(ValueError):
            request("trades", "linear", "BTCUSDT", start_ms=1, end_ms=2)
        with self.assertRaises(ValueError):
            request("open_interest", "linear", "BTCUSDT", interval="1m")
        self.assertEqual(request("open_interest", "linear", "BTCUSDT", interval="5min")[0],
                         "/open-interest")

    def test_atomic_store_contract_unknown_coverage(self):
        store = Mock()
        store.ingest.return_value = {"written": 1}
        row = {"symbol": "BTCUSDT", "fundingRate": "0.0001",
               "fundingRateTimestamp": "1794560000000"}
        result = ingest_page(store, kind="funding", category="linear", symbol="BTCUSDT",
                             response=response(row), fetched_at=NOW,
                             url=BASE["url"], run_id="run-test", job_key="job-test",
                             evidence_ref="page-test", start_ms=1794559999999,
                             end_ms=1794560000001, request_params={"limit": 200})
        self.assertEqual(result, {"written": 1})
        records, evidence, key = store.ingest.call_args.args
        self.assertEqual(key, "job-test")
        self.assertEqual(len(records), 1)
        self.assertEqual(evidence["coverage_status"], "UNKNOWN")
        self.assertEqual(evidence["status"], "UNVERIFIED")
        self.assertEqual(store.ingest.call_args.kwargs["run_id"], "run-test")

    def test_reject_out_of_window_before_pg(self):
        store = Mock()
        with self.assertRaises(ValueError):
            ingest_page(store, kind="funding", category="linear", symbol="BTCUSDT",
                        response=response({"symbol": "BTCUSDT", "fundingRate": "0.1",
                                           "fundingRateTimestamp": "1794560000000"}),
                        fetched_at=NOW, url=BASE["url"], run_id="run-test",
                        job_key="job-test", evidence_ref="page-test",
                        start_ms=1, end_ms=100, request_params={})
        store.ingest.assert_not_called()


if __name__ == "__main__":
    unittest.main()
