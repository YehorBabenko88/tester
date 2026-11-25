"""Offline admission and orchestration safety checks for DATA runner."""
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from src.ingest.public_auxiliary_runner import isolated_dsn, policy_api, run
from src.ingest.rest import API
from src.ingest.transport import Transport

GOOD = ("host=127.0.0.1 port=55479 dbname=tester_aux_disposable_ci user=aux "
        "password=test-only require_auth=scram-sha-256")


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

    def test_issue105_dsn_requires_scram(self):
        # Before #105 a DSN without require_auth (password/md5/trust) passed.
        for bad in (
            GOOD.replace(" require_auth=scram-sha-256", ""),
            GOOD.replace("scram-sha-256", "md5"),
            GOOD.replace("scram-sha-256", "password"),
            GOOD + " sslmode=disable",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                isolated_dsn(bad)

    def test_issue105_real_network_requires_durable_policy(self):
        # Before #105 run() built API() -> Transport() without policy_store,
        # bypassing installation-wide dispatch, 429 cooldown and 403 halt.
        store = Mock()
        with self.assertRaisesRegex(ValueError, "durable public transport policy"):
            run(dsn=GOOD, symbols=["BTCUSDT"], category="linear", store=store)
        for unsafe in (API(), API(Transport()), API(Mock())):
            with self.subTest(api=unsafe), self.assertRaisesRegex(ValueError, "durable"):
                run(dsn=GOOD, symbols=["BTCUSDT"], category="linear", api=unsafe, store=store)
        store.ingest.assert_not_called()
        with self.assertRaisesRegex(ValueError, "durable"):
            policy_api(None, None)

    def test_policy_api_binds_canonical_transport_to_postgres_policy(self):
        policy_dsn = ("host=127.0.0.1 port=55480 dbname=tester user=tester_app "
                      "password=x require_auth=scram-sha-256")
        api = policy_api(policy_dsn, str(uuid4()))
        self.assertIsInstance(api, API)
        self.assertIsInstance(api.transport, Transport)
        self.assertEqual(type(api.transport.policy_store).__name__, "PostgresTransportPolicy")
        with self.assertRaises(ValueError):
            policy_api(policy_dsn.replace(" require_auth=scram-sha-256", ""), str(uuid4()))

    def test_policy_bound_api_is_accepted(self):
        api = API(Transport(policy_store=Mock()))
        with patch("src.ingest.public_auxiliary_runner.fetch_and_ingest",
                   return_value={"records": 1, "coverage": "UNKNOWN"}) as acquire:
            run(dsn=GOOD, symbols=["BTCUSDT"], category="linear", api=api, store=Mock())
        self.assertIs(acquire.call_args.args[1], api)

    def test_refuse_more_than_ten_duplicates_and_metrics_before_network(self):
        api, store = Mock(), Mock()
        with self.assertRaises(ValueError):
            run(dsn=GOOD, symbols=["BTCUSDT"] * 11, category="linear", api=api, store=store)
        with self.assertRaises(ValueError):
            run(dsn=GOOD, symbols=["BTCUSDT", "BTCUSDT"], category="linear", api=api, store=store)
        for kind in ("funding", "open_interest"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                run(dsn=GOOD, symbols=["BTCUSDT"], category="linear", kind=kind,
                    api=api, store=store)
        api.call.assert_not_called()
        store.ingest.assert_not_called()

    def test_ten_symbol_dispatch_is_bounded(self):
        symbols = [f"TEST{i}USDT" for i in range(10)]
        with patch("src.ingest.public_auxiliary_runner.fetch_and_ingest",
                   return_value={"records": 1, "coverage": "UNKNOWN"}) as acquire:
            result = run(dsn=GOOD, symbols=symbols, category="linear",
                         api=Mock(), store=Mock())
        self.assertEqual(len(result["results"]), 10)
        self.assertEqual(acquire.call_count, 10)
        self.assertTrue(all(x["coverage"] == "UNKNOWN" for x in result["results"]))

    def test_explicit_one_symbol_dispatch(self):
        with patch("src.ingest.public_auxiliary_runner.fetch_and_ingest",
                   return_value={"records": 1, "coverage": "UNKNOWN"}) as acquire:
            receipt = run(dsn=GOOD, symbols=["BTCUSDT"], category="linear",
                          api=Mock(), store=Mock())
        self.assertEqual(receipt["coverage"], "UNKNOWN")
        self.assertEqual(receipt["trader_identity"], "ANONYMOUS")
        self.assertEqual(receipt["results"][0]["pages"], 1)
        acquire.assert_called_once()


if __name__ == "__main__":
    unittest.main()
