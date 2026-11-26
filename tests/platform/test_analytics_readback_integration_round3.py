"""Canonical DATA->C calculator->read-only API regression, no live Bybit or F.

The optional disposable PG test runs only on a specifically named CI database.
The unit test traverses the actual DATA receipt and C calculator implementations.
"""
import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.analytics_readback import ReadbackScope, observed_readback
from src.api.config import Settings
from src.api.main import create_app
from src.api.providers import UnavailableReader
from tests.test_execution_contract_bridge import MS, compact_store, fixture


def verified_test_identity():
    # Only a test-injected identity. NOT proof of production/SCM authority.
    return dict(verified=True, installation_id=str(uuid4()), source_sha="a" * 40)


def test_canonical_source_receipt_to_v2_api_adapter_no_calculator_duplication():
    start = MS // 60_000 * 60_000
    store = compact_store([fixture(category="inverse")])
    scope = ReadbackScope(
        category="inverse", symbol="BTCUSDT", start_ms=start,
        end_ms=start + 60_000, as_of_ms=1795000000000,
        interval_ms=60_000, tick_size="0.5", limit=10,
    )
    result = observed_readback(scope, trusted_store=store,
                               trusted_identity=verified_test_identity())
    assert result["status"] == "OBSERVED_UNVERIFIED", result
    assert result["method"] == "tester.execution-metrics/2"
    assert result["quantity_unit"] == "quote"
    assert result["source_quantity_unit"] == "source_native_inverse_contract"
    assert result["quality"]["coverage"] == "UNKNOWN"
    assert result["admitted_to_f"] is False
    assert result["metrics"]["admitted_to_f"] is False
    assert result["metrics"]["bars"][0]["volume"] == "0.125"
    assert result["metrics"]["bars"][0]["vwap"] == "80000"
    store.read_bounded.assert_called_once()
    assert store.read_bounded.call_args.kwargs["limit"] == 10


def test_source_knowledge_after_requested_cutoff_is_not_a_readback():
    start = MS // 60_000 * 60_000
    store = compact_store([fixture()])
    scope = ReadbackScope(
        category="linear", symbol="BTCUSDT", start_ms=start,
        end_ms=start + 60_000, as_of_ms=start + 60_000,
        interval_ms=60_000, tick_size="0.5", limit=10,
    )
    denied = observed_readback(scope, trusted_store=store,
                               trusted_identity=verified_test_identity())
    assert denied["status"] == "UNVERIFIED"
    assert denied["admitted_to_f"] is False


@pytest.mark.integration
def test_disposable_pg_persisted_original_http_to_actual_api_and_no_f(tmp_path):
    """Full original fake HTTP -> durable PG -> DATA -> C -> FastAPI readback."""
    dsn = os.getenv("TESTER_RESEARCH_DATA_TEST_DSN")
    if not dsn:
        pytest.skip("explicit disposable PG readback DSN not configured")
    from psycopg.conninfo import conninfo_to_dict
    endpoint = conninfo_to_dict(dsn)
    if (endpoint.get("host") != "127.0.0.1"
            or endpoint.get("port") != "55479"
            or endpoint.get("dbname") != "tester_research_data_disposable"):
        raise ValueError("refuse non-disposable PG analytics readback")
    import psycopg
    from src.storage.migrations import migrate
    from src.storage.store import Store
    from src.ingest.public_auxiliary import fetch_and_ingest

    stamp = 1600000680000
    symbol = "ADAUSDT"
    row = {"symbol": symbol, "execId": "B-round3-pg-observed-001",
           "time": str(stamp), "price": "1.5", "size": "2", "side": "Buy"}
    result = {"list": [row]}
    params = {"category": "linear", "symbol": symbol, "limit": 1000}
    url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(params)
    body = json.dumps({"retCode": 0, "result": result}).encode()

    class RecordedHttp:
        last_response = {
            "url": url, "status_code": 200, "hash_scope": "original_http_body",
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes_base64": base64.b64encode(body).decode(),
        }
        def call(self, path, supplied):
            assert (path, supplied) == ("/recent-trade", params)
            return result, url

    with psycopg.connect(dsn) as conn:
        migrate(conn)
    store = Store(dsn)
    for _ in range(2):
        receipt = fetch_and_ingest(
            store, RecordedHttp(),
            kind="trades", category="linear", symbol=symbol,
            run_id="B-r3-source-run-1", job_key="B-r3-source-job-1",
            observed_at=datetime(2026, 11, 13, 20, tzinfo=timezone.utc),
        )
        assert receipt["count"] == 1
    app = create_app(
        Settings(frontend_dir=tmp_path / "nonexistent"),
        provider=UnavailableReader(),
        analytics_binding_factory=lambda: (Store(dsn), verified_test_identity()),
    )
    client = TestClient(app, base_url="http://localhost")
    start = stamp // 60_000 * 60_000
    args = dict(category="linear", symbol=symbol,
                start_ms=start, end_ms=start + 60_000,
                interval_ms=60_000, as_of_ms=1794999960000,  # exactly UTC-minute aligned for Chromium input
                tick_size="0.5", limit=10)
    found = client.get("/api/analytics/readback", params=args)
    assert found.status_code == 200, found.text
    data = found.json()["data"]
    assert data["metrics"]["bars"][0]["volume"] == "2"
    assert data["metrics"]["bars"][0]["vwap"] == "1.5"
    assert data["quality"]["coverage"] == "UNKNOWN"
    assert data["source"]["publication_time"] == "UNKNOWN"
    assert data["admitted_to_f"] is False

    # Pass the *actual persisted PG -> DATA -> C -> FastAPI response* to the
    # real ESM terminal validator. Do not use a second hand-built JSON fixture.
    # Chromium route rendering is covered by the separate Playwright suite;
    # this check proves the producer-to-consumer wire contract end to end.
    node = shutil.which("node")
    assert node, "Node.js is required to verify the real frontend consumer"
    script = """
import { readFileSync } from "node:fs";
import { validatedObservedReadback } from "./frontend/analytics-readback.js";
const { payload, scope } = JSON.parse(readFileSync(0, "utf8"));
const result = validatedObservedReadback(payload, scope);
if (result.status !== "OBSERVED_UNVERIFIED" ||
    result.admitted_to_f !== false ||
    result.quality.coverage !== "UNKNOWN" ||
    result.metrics.bars[0].volume !== "2" ||
    result.metrics.bars[0].vwap !== "1.5") process.exit(3);
process.stdout.write("PG_API_FRONTEND_CONTRACT_OK");
"""
    consumer = subprocess.run(
        [node, "--input-type=module", "-e", script],
        input=json.dumps({"payload": found.json(), "scope": args}),
        text=True, capture_output=True, check=False, timeout=10,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert consumer.returncode == 0, consumer.stderr
    assert consumer.stdout == "PG_API_FRONTEND_CONTRACT_OK"

    late = client.get("/api/analytics/readback", params={**args, "as_of_ms": start + 60_000})
    assert late.status_code == 503
    assert late.json()["detail"] == "source_readback_not_verified"
    catalog = client.get("/api/capabilities", params=dict(
        category="linear", symbol=symbol, start_ms=start, end_ms=start + 60_000))
    assert catalog.status_code == 200
    assert catalog.json()["data"]["features"]["analytics_readback"]["enabled"] is True
    # Browser consumer against the *real* FastAPI TestClient responses,
    # not separately invented route fixtures. Browser requests are intercepted
    # and fulfilled by the same TestClient; no public network/Bybit API.
    browser_enabled = os.getenv("TESTER_RESEARCH_BROWSER_E2E") == "1"
    if browser_enabled:
        # CI must opt in and have Chromium installed. Never silently SKIP
        # missing Playwright once the disposable PG + browser gate is enabled.
        from playwright.sync_api import sync_playwright

        html = """<!doctype html><html><body>
          <form id="analytics-readback-form">
            <input id="analytics-readback-tick" value="0.5">
            <input id="analytics-readback-as-of" value="2026-11-26T00:00">
            <button id="analytics-readback-submit" type="submit">Read sample</button>
          </form>
          <p id="analytics-readback-status"></p>
          <pre id="analytics-readback-data"></pre>
          <script type="module">
            import {mountObservedReadback} from "/analytics-readback.js";
            import {request} from "/api.js";
            window.viewer = mountObservedReadback(document, {
              getScope: () => ({
                category: "linear", symbol: "ADAUSDT", interval: "1",
                start: "2020-09-13T12:38", end: "2020-09-13T12:39",
              }),
              fetchCapabilities: (p, signal) =>
                request("/api/capabilities", p, {signal}),
              fetchReadback: (p, signal) =>
                request("/api/analytics/readback", p, {signal}),
            });
          </script>
        </body></html>"""
        # Keep page's UTC scope *exact* to the persisted epoch fixture. Derive
        # the HTML from the already-validated DB timestamps rather than hardcoding.
        from datetime import datetime as _dt
        starts = _dt.fromtimestamp(start / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M")
        ends = _dt.fromtimestamp((start + 60_000) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M")
        html = html.replace('2020-09-13T12:38', starts).replace('2020-09-13T12:39', ends)
        cutoff = datetime.fromtimestamp(args["as_of_ms"] / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M")
        html = html.replace('value="2026-11-26T00:00"', f'value="{cutoff}"')
        # The fixture's original HTTP was observed after its event time and
        # before the UI's explicit 2026 cutoff. No history proof is implied.
        seen_paths = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                def route_request(route):
                    from urllib.parse import urlsplit
                    from pathlib import Path as _Path
                    target = urlsplit(route.request.url)
                    if target.path == "/":
                        return route.fulfill(status=200, content_type="text/html", body=html)
                    if target.path in ("/api/capabilities", "/api/analytics/readback"):
                        seen_paths.append(target.path)
                        # The browser must send the *actual* public Query
                        # shape. Do not hide a bad UI request by stripping
                        # unsupported query parameters in the test proxy.
                        if target.path == "/api/capabilities":
                            from urllib.parse import parse_qs
                            supplied = parse_qs(target.query, keep_blank_values=True)
                            assert set(supplied) == {
                                "category", "symbol", "start_ms", "end_ms", "interval"
                            }
                            assert supplied["interval"] == ["1"]
                        response = client.get(target.path + ("?" + target.query if target.query else ""))
                        return route.fulfill(
                            status=response.status_code, content_type="application/json",
                            body=response.text,
                        )
                    if target.path in ("/analytics-readback.js", "/api.js"):
                        filepath = _Path(__file__).resolve().parents[2] / "frontend" / target.path.lstrip("/")
                        return route.fulfill(
                            status=200, content_type="text/javascript",
                            body=filepath.read_text(encoding="utf-8"),
                        )
                    return route.abort()
                page.route("http://terminal.test/**", route_request)
                page.goto("http://terminal.test/")
                page.locator("#analytics-readback-submit").click()
                page.locator("#analytics-readback-status").filter(
                    has_text="OBSERVED_UNVERIFIED"
                ).wait_for(timeout=10_000)
                status = page.locator("#analytics-readback-status").inner_text()
                assert "historical coverage UNKNOWN" in status
                assert "strategy admission DENIED" in status
                assert "observed volume 2" in page.locator("#analytics-readback-data").inner_text()
                assert "sample VWAP 1.5" in page.locator("#analytics-readback-data").inner_text()
                assert seen_paths == ["/api/capabilities", "/api/analytics/readback"]
            finally:
                browser.close()
    # No mutation of source data/credentials, no F execute path.


@pytest.mark.integration
def test_round7_actual_postgresql_cancels_bounded_store_statement():
    """Disposable real PostgreSQL verifies startup timeout: no fake SQL or SQLite."""
    dsn = os.getenv("TESTER_RESEARCH_DATA_TEST_DSN")
    if not dsn:
        pytest.skip("explicit disposable PostgreSQL DSN not configured")
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from src.storage.store import Store
    import time

    endpoint = conninfo_to_dict(dsn)
    if (
        endpoint.get("host") != "127.0.0.1"
        or endpoint.get("port") != "55479"
        or endpoint.get("dbname") != "tester_research_data_disposable"
    ):
        raise ValueError("refuse non-disposable PostgreSQL timeout test")
    endpoint["connect_timeout"] = "2"
    endpoint["options"] = (
        "-c timezone=UTC -c statement_timeout=250 -c lock_timeout=250"
    )
    store = Store(make_conninfo(**endpoint))
    start_time = time.monotonic()
    with pytest.raises(psycopg.errors.QueryCanceled):
        with store.connect() as connection:
            connection.execute("SELECT pg_sleep(2)")
    elapsed = time.monotonic() - start_time
    assert 0.1 <= elapsed < 2.0, f"measured SQL cancellation {elapsed:.3f}s"
