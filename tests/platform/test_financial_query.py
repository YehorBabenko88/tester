"""Actual pinned B/MASTER/C fixture through E HTTP; synthetic, no storage proof claim."""

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.config import Settings
from src.api.financial_query import FinancialQueryError, FinancialRequest
from src.api.main import create_app
from src.api.models import Query

REF = Path(__file__).with_name("reference") / "financial_query"


def load(name, path, package=False):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)] if package else None
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load("fixture_c_financial", REF / "c_analytics/__init__.py", True)
binding = load("fixture_c_financial.binding", REF / "c_analytics/binding.py")
b = load("fixture_b_financial", REF / "b_models.py")
master = load("fixture_master_financial", REF / "master_storage_api.py")
PARAMS = dict(
    symbol="BTCUSDT",
    start_ms=0,
    end_ms=60000,
    interval="1",
    query_schema_version="1.0",
    information_cutoff_ms=60000,
    price_step="0.10",
    price_step_mode="explicit",
    candle_kind="derived",
)


def storage_row(fetched="1970-01-01T00:00:20Z", ns=100, run="fixture:run-1"):
    original = dict(
        schema_version="1.0",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        dataset="trades",
        interval="",
        timestamp_ms=ns // 1000000,
        timestamp_ns=ns,
        timestamp_precision="ns",
        payload=dict(trade_id="fixture-id", price="10.11", size="2", side="Buy"),
        source=dict(
            url="fixture:B/synthetic-trades",
            fetched_at=fetched,
            source_kind="rest",
            ingestion_run=run,
            source_timestamp=str(ns),
        ),
    )
    row = b.normalize(copy.deepcopy(original))
    observation = dict(
        observation_id="obs:" + run,
        source_id="source:" + run,
        source=copy.deepcopy(row["source"]),
        raw_envelope=original,
        run_id=run,
        job_key="fixture:job",
        evidence_id="evidence:" + run,
        source_kind="rest",
        timestamp_precision="ns",
        source_run_status="matched",
        authentic_run=True,
        source_ingestion_run=run,
        verified=False,
        pagination_status="UNVERIFIED",
        evidence={},
        validator_version="fixture",
    )
    row["provenance"] = dict(
        observations=[observation],
        evidence_refs=[observation["evidence_id"]],
        run_ids=[run],
        unlinked_sources=[],
        authentic=True,
    )
    return row


class FixtureReader:
    def __init__(self, rows=None, page=False):
        self.rows = [storage_row()] if rows is None else rows
        self.calls = []
        self.page = page

    def read(self, resource, query):
        pytest.fail("Derived request must never use generic read")

    def read_derived(self, resource, request):
        self.calls.append(request)
        with pytest.raises(ValidationError):
            request.price_step = "999"
        try:
            batch = binding.bind_storage_trades(
                self.rows,
                venue=request.venue,
                category=request.category,
                symbol=request.symbol,
                start_ms=request.start_ms,
                end_ms=request.end_ms,
                information_cutoff_ms=request.information_cutoff_ms,
                quality_available_at_ms=30000,  # fixture fact, independent of request
                availability=dict(status="UNVERIFIED", reason="Synthetic UNKNOWN history"),
                coverage=dict(
                    status="UNKNOWN", start_ms=0, end_ms=60000, evidence_refs=["fixture:coverage"]
                ),
                trade_projector=master.project_trade,
                provenance_projector=master.provenance_from_storage,
                input_exhausted=True,
            )
            value = batch.response(
                resource,
                interval=request.interval,
                price_step=request.price_step,
                limit=request.limit,
            )
        except binding.BindingUnavailable as exc:
            raise FinancialQueryError("fixture_information_unavailable", 503) from exc
        if self.page:
            value["next_cursor"] = "fixture:raw-page"  # transport-only fixture, not B page proof
        return value


def client(tmp_path, reader):
    return TestClient(
        create_app(Settings(data_dir=tmp_path), provider=reader), base_url="http://127.0.0.1:8765"
    )


def test_reference_pins():
    pin = json.loads((REF / "PIN.json").read_text())
    assert pin["source_commit"] == "19f306cd8314cd9ce0cf488b143a3338d2393442"
    for item in pin["files"]:
        assert hashlib.sha256((REF / item["local"]).read_bytes()).hexdigest() == item["sha256"]


@pytest.mark.parametrize("resource", ["candles", "footprint"])
def test_actual_c_binding_through_http(tmp_path, resource):
    reader = FixtureReader()
    original = copy.deepcopy(reader.rows)
    response = client(tmp_path, reader).get("/api/" + resource, params=PARAMS)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["coverage"]["status"] == "UNKNOWN"
    assert result["data"]
    assert "fixture:run-1" in {p["ingestion_run"] for p in result["provenance"]}
    assert reader.rows == original
    assert reader.calls[0].price_step == "0.10"
    assert isinstance(reader.calls[0], FinancialRequest)


@pytest.mark.parametrize(
    "key,value",
    [
        ("information_cutoff_ms", "1.0"),
        ("information_cutoff_ms", "1e3"),
        ("information_cutoff_ms", "-1"),
        ("information_cutoff_ms", "9007199254740992"),
        ("start_ms", "0.0"),
        ("end_ms", "6e4"),
        ("price_step", "0"),
        ("price_step", "-0.1"),
        ("price_step", "1e-2"),
        ("price_step", "NaN"),
        ("price_step", " 0.1"),
        ("query_schema_version", "2.0"),
        ("price_step_mode", "auto"),
        ("instrument_tick_ref", "fixture:tick"),
    ],
)
def test_invalid_inputs_do_not_call_provider(tmp_path, key, value):
    reader = FixtureReader()
    assert (
        client(tmp_path, reader).get("/api/footprint", params=PARAMS | {key: value}).status_code
        == 422
    )
    assert not reader.calls


@pytest.mark.parametrize(
    "missing",
    [
        "query_schema_version",
        "information_cutoff_ms",
        "price_step",
        "price_step_mode",
        "symbol",
        "start_ms",
        "end_ms",
    ],
)
def test_no_implicit_inputs(tmp_path, missing):
    params = PARAMS.copy()
    params.pop(missing)
    reader = FixtureReader()
    assert client(tmp_path, reader).get("/api/footprint", params=params).status_code == 422
    assert not reader.calls


@pytest.mark.parametrize(
    "resource",
    ["trades", "funding", "open-interest", "instruments", "sources", "coverage", "candles"],
)
def test_unsupported_resources_reject_financial_fields(tmp_path, resource):
    reader = FixtureReader()
    response = client(tmp_path, reader).get(
        "/api/" + resource, params=PARAMS | {"candle_kind": "official_trade"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "financial_query_unsupported"
    assert not reader.calls


def test_reserved_tick_and_unconnected_port(tmp_path):
    reader = FixtureReader()
    c = client(tmp_path, reader)
    r = c.get(
        "/api/footprint",
        params=PARAMS
        | {"price_step_mode": "instrument_tick", "instrument_tick_ref": "fixture:tick-v1"},
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "instrument_tick_unavailable"
    assert not reader.calls
    r = client(tmp_path, object()).get("/api/footprint", params=PARAMS)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "derived_provider_unavailable"


def test_cursor_freezes_exact_cutoff_and_step(tmp_path):
    reader = FixtureReader(page=True)
    c = client(tmp_path, reader)
    first = c.get("/api/footprint", params=PARAMS)
    assert first.status_code == 200, first.text
    cursor = first.json()["next_cursor"]
    params = PARAMS | {"cursor": cursor}
    for changes in [
        {"information_cutoff_ms": 60001},
        {"price_step": "0.1"},
        {"price_step_mode": "instrument_tick", "instrument_tick_ref": "fixture:tick-v1"},
    ]:
        response = c.get("/api/footprint", params=params | changes)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "cursor_scope_mismatch"
    assert len(reader.calls) == 1
    assert c.get("/api/footprint", params=params).status_code == 200
    assert reader.calls[-1].cursor == "fixture:raw-page"


def test_acquisition_nanoseconds_and_quality_are_independent(tmp_path):
    early = storage_row()
    late = storage_row("1970-01-01T00:01:00.000000001Z", run="fixture:late")
    early["provenance"]["observations"].extend(late["provenance"]["observations"])
    reader = FixtureReader([early])
    c = client(tmp_path, reader)
    at = c.get("/api/footprint", params=PARAMS)
    after = c.get("/api/footprint", params=PARAMS | {"information_cutoff_ms": 60001})
    assert at.status_code == after.status_code == 200
    assert {
        p["ingestion_run"]
        for p in at.json()["provenance"]
        if p["ingestion_run"].startswith("fixture:")
    } == {"fixture:run-1"}
    assert {
        p["ingestion_run"]
        for p in after.json()["provenance"]
        if p["ingestion_run"].startswith("fixture:")
    } == {
        "fixture:run-1",
        "fixture:late",
    }
    assert (
        c.get("/api/footprint", params=PARAMS | {"information_cutoff_ms": 29999}).status_code == 503
    )


@pytest.mark.parametrize("value", [True, 1.0, "60000"])
def test_direct_query_cutoff_is_strict_integer(value):
    with pytest.raises(ValidationError):
        Query(information_cutoff_ms=value)


def test_machine_schema_enforces_exact_step_and_tick_selection():
    import jsonschema

    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "contracts/requests/v1/financial-query.schema.json"
        ).read_text()
    )
    jsonschema.Draft202012Validator.check_schema(schema)
    request = dict(
        query_schema_version="1.0",
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        start_ms=0,
        end_ms=60000,
        information_cutoff_ms=60000,
        interval="1",
        source_interval="1",
        price_step="0.10",
        price_step_mode="explicit",
        instrument_tick_ref=None,
        limit=100,
        cursor=None,
    )
    jsonschema.validate(request, schema)
    for changes in [
        {"price_step": 0.1},
        {"price_step": "0.00"},
        {"price_step": "1e-2"},
        {"price_step": "0.1 "},
        {"price_step_mode": "instrument_tick"},
        {"instrument_tick_ref": "fixture:tick"},
    ]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(request | changes, schema)


def test_source_event_after_cutoff_is_excluded_by_actual_c(tmp_path):
    reader = FixtureReader([storage_row("1970-01-01T00:00:31Z", ns=30000000001)])
    r = client(tmp_path, reader).get(
        "/api/footprint", params=PARAMS | {"information_cutoff_ms": 30000}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == []
    assert r.json()["coverage"]["status"] != "COMPLETE"


@pytest.mark.parametrize(
    "private_code",
    [
        "postgresql://private_user:synthetic-secret@127.0.0.1/private_database",
        "source request failed: synthetic-secret /private/archive/file.json",
    ],
)
def test_financial_provider_diagnostics_never_reach_http(tmp_path, private_code):
    class FailingReader:
        def read_derived(self, resource, request):
            raise FinancialQueryError(private_code, 503)

    response = client(tmp_path, FailingReader()).get("/api/footprint", params=PARAMS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "financial_provider_error"
    assert private_code not in response.text
    assert "synthetic-secret" not in response.text
    assert "private_database" not in response.text


@pytest.mark.parametrize(
    "code,expected_status",
    [
        ("financial_query_required", 422),
        ("financial_query_unsupported", 422),
        ("price_step_selection_invalid", 422),
        ("instrument_tick_unavailable", 503),
        ("derived_provider_unavailable", 503),
    ],
)
def test_financial_public_codes_have_fixed_http_status(tmp_path, code, expected_status):
    class FailingReader:
        def read_derived(self, resource, request):
            raise FinancialQueryError(code, 200)

    response = client(tmp_path, FailingReader()).get("/api/footprint", params=PARAMS)
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == code


def test_http_boundary_rechecks_mutated_financial_error(tmp_path):
    class FailingReader:
        def read_derived(self, resource, request):
            exc = FinancialQueryError("derived_provider_unavailable", 503)
            exc.code = "synthetic-secret after construction"
            exc.status = 201
            raise exc

    response = client(tmp_path, FailingReader()).get("/api/footprint", params=PARAMS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "financial_provider_error"
    assert "synthetic-secret" not in response.text


def test_unknown_financial_error_does_not_retain_or_format_diagnostic():
    class PrivateDiagnostic:
        def __str__(self):
            pytest.fail("Private diagnostic must not be formatted")

        def __hash__(self):
            pytest.fail("Private diagnostic must not be hashed")

    exc = FinancialQueryError(PrivateDiagnostic(), {"private_status": "synthetic-secret"})
    assert exc.args == ("financial_provider_error",)
    assert str(exc) == "financial_provider_error"
    assert exc.status == 503
