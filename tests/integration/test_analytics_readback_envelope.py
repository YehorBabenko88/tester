"""Negative tests for the unregistered, read-only analytics envelope."""

import unittest

from src.integration.analytics_readback_envelope import (
    EXECUTION_METHOD,
    ReadbackRefused,
    package_observation,
    validate_scope,
)

SCOPE = dict(
    category="linear", symbol="BTCUSDT", start_ms=60000, end_ms=120000,
    interval_ms=60000, as_of_ms=120000, limit=50,
)
HEX = "a" * 64


def observation():
    return {
        "schema_version": "tester.research-ui-observation/1",
        "status": "OBSERVED_UNVERIFIED",
        "source_verification": "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS",
        "source_coverage": "UNKNOWN",
        "publication_time": "UNKNOWN",
        "historical_known_at_ms": None,
        "f_admission": "DENIED_UNKNOWN_COVERAGE",
        "g_e_execution": "NOT_AUTHORIZED",
        "http_route_status": "NOT_REGISTERED",
        "utc_window": {"start_ms": 60000, "end_ms": 120000},
        "method": EXECUTION_METHOD,
        "metrics_contract": {"status": "CURRENT"},
        "quantity_unit": "base",
        "source_quantity_unit": "source_native_linear_size",
        "source_receipts_digest": HEX,
        "data_execution_digest": HEX,
        "observed_sample_fingerprint": HEX,
        "metrics_digest": HEX,
        "bars": [{"start_ms": 60000, "end_ms": 120000, "trade_count": 1, "volume": "1"}],
        "delta_complete": False,
        "cvd_prior_history_known": False,
    }


class EnvelopeTests(unittest.TestCase):
    def test_default_unverified_no_admission(self):
        value = package_observation(observation(), scope=SCOPE)
        self.assertEqual(value["quality"]["status"], "UNVERIFIED")
        self.assertFalse(value["admitted_to_f"])
        self.assertFalse(value["http_route_registered"])
        self.assertIsNone(value["source"]["dataset_revision"])

    def test_inverse_quote_units(self):
        scope = {**SCOPE, "category": "inverse"}
        value = observation()
        value["quantity_unit"] = "quote"
        value["source_quantity_unit"] = "source_native_inverse_contract"
        self.assertEqual(package_observation(value, scope=scope)["quantity_unit"], "quote")

    def test_bad_market_bounds_and_symbol(self):
        bad = [
            {"symbol": "B"}, {"symbol": "BTC-USDT"}, {"category": "spot"},
            {"start_ms": 120000}, {"end_ms": 120001}, {"as_of_ms": 119999},
            {"limit": 101}, {"interval_ms": 40000}, {"start_ms": True},
        ]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ReadbackRefused):
                validate_scope(**{**SCOPE, **change})

    def test_forged_source_quality_never_promoted(self):
        bad = [
            {"source_coverage": "COMPLETE"}, {"publication_time": "2026-01-01"},
            {"f_admission": "APPROVED"}, {"http_route_status": "REGISTERED"},
            {"status": "VERIFIED"}, {"method": "tester.execution-metrics/1"},
            {"quantity_unit": "quote"}, {"metrics_digest": "BAD"},
            {"utc_window": {"start_ms": 0, "end_ms": 120000}},
            {"metrics_contract": None},
            {"bars": [{"start_ms": 0, "end_ms": 120000}]},
        ]
        for change in bad:
            obj = observation()
            obj.update(change)
            with self.subTest(change=change), self.assertRaises(ReadbackRefused):
                package_observation(obj, scope=SCOPE)

    def test_response_limit(self):
        obj = observation()
        obj["bars"][0]["footprint"] = "x" * 140000
        with self.assertRaisesRegex(ReadbackRefused, "readback_response_bytes_exceeded"):
            package_observation(obj, scope=SCOPE)

    def test_missing_is_never_zero(self):
        obj = observation()
        obj["bars"] = []
        with self.assertRaises(ReadbackRefused):
            package_observation(obj, scope=SCOPE)


if __name__ == "__main__":
    unittest.main()
