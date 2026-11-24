"""Offline source-contract regression tests. No network or PC4 access."""
import base64
import hashlib
import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock
from urllib.parse import urlencode
from uuid import uuid4

import requests

from src.ingest import public_auxiliary
from src.ingest.public_access import public_endpoint_key
from src.ingest.public_auxiliary import decode, fetch_and_ingest, request
from src.ingest.rest import API
from src.ingest.transport import Transport

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
BASE = dict(fetched_at=NOW, url="https://api.bybit.com/v5/market/recent-trade",
            ingestion_run="run-test", evidence_ref="page-test")
ROW = {"symbol": "BTCUSDT", "execId": "trade-123", "time": "1794560000000",
       "price": "80000.01", "size": "0.001", "side": "Buy"}


def response(row):
    return {"retCode": 0, "result": {"list": [row]}}


def recorded_api(result, params):
    api = Mock()
    url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(params)
    original = json.dumps({"retCode": 0, "result": result}).encode()
    api.call.return_value = (result, url)
    api.last_response = {"hash_scope": "original_http_body", "url": url,
                         "status_code": 200,
                         "sha256": hashlib.sha256(original).hexdigest(),
                         "bytes_base64": base64.b64encode(original).decode()}
    return api


class PolicyRecorder:
    """Durable-policy protocol double; PostgreSQL semantics are tested elsewhere."""

    def __init__(self):
        self.reserved, self.dispatched, self.finished = [], 0, []

    def reserve(self, key):
        self.reserved.append(key)
        return "GRANTED", str(uuid4())

    def finish(self, key, token, outcome):
        self.finished.append((key, outcome))

    def reserve_dispatch(self):
        self.dispatched += 1
        return "GRANTED", str(uuid4()), 0

    def finish_dispatch(self, token, cooldown_seconds=0):
        pass


class PublicAuxiliaryTests(unittest.TestCase):
    def test_anonymous_trades_exchange_id_and_taker_side(self):
        row = {"symbol": "BTCUSDT", "execId": "trade-123", "time": "1794560000000",
               "price": "80000.00000001", "size": "0.00000001", "side": "Sell"}
        a = decode("trades", "linear", "BTCUSDT", response(row), **BASE)[0]
        b = decode("trades", "linear", "BTCUSDT", response(row), **BASE)[0]
        self.assertEqual(a["record_key"], b["record_key"])
        self.assertEqual(a["trade_id"], "trade-123")
        self.assertTrue(a["payload"]["anonymous_public_execution"])
        self.assertEqual(a["payload"]["price"], "80000.00000001")

    def test_replay_content_identity_independent_of_fetch_clock(self):
        first = decode("trades", "linear", "BTCUSDT", response(ROW), **BASE)[0]
        later = decode("trades", "linear", "BTCUSDT", response(ROW),
                       **{**BASE, "fetched_at": NOW + timedelta(minutes=5)})[0]
        self.assertEqual(first["content_hash"], later["content_hash"])
        self.assertEqual(first["record_key"], later["record_key"])
        self.assertNotEqual(first["source"]["fetched_at"], later["source"]["fetched_at"])

    def test_reject_bad_source_identity_and_side(self):
        row = {"symbol": "ETHUSDT", "execId": "a", "time": "1794560000000",
               "price": "1", "size": "1", "side": "Buy"}
        with self.assertRaises(ValueError):
            decode("trades", "linear", "BTCUSDT", response(row), **BASE)
        row["symbol"], row["side"] = "BTCUSDT", "Unknown"
        self.assertEqual(decode("trades", "linear", "BTCUSDT", response(row), **BASE)[0]["payload"]["side"], "Unknown")
        row["side"] = "invalid"
        with self.assertRaises(ValueError):
            decode("trades", "linear", "BTCUSDT", response(row), **BASE)

    def test_issue105_funding_and_oi_are_not_a_second_writer(self):
        # The canonical funding/OI writer is rest.Backfill -> handoff; this
        # module previously wrote a different payload for the same record_key.
        self.assertEqual(set(public_auxiliary.PATHS), {"trades"})
        for name in ("fetch_funding_pages", "fetch_open_interest_pages", "ingest_page"):
            self.assertFalse(hasattr(public_auxiliary, name), name)
        for kind in ("funding", "open_interest"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                request(kind, "linear", "BTCUSDT")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                decode(kind, "linear", "BTCUSDT", response(ROW), **BASE)
        store, api = Mock(), Mock()
        with self.assertRaises(ValueError):
            fetch_and_ingest(store, api, kind="funding", category="linear",
                             symbol="BTCUSDT", run_id="r", job_key="j")
        api.call.assert_not_called()
        store.ingest.assert_not_called()

    def test_reject_private_or_unbounded_history(self):
        with self.assertRaises(TypeError):
            request("trades", "linear", "BTCUSDT", start_ms=1, end_ms=2)
        for limit in (0, 1001, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                request("trades", "linear", "BTCUSDT", limit=limit)
        self.assertEqual(request("trades", "linear", "BTCUSDT"),
                         ("/recent-trade", {"category": "linear", "symbol": "BTCUSDT", "limit": 1000}))

    def test_verified_http_body_and_atomic_publication(self):
        store = Mock()
        store.ingest.return_value = {"written": 1}
        params = {"category": "linear", "symbol": "BTCUSDT", "limit": 1000}
        api = recorded_api({"list": [ROW]}, params)
        fetch_and_ingest(store, api, kind="trades", category="linear",
                         symbol="BTCUSDT", run_id="run-test", job_key="trade-job",
                         observed_at=NOW)
        records, evidence, key = store.ingest.call_args.args
        self.assertEqual(key, "trade-job")
        self.assertEqual(len(records), 1)
        self.assertEqual(evidence["coverage_status"], "UNKNOWN")
        self.assertEqual(evidence["status"], "UNVERIFIED")
        self.assertEqual((evidence["start_ms"], evidence["end_ms"]), (1794560000000, 1794560000001))
        self.assertEqual(store.ingest.call_args.kwargs["run_id"], "run-test")
        api.last_response["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            fetch_and_ingest(store, api, kind="trades", category="linear",
                             symbol="BTCUSDT", run_id="run-test", job_key="bad-job",
                             observed_at=NOW)

    def test_reject_wrong_source_url_before_storage(self):
        store, api = Mock(), Mock()
        api.call.return_value = ({"list": []},
                                 "https://api.bybit.com/v5/market/recent-trade?symbol=ETHUSDT")
        with self.assertRaises(ValueError):
            fetch_and_ingest(store, api, kind="trades", category="linear",
                             symbol="BTCUSDT", run_id="run-test", job_key="wrong-url")
        store.ingest.assert_not_called()

    def test_refuse_unbounded_http_receipt_before_publication(self):
        store = Mock()
        params = {"category": "linear", "symbol": "BTCUSDT", "limit": 1000}
        api = recorded_api({"list": [ROW]}, params)
        api.last_response["bytes_base64"] = "A" * (4 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            fetch_and_ingest(store, api, kind="trades", category="linear",
                             symbol="BTCUSDT", run_id="run-test", job_key="large",
                             observed_at=NOW)
        store.ingest.assert_not_called()

    def test_reject_source_page_above_limit_or_cursor_before_storage(self):
        store = Mock()
        params = {"category": "linear", "symbol": "BTCUSDT", "limit": 1}
        api = recorded_api({"list": [ROW, {**ROW, "execId": "trade-124"}]}, params)
        with self.assertRaises(ValueError):
            fetch_and_ingest(store, api, kind="trades", category="linear",
                             symbol="BTCUSDT", run_id="run-test", job_key="oversize",
                             limit=1, observed_at=NOW)
        api = recorded_api({"list": [ROW], "nextPageCursor": "unproven"}, {**params, "limit": 1000})
        with self.assertRaises(ValueError):
            fetch_and_ingest(store, api, kind="trades", category="linear",
                             symbol="BTCUSDT", run_id="run-test", job_key="cursor",
                             observed_at=NOW)
        store.ingest.assert_not_called()


class SingleFundingOpenInterestWriterTests(unittest.TestCase):
    """Issue #105(c): exactly one module requests funding/OI from Bybit REST."""

    def test_only_canonical_rest_backfill_requests_funding_and_open_interest(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src"
        pattern = re.compile(r"[\"'](?:/v5/market)?/(?:funding/history|open-interest)[\"']")
        allowed = {
            "ingest/rest.py",  # canonical writer: Backfill -> journal/handoff -> Store
            "ingest/public_access.py",  # transport allowlist, not a writer
            "api/capability_source_contracts.py",  # descriptive metadata only
        }
        found = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8"))
        }
        self.assertEqual(found - allowed, set())
        self.assertIn("ingest/rest.py", found)


class RecentTradeAllowlistTests(unittest.TestCase):
    """Issue #105(a): recent-trade passes the real policy, with an exact query."""

    URL = "https://api.bybit.com/v5/market/recent-trade?category=linear&symbol=BTCUSDT&limit=1000"

    def test_public_endpoint_key_accepts_exact_recent_trade_request(self):
        # Before the fix this raised ValueError("unapproved_endpoint").
        self.assertEqual(public_endpoint_key(self.URL), "api.bybit.com/v5/market/recent-trade")
        self.assertEqual(public_endpoint_key("https://api.bybit.com/v5/market/recent-trade"),
                         "api.bybit.com/v5/market/recent-trade")

    def test_recent_trade_query_is_narrowed(self):
        base = "https://api.bybit.com/v5/market/recent-trade?"
        for query in ("category=linear&symbol=BTCUSDT&limit=1001",
                      "category=spot&symbol=BTCUSDT&limit=61",
                      "category=linear&symbol=BTCUSDT&limit=0",
                      "category=linear&symbol=BTCUSDT&limit=01",
                      "category=linear&symbol=BTCUSDT&startTime=1",
                      "category=linear&symbol=BTCUSDT&cursor=x",
                      "category=option&symbol=BTC",
                      "category=linear",
                      "symbol=BTCUSDT",
                      "category=linear&symbol=btc-usdt"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                public_endpoint_key(base + query)
        public_endpoint_key(base + "category=spot&symbol=BTCUSDT&limit=60")
        public_endpoint_key(base + "category=inverse&symbol=BTCUSD")

    def test_trades_fetch_through_real_api_transport_and_policy(self):
        result = {"category": "linear", "list": [ROW]}
        body = json.dumps({"retCode": 0, "retMsg": "OK", "result": result}).encode()
        reply = requests.Response()
        reply.status_code, reply._content, reply._content_consumed = 200, body, True
        session = Mock()
        session.request.return_value = reply
        policy = PolicyRecorder()
        api = API(Transport(session=session, policy_store=policy, sleep=lambda _: None))
        store = Mock()
        store.ingest.return_value = {"written": 1}
        fetch_and_ingest(store, api, kind="trades", category="linear", symbol="BTCUSDT",
                         run_id="run-real", job_key="job-real", observed_at=NOW)
        self.assertEqual(session.request.call_args.args[1], self.URL)
        self.assertEqual(policy.reserved, ["api.bybit.com/v5/market/recent-trade"])
        self.assertEqual(policy.dispatched, 1)
        self.assertEqual(policy.finished, [("api.bybit.com/v5/market/recent-trade", "allow")])
        records, evidence, _ = store.ingest.call_args.args
        self.assertEqual(evidence["http_sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(records[0]["trade_id"], "trade-123")


if __name__ == "__main__":
    unittest.main()
