"""Observed grids are useful receipts, never historical-completeness certificates."""
import copy
import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def wrapper():
    coverage = dict(status="UNKNOWN", start_ms=0, end_ms=120000, evidence_refs=[])
    availability = dict(status="PARTIAL", reason="Observed only")
    records = []
    for i in range(2):
        records.append(dict(schema_version="1.0", venue="bybit", category="linear", symbol="BTCUSDT",
            interval="1", timestamp_ms=i*60000, start_ms=i*60000, end_ms=(i+1)*60000,
            kind="official_trade", is_closed=True, open="1", high="2", low="1", close="2", volume="3",
            availability=copy.deepcopy(availability), coverage=copy.deepcopy(coverage),
            provenance=[dict(source_kind="bybit_rest", source_ref="https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=1",
                source_hash=("a" if i==0 else "b")*64, ingestion_run="run:original", timestamp_precision="ms", source_timestamp=str(i*60000))]))
    return dict(kind="SYNTHETIC_UNIT_INPUT_NOT_REAL_MARKET", captured_at="1970-01-01T00:03:00+00:00",
                availability="PARTIAL", coverage_status="UNKNOWN",
                query=dict(venue="bybit", category="linear", symbol="BTCUSDT", interval="1", start_ms=0, end_ms=120000, limit=100),
                api_pages=[dict(records=2, sha256="c"*64, availability=availability, coverage=coverage)], records=records)


def receipt(value, **changes):
    module = importlib.import_module("src.integration.lab_coverage_evidence")
    raw = json.dumps(value).encode()
    args = dict(expected_snapshot_sha256=hashlib.sha256(raw).hexdigest(), venue="bybit", category="linear",
                symbol="BTCUSDT", interval="1", start_ms=0, end_ms=120000, source_cutoff_ms=180000)
    return module.build_observed_grid_receipt(raw, **(args | changes))


def test_observed_grid_preserves_unknown_quality_and_original_provenance():
    value = wrapper()
    result = receipt(value)
    assert result["evidence_scope"] == "observed_grid_only_NOT_HISTORICAL_COMPLETENESS"
    assert result["historical_completeness"] == "UNPROVEN" and result["f_admission"] == "NOT_READY"
    assert result["grid_checks_passed"] is True
    assert result["observed_records"] == result["expected_grid_records"] == 2
    assert result["source_cutoff_semantics"] == "input_observation_cutoff_not_historical_visibility"
    assert result["declared_source_coverage_status"] == "UNKNOWN"
    assert result["declared_pages"][0]["coverage"] == value["api_pages"][0]["coverage"]
    assert result["provenance"] == [record["provenance"][0] for record in value["records"]]
    assert result["provenance"][1]["source_timestamp"] == "60000"
    assert "coverage" not in result and "quality_available_at_ms" not in result


@pytest.mark.parametrize("mutation", ["gap", "duplicate", "unordered", "unclosed", "scope", "ohlcv", "fake_complete", "fake_page_complete", "source_hash", "source_time", "missing_run"])
def test_hash_rebound_input_still_requires_observed_scope_grid_and_original_metadata(mutation):
    value = wrapper()
    if mutation == "gap":
        value["records"].pop()
    elif mutation == "duplicate":
        value["records"][1] = copy.deepcopy(value["records"][0])
    elif mutation == "unordered":
        value["records"].reverse()
    elif mutation == "unclosed":
        value["records"][0]["is_closed"] = False
    elif mutation == "scope":
        value["records"][0]["symbol"] = "ETHUSDT"
    elif mutation == "ohlcv":
        value["records"][0]["high"] = "0.5"
    elif mutation == "fake_complete":
        value["coverage_status"] = "COMPLETE"
    elif mutation == "fake_page_complete":
        value["api_pages"][0]["coverage"]["status"] = "COMPLETE"
    elif mutation == "source_hash":
        value["records"][0]["provenance"][0]["source_hash"] = None
    elif mutation == "source_time":
        value["records"][0]["provenance"][0]["source_timestamp"] = "60000"
    elif mutation == "missing_run":
        value["records"][0]["provenance"][0]["ingestion_run"] = ""
    with pytest.raises(ValueError):
        receipt(value)


@pytest.mark.parametrize("changes", [dict(expected_snapshot_sha256="0"*64), dict(source_cutoff_ms=119999),
    dict(source_cutoff_ms=180001), dict(source_cutoff_ms=True), dict(start_ms=True), dict(interval="M"), dict(symbol="ETHUSDT")])
def test_hash_and_explicit_observation_cutoff_are_bound_without_visibility_inference(changes):
    with pytest.raises(ValueError):
        receipt(wrapper(), **changes)


def test_published_f_fixture_is_pinned_and_explicitly_synthetic():
    root = Path(__file__).parent / "fixtures/f998"
    pin = json.loads((root / "PIN.json").read_text())
    raw = (root / "example.snapshot.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pin["files"]["docs/strategy/example.snapshot.json"]["sha256"]
    assert pin["source_sha"] == "998c5bc89d1b35c9ad3047b1e50dcb1295120e31"
    value = json.loads(raw)
    assert value["coverage"]["status"] == "COMPLETE" and value["coverage"]["evidence_refs"] == ["fixture:bars"]
    assert value["provenance"][0]["source_kind"] == "synthetic_fixture"


def test_opt_in_published_f_engine_replay_persistence_and_overlay(tmp_path):
    root = os.environ.get("TESTER_F_PUBLISHED_ROOT")
    if not root:
        pytest.skip("Opt-in pinned published F source required; no engine is fabricated")
    fixtures = Path(__file__).parent / "fixtures/f998"
    pin = json.loads((fixtures / "PIN.json").read_text())
    for relative, identity in pin["files"].items():
        raw = (Path(root) / relative).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == identity["sha256"], relative
    completed = subprocess.run(
        [sys.executable, "-I", "-B", str(Path(__file__).with_name("f_published_probe.py")),
         root, str(fixtures), str(tmp_path)], capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["classification"] == "SYNTHETIC_ONLY"
    assert result["fill_overlay_correspondence"] == "PASS"
    assert result["canonical_persistence"] == result["g_overlay"] == "NOT_INTEGRATED"


def test_opt_in_actual_observed_grid_remains_unproven():
    actual = os.environ.get("TESTER_OBSERVED_SNAPSHOT")
    if not actual:
        pytest.skip("Opt-in saved public snapshot required; no network or installed API is used")
    raw = Path(actual).read_bytes()
    expected = "178e775375c39f360e858c2dc68b24a11af71f3e47888bfac0b8e853ea8826d6"
    assert hashlib.sha256(raw).hexdigest() == expected
    value = json.loads(raw)
    query = value["query"]
    module = importlib.import_module("src.integration.lab_coverage_evidence")
    result = module.build_observed_grid_receipt(
        raw, expected_snapshot_sha256=expected,
        **{key: query[key] for key in ("venue", "category", "symbol", "interval", "start_ms", "end_ms")},
        source_cutoff_ms=query["end_ms"],
    )
    assert result["observed_records"] == 1440
    assert result["declared_source_coverage_status"] == "UNKNOWN"
    assert result["historical_completeness"] == "UNPROVEN"
    assert result["f_admission"] == "NOT_READY"
    assert result["historical_visibility_proven"] is False


@pytest.mark.parametrize("actual_snapshot", [False, True])
def test_opt_in_actual_published_f_metadata_accepts_its_fixture_and_rejects_actual_unknown(actual_snapshot):
    root = os.environ.get("TESTER_F_PUBLISHED_ROOT")
    actual = os.environ.get("TESTER_OBSERVED_SNAPSHOT")
    if not root or (actual_snapshot and not actual):
        pytest.skip("Opt-in published F root and actual snapshot paths required; no F validator is fabricated")
    fixtures = Path(__file__).parent / "fixtures/f998"
    pin = json.loads((fixtures / "PIN.json").read_text())
    for relative in ("src/backtest/quality.py", "src/strategy/dsl.py"):
        assert hashlib.sha256((Path(root)/relative).read_bytes()).hexdigest() == pin["files"][relative]["sha256"]
    path = actual if actual_snapshot else str(fixtures / "example.snapshot.json")
    script = """
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
from src.backtest.quality import metadata
value=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
if sys.argv[3]=='actual':
 assert hashlib.sha256(Path(sys.argv[2]).read_bytes()).hexdigest()=='178e775375c39f360e858c2dc68b24a11af71f3e47888bfac0b8e853ea8826d6'
 for exploratory in (False,True):
  try: metadata(value['records'][0],value['query']['start_ms'],value['query']['end_ms'],exploratory,set())
  except ValueError as error: assert str(error)=='coverage evidence required', str(error)
  else: raise AssertionError('Actual UNKNOWN snapshot improperly admitted')
else:
 warnings=set();metadata(value,value['start_ms'],value['end_ms'],False,warnings)
 assert 'SYNTHETIC_DATA' in warnings and 'NON_CERTIFIED' in warnings
print('published-F-metadata-guard:OK')
"""
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", script, root, path,
                                "actual" if actual_snapshot else "synthetic"], capture_output=True, text=True, timeout=15)
    assert completed.returncode == 0, completed.stderr
    assert "published-F-metadata-guard:OK" in completed.stdout
