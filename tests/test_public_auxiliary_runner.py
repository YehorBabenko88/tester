"""Offline admission and orchestration safety checks for DATA runner."""
import unittest
from unittest.mock import Mock, patch

from src.ingest.public_auxiliary_runner import isolated_dsn, run

GOOD = "host=127.0.0.1 port=55479 dbname=tester_aux_disposable_ci user=aux password=test-only"


class AuxiliaryRunnerTests(unittest.TestCase):
    def test_protected_ports_and_non_disposable_names(self):
        for bad in (
            GOOD.replace("55479", "5432"),
            GOOD.replace("55479", "55471"),
            GOOD.replace("127.0.0.1", "192.168.1.5"),
            GOOD.replace("tester_aux_disposable_ci", "production"),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                isolated_dsn(bad)
        self.assertEqual(isolated_dsn(GOOD), GOOD)

    def test_refuse_more_than_ten_and_duplicates_before_network(self):
        api, store = Mock(), Mock()
        with self.assertRaises(ValueError):
            run(dsn=GOOD, symbols=["BTCUSDT"] * 11, kind="trades",
                category="linear", api=api, store=store)
        with self.assertRaises(ValueError):
            run(dsn=GOOD, symbols=["BTCUSDT", "BTCUSDT"], kind="trades",
                category="linear", api=api, store=store)
        api.call.assert_not_called()
        store.ingest.assert_not_called()

    def test_no_implicit_historical_window(self):
        with self.assertRaises(ValueError):
            run(dsn=GOOD, symbols=["BTCUSDT"], kind="funding",
                category="linear", api=Mock(), store=Mock())

    def test_explicit_one_symbol_dispatch(self):
        with patch("src.ingest.public_auxiliary_runner.fetch_and_ingest",
                   return_value={"records": 1, "coverage": "UNKNOWN"}) as acquire:
            receipt = run(dsn=GOOD, symbols=["BTCUSDT"], kind="trades",
                          category="linear", api=Mock(), store=Mock())
        self.assertEqual(receipt["coverage"], "UNKNOWN")
        self.assertEqual(receipt["trader_identity"], "ANONYMOUS")
        self.assertEqual(receipt["results"][0]["pages"], 1)
        acquire.assert_called_once()


if __name__ == "__main__":
    unittest.main()
