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
                interval_ms=60_000, as_of_ms=1795000000000,
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
    # No mutation of source data/credentials, no F execute path.
