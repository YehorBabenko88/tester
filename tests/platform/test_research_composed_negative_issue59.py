"""Negative DATA72 -> RESEARCH -> UI acceptance in disposable composition only.

Pure negative reads use authentic DATA contract fixture rows. The integration
case reads real immutable PostgreSQL data; hostile projections are made only
on read-only copies. Never disables append-only triggers or promotes REAL F.
"""

import base64
import copy
import hashlib
import importlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from fractions import Fraction
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest

from src.analytics.exact import decimal
from src.integration.data_research_adapter import (
    read_data_metrics,
    require_strategy_lab_f_input,
)
from src.integration.research_ui_projection import (
    project_source_observation,
    read_verified_ui_observation,
)
from src.storage.models import digest
from tests.test_execution_contract_bridge import MS, fixture

WINDOW = MS // 60000 * 60000
SCOPE = {
    "category": "linear",
    "symbol": "BTCUSDT",
    "start_ms": WINDOW,
    "end_ms": WINDOW + 60000,
    "interval_ms": 60000,
    "tick_size": "0.5",
    "limit": 10,
}


def _fixture_read(row=None):
    store = Mock()
    store.read.return_value = [fixture(side="Unknown") if row is None else row]
    return store


def _verified_sample():
    return read_data_metrics(_fixture_read(), **SCOPE)


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_provenance",
        "forged_http_hash",
        "missing_http_body",
        "corrupt_http_bytes",
        "forged_page_hash",
        "forged_row_hash",
        "missing_evidence_id",
        "missing_row_source",
        "missing_parent_refs",
        "tampered_original_price",
        "forged_complete_coverage",
    ),
    ids=lambda value: value,
)
def test_negative_data_http_receipts_or_provenance_refuse_ui_read(mutation):
    from src.ingest.execution_contract import ExecutionContractError

    row = fixture(side="Unknown")
    observation = row["provenance"]["observations"][0]
    evidence = observation["evidence"]
    source = observation["source"]
    edits = {
        "missing_provenance": lambda: row.pop("provenance"),
        "forged_http_hash": lambda: evidence.__setitem__("http_sha256", "0" * 64),
        "missing_http_body": lambda: evidence.pop("http_bytes_base64"),
        "corrupt_http_bytes": lambda: evidence.__setitem__(
            "http_bytes_base64", base64.b64encode(b"altered HTTP").decode()
        ),
        "forged_page_hash": lambda: evidence.__setitem__("page_sha256", "0" * 64),
        "forged_row_hash": lambda: source.__setitem__("sha256", "0" * 64),
        "missing_evidence_id": lambda: observation.pop("evidence_id"),
        "missing_row_source": lambda: observation.pop("source"),
        "missing_parent_refs": lambda: source.__setitem__("evidence_refs", []),
        "tampered_original_price": lambda: row["payload"].__setitem__("price", "7"),
        "forged_complete_coverage": lambda: evidence.__setitem__("coverage_status", "COMPLETE"),
    }
    edits[mutation]()
    store = _fixture_read(row)
    with pytest.raises((ExecutionContractError, ValueError)):
        read_verified_ui_observation(store, **SCOPE)
    store.ingest.assert_not_called()


@pytest.mark.parametrize(
    "key,value",
    (
        ("persisted_dataset_revision", "forged-revision"),
        ("historical_known_at_ms", WINDOW + 1),
        ("coverage", "COMPLETE"),
        ("admitted_to_f", True),
        ("f_input", {"run_id": "fabricated"}),
        ("g_e_result", {"ack": True}),
    ),
    ids=lambda pair: str(pair),
)
def test_observed_sample_fingerprint_cannot_be_promoted_to_revision(key, value):
    source = _verified_sample()
    assert source["persisted_dataset_revision"] is None
    assert source["historical_known_at_ms"] is None
    assert source["coverage"] == "UNKNOWN"
    source[key] = value
    with pytest.raises(ValueError, match="research_ui_historical_authority_refused"):
        project_source_observation(source)


def test_source_unavailable_is_not_a_successful_ui_or_strategy_job():
    store = Mock()
    store.read.return_value = []
    with pytest.raises(ValueError, match="research_ui_not_source_backed"):
        read_verified_ui_observation(store, **SCOPE)
    store.ingest.assert_not_called()


def test_unknown_side_exact_decimal_and_real_f_denied_on_valid_data_receipt():
    source = _verified_sample()
    ui = project_source_observation(source)
    bar = ui["bars"][0]
    assert bar["unknown_side_volume"] == "0.125"
    assert bar["delta_known"] == "0"
    assert bar["cvd_known"] == "0"
    assert bar["vwap"] == "80000"
    assert bar["poc"] == "80000"
    assert bar["footprint"][0]["unknown_side_volume"] == "0.125"
    assert ui["source_coverage"] == "UNKNOWN"
    assert ui["f_admission"] == "DENIED_UNKNOWN_COVERAGE"
    assert ui["g_e_execution"] == "NOT_AUTHORIZED"
    assert ui["http_route"] is None
    with pytest.raises(ValueError, match="real_historical_visibility_unknown_f_denied"):
        require_strategy_lab_f_input(source)


def test_lookahead_is_refused_before_projection():
    store = _fixture_read()
    with pytest.raises(ValueError, match="late_source_knowledge"):
        read_verified_ui_observation(store, **SCOPE, as_of_ms=MS + 1000)


@pytest.mark.parametrize(
    "field,value",
    (
        ("delta_known", "100"),
        ("cvd_known", "99"),
        ("volume", "NaN"),
        ("vwap", "-1"),
        ("poc", "NaN"),
        ("unknown_side_volume", "0"),
        ("buy_volume", "0.01"),
    ),
)
def test_forged_math_and_nonfinite_decimal_refused_even_after_rehash(field, value):
    source = _verified_sample()
    source["metrics"]["bars"][0][field] = value
    source["metrics_digest"] = digest(source["metrics"])
    with pytest.raises(ValueError):
        project_source_observation(source)


@pytest.mark.integration
def test_actual_postgres_source_receipt_tampering_and_ui_denial():
    """Actual PG16 roundtrip, then tamper *read copies*, never immutable rows."""
    dsn = os.environ.get("TESTER_RESEARCH_DATA_TEST_DSN")
    if not dsn:
        pytest.skip("disposable DATA PostgreSQL not configured")
    from psycopg.conninfo import conninfo_to_dict

    endpoint = conninfo_to_dict(dsn)
    if (
        endpoint.get("host") != "127.0.0.1"
        or endpoint.get("port") != "55476"
        or endpoint.get("dbname") != "tester_research_data_disposable"
    ):
        raise ValueError("refusing non-disposable receipt test database")

    import psycopg

    from src.storage.migrations import migrate
    from src.storage.store import Store

    fetch_and_ingest = importlib.import_module("src.ingest.public_auxiliary").fetch_and_ingest

    with psycopg.connect(dsn) as conn:
        migrate(conn)

    stamp = 1600000680000
    symbol = "ADAUSDT"
    original_rows = [
        {
            "symbol": symbol,
            "execId": "research-negative-pg-unknown-v1",
            "time": str(stamp),
            "price": "1.23456789",
            "size": "0.00000013",
            "side": "Unknown",
        },
        {
            "symbol": symbol,
            "execId": "research-negative-pg-buy-v1",
            "time": str(stamp + 1),
            "price": "1.23456890",
            "size": "0.00000017",
            "side": "Buy",
        },
    ]
    result = {"list": original_rows}
    params = {"category": "linear", "symbol": symbol, "limit": 1000}
    url = "https://api.bybit.com/v5/market/recent-trade?" + urlencode(params)
    body = json.dumps({"retCode": 0, "result": result}).encode()

    class RecordedHttp:
        last_response = {
            "url": url,
            "status_code": 200,
            "hash_scope": "original_http_body",
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes_base64": base64.b64encode(body).decode(),
        }

        def call(self, path, supplied):
            assert (path, supplied) == ("/recent-trade", params)
            return result, url

    stored = Store(dsn)
    fetch_and_ingest(
        stored,
        RecordedHttp(),
        kind="trades",
        category="linear",
        symbol=symbol,
        run_id="research-negative-pg-run-v1",
        job_key="research-negative-pg-job-v1",
        observed_at=datetime(2026, 11, 13, 20, tzinfo=UTC),
    )
    reopened = Store(dsn)
    start = stamp // 60000 * 60000
    scope = dict(
        category="linear",
        symbol=symbol,
        start_ms=start,
        end_ms=start + 60000,
        interval_ms=60000,
        tick_size="0.00000001",
        limit=10,
    )
    actual = read_data_metrics(reopened, **scope)
    ui = read_verified_ui_observation(reopened, **scope)
    bar = ui["bars"][0]
    assert bar["trade_count"] == 2
    assert bar["volume"] == "0.0000003"
    assert bar["unknown_side_volume"] == "0.00000013"
    assert bar["buy_volume"] == "0.00000017"
    assert bar["delta_known"] == "0.00000017"
    assert bar["cvd_known"] == "0.00000017"
    assert bar["poc"] == "1.2345689"
    weighted = (
        Fraction("1.23456789") * Fraction("0.00000013")
        + Fraction("1.23456890") * Fraction("0.00000017")
    ) / Fraction("0.00000030")
    assert bar["vwap"] == decimal(weighted)
    assert ui["unknown_side_present"]
    assert ui["f_admission"] == "DENIED_UNKNOWN_COVERAGE"
    assert ui["g_e_execution"] == "NOT_AUTHORIZED"
    assert ui["http_route"] is None
    with pytest.raises(ValueError, match="real_historical_visibility_unknown_f_denied"):
        require_strategy_lab_f_input(actual)

    class CorruptedRead:
        def __init__(self, change):
            self.change = change

        def read(self, *args, **kwargs):
            records = copy.deepcopy(reopened.read(*args, **kwargs))
            assert len(records) == 2
            self.change(records[0])
            return records

    for change in (
        lambda row: row.pop("provenance"),
        lambda row: row["provenance"]["observations"][0]["evidence"].__setitem__(
            "http_sha256", "0" * 64
        ),
        lambda row: row["provenance"]["observations"][0]["evidence"].pop("http_bytes_base64"),
        lambda row: row["provenance"]["observations"][0].__setitem__(
            "source_run_status", "mismatched"
        ),
    ):
        with pytest.raises(ValueError):
            read_verified_ui_observation(CorruptedRead(change), **scope)

    # The readbacks are corrupted only in memory; immutable PostgreSQL rows
    # and provenance remain verifiable on every subsequent fresh read.
    assert read_verified_ui_observation(reopened, **scope) == ui
    forged = copy.deepcopy(actual)
    forged["persisted_dataset_revision"] = "unreviewed-B-revision"
    with pytest.raises(ValueError, match="research_ui_historical_authority_refused"):
        project_source_observation(forged)
    with pytest.raises(ValueError, match="late_source_knowledge"):
        read_verified_ui_observation(reopened, **scope, as_of_ms=stamp + 60000)


@pytest.mark.integration
@pytest.mark.parametrize("ambient_hostaddr", ("127.0.0.2", "172.18.0.2"))
def test_actual_postgres_client_hostaddr_is_pinned_despite_libpq_environment(
    monkeypatch, ambient_hostaddr
):
    """Client transport pinning is not Docker's server-side SQL address.

    The Docker runner publishes 127.0.0.1:55472 to the PostgreSQL server's
    independent 172.x:5432 network namespace. Server-side inet_server_addr()
    is not the client destination and must never be compared to the DSN host.
    We instead assert the actual libpq client connection's hostaddr/port,
    while explicitly poisoning PGHOSTADDR, PGHOST, PGPORT and PGDATABASE.
    """
    dsn = os.getenv("TEST_RESEARCH_DATABASE_URL")
    if not dsn:
        pytest.skip("explicit disposable RESEARCH PostgreSQL DSN required")

    from psycopg.conninfo import conninfo_to_dict

    from src.api.research_ledger import (
        PersistentResearchLedger,
        validate_research_dsn,
    )

    endpoint = conninfo_to_dict(dsn)
    assert endpoint["host"] == "127.0.0.1"
    assert endpoint["port"] == "55472"
    assert endpoint["dbname"] == "tester_research_combined_ci"
    assert validate_research_dsn(dsn)["host"] == "127.0.0.1"
    monkeypatch.setenv("PGHOSTADDR", ambient_hostaddr)
    monkeypatch.setenv("PGHOST", ambient_hostaddr)
    monkeypatch.setenv("PGPORT", "1")
    monkeypatch.setenv("PGDATABASE", "postgres")

    with PersistentResearchLedger(dsn).connection() as conn:
        # These are the actual client-side libpq parameters. Both must
        # remain exactly pinned despite a hostile libpq environment.
        assert conn.info.host == endpoint["host"]
        assert conn.info.hostaddr == endpoint["host"]
        assert int(conn.info.port) == int(endpoint["port"])
        assert conn.info.dbname == endpoint["dbname"]
        server = conn.execute(
            "SELECT host(inet_server_addr()) AS address, "
            "inet_server_port() AS port, current_database() AS db"
        ).fetchone()

    # Never conflate server namespace address/port with the client endpoint.
    assert server["db"] == endpoint["dbname"]
    assert server["address"] is not None
    assert isinstance(server["port"], int) and 1 <= server["port"] <= 65535
    for explicit in ("127.0.0.2", "172.18.0.2", "127.0.0.1"):
        with pytest.raises(ValueError, match="explicit isolated"):
            validate_research_dsn(
                "host=127.0.0.1 hostaddr=" + explicit + " port=55472 dbname=tester_research_test"
            )


def test_exact_five_new_research_allowlist_blobs_are_pending_not_approved():
    """Immutable Git evidence for MASTER #77 allowlist delta (CI only).

    The reviewed files remain unapproved. Run this against the exact PR71
    SHA in an isolated checkout with the MASTER review object fetched.
    No Git writes, production processes, trading, or external HTTP calls.
    """
    head = os.getenv("RESEARCH_ALLOWLIST_EXACT_HEAD")
    if head is None:
        pytest.skip("Source Git allowlist review checked by pinned composed CI")
    if not re.fullmatch(r"[a-f0-9]{40}", head):
        raise ValueError("unqualified_research_head")

    def get(*args):
        return subprocess.check_output(("git", *args), timeout=15)

    path = "docs/research_composed_negative_matrix.md"
    content = get("show", f"{head}:{path}").decode("utf-8")
    start, end = (
        "<!-- RESEARCH_ALLOWLIST_JSON_BEGIN -->",
        "<!-- RESEARCH_ALLOWLIST_JSON_END -->",
    )
    assert content.count(start) == content.count(end) == 1
    embedded = content.split(start, 1)[1].split(end, 1)[0].strip()
    assert embedded.startswith("```json\n") and embedded.endswith("```")
    snapshot = json.loads(embedded[len("```json\n") : -3].strip())
    original = "e76b08dbb2b45121c577c84414c56a7218994d0c"
    master = "298d610771d962ee7b796eac1834d74138768fac"
    assert snapshot["schema"] == "research-pr71-allowlist-review/1"
    assert snapshot["research_immutable_source_commit"] == original
    assert snapshot["master_review_commit"] == master
    assert snapshot["review_status"] == "REVIEW_PENDING_MASTER_NOT_APPROVED"
    assert snapshot["broadened_master_allowlist_approved"] is False
    assert snapshot["installable_runtime_files"] == []
    assert snapshot["native_launcher_activation"] == "FORBIDDEN"
    assert snapshot["f_admission"] == "DENIED_UNKNOWN_COVERAGE"
    assert snapshot["historical_completeness"] == "UNKNOWN"

    expected = {
        ".github/workflows/research-private-generation.yml": "7f382dd526b2e030af59a69ba5ba352983638a41",
        "docs/research_private_generation_e2e_commands.md": "a1f05176b56ad323736e92dbda44a06861168c3a",
        "docs/research_private_generation_source_ledger.json": "1447406f01ff1c200059a89a7556dd404be3f9be",
        "scripts/research_private_generation_issue59.py": "9845ac1f3ef1be49deea4c5e32a0c9da34b5bfd3",
        "tests/platform/test_research_private_generation_issue59.py": "7da3cf46fb93541e5f58b1bdb785c234002d850b",
    }
    files = snapshot["reviewed_files"]
    assert len(files) == len(expected) == 5
    assert {e["path"]: e["git_blob_sha1"] for e in files} == expected
    assert all(e["mode"] == "100644" for e in files)
    master_review_path = snapshot["master_allowlist_review_path"]
    master_review = get("show", f"{master}:{master_review_path}")
    master_blob = hashlib.sha1(
        b"blob " + str(len(master_review)).encode() + b"\x00" + master_review
    ).hexdigest()
    assert master_blob == snapshot["master_allowlist_review_git_blob_sha1"]
    approved = json.loads(
        get("show", f"{master}:docs/coordination/master-reviewed-producer-files.json")
    )
    assert approved["schema"] == "master-reviewed-producer-files/1"
    for e in files:
        p, sha = e["path"], e["git_blob_sha1"]
        assert p not in approved["producers"]["RESEARCH"], p
        assert ("- `" + p + "`").encode() in master_review
        row = get("ls-tree", original, "--", p).decode("utf-8").strip()
        mode, kind, observed_sha, observed_path = row.split(None, 3)
        assert (mode, kind, observed_sha, observed_path) == ("100644", "blob", sha, p)
        raw = get("show", f"{original}:{p}")
        git_hash = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()
        assert git_hash == sha
        assert get("show", f"{head}:{p}") == raw, (
            "Five reviewed files changed; a new immutable review is required",
            p,
        )
