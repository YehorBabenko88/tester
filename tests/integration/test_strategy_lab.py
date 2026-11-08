"""The real vendored producer, never a success-returning fake or external path."""

import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.integration.strategy_lab import (
    project_research_chart,
    run_certified_fixture,
    run_synthetic_research,
    serialize_research_bundle,
    verify_research_bundle,
)


def test_actual_f_run_replay_serialization_and_full_correspondence():
    bundle = run_certified_fixture()
    assert bundle == run_certified_fixture()
    assert bundle["source_sha"] == "998c5bc89d1b35c9ad3047b1e50dcb1295120e31"
    assert bundle["certification"] == {"scope": "SYNTHETIC_ONLY", "real_market_coverage": "NOT_PROVEN"}
    result, chart = bundle["result"], bundle["chart"]
    assert result["status"] == "completed"
    assert Decimal(result["trades"][0]["net_pnl"]) == Decimal("-5.195")
    assert result["trades"][0]["reason"] == "stop_loss"
    assert result["engine_version"] == bundle["engine_version"] == "strategy-bar-1.1.0"
    assert chart["snapshot_id"] == "synthetic:" + result["market_snapshot_hash"]
    assert chart["run_id"] == result["run_id"]
    assert chart["synthetic"] is True and chart["coverage"] == "complete"
    assert len(chart["markers"]) == len(result["fills"]) > 0
    assert len(chart["candles"]) == len(bundle["snapshot"]["candles"])
    fills = {fill["id"]: fill for fill in result["fills"]}
    trades = {trade["exit_fill_id"]: trade for trade in result["trades"]}
    for marker in chart["markers"]:
        fill = fills[marker["fill_id"]]
        assert marker["order_id"] == fill["order_id"]
        for field in ("timestamp_ms", "known_at_ms", "time_precision", "price", "role"):
            assert marker[field] == fill[field]
        assert marker["window_start_ms"] == fill["time_window"]["start_ms"]
        assert marker["window_end_ms"] == fill["time_window"]["end_ms"]
        if fill["role"] == "entry":
            assert marker["closed_trade_id"] is None
            assert marker["position_id"] == "position:" + fill["id"]
        else:
            assert marker["closed_trade_id"] == trades[fill["id"]]["id"]
            assert marker["position_id"] == "position:" + trades[fill["id"]]["entry_fill_id"]
    verify_research_bundle(bundle)
    raw = serialize_research_bundle(bundle)
    assert type(raw) is bytes and json.loads(raw) == bundle
    canonical = json.dumps(chart, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(canonical).hexdigest() == bundle["chart_digest"]


@pytest.mark.parametrize("drift", ["authentic", "unknown", "empty_proof", "nested_authentic", "gap", "unclosed"])
def test_synthetic_admission_never_upgrades_unqualified_or_authentic_metadata(drift):
    seed = run_certified_fixture()
    snapshot = copy.deepcopy(seed["snapshot"])
    if drift == "authentic":
        snapshot["provenance"][0]["source_kind"] = "bybit_rest"
    elif drift == "unknown":
        snapshot["coverage"]["status"] = "UNKNOWN"
    elif drift == "empty_proof":
        snapshot["coverage"]["evidence_refs"] = []
    elif drift == "nested_authentic":
        snapshot["candles"][0]["provenance"][0]["source_ref"] = "https://api.bybit.com/market"
    elif drift == "gap":
        snapshot["candles"].pop(1)
    else:
        snapshot["candles"][0]["is_closed"] = False
    with pytest.raises(ValueError):
        run_synthetic_research(seed["strategy"], snapshot)


@pytest.mark.parametrize("part", ["result", "snapshot", "chart", "result_rehashed", "chart_rehashed"])
def test_bundle_tamper_rejected_even_with_rehashed_untrusted_payload(part):
    value = run_certified_fixture()
    if part.startswith("result"):
        value["result"]["fills"][0]["price"] = "999"
    elif part == "snapshot":
        value["snapshot"]["candles"][0]["close"] = "100.5"
    else:
        value["chart"]["markers"][0]["closed_trade_id"] = "invented"
    if part.endswith("rehashed"):
        name = part.split("_")[0]
        value[name + "_digest"] = hashlib.sha256(
            json.dumps(value[name], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    with pytest.raises(ValueError):
        verify_research_bundle(value)


def test_cutoff_visibility_and_view_projection_preserve_all_raw_fills():
    bundle = run_certified_fixture()
    early = project_research_chart(bundle, start_ms=0, end_ms=180000, cutoff_ms=60000)
    assert len(early["markers"]) == 1
    assert early["markers"][0]["time_precision"] == "modeled_open"
    assert early["markers"][0]["side"] == "buy"
    assert len(early["candles"]) == 1
    assert len(bundle["result"]["fills"]) > len(early["markers"])
    assert bundle["chart"]["markers"][1]["side"] == "sell"
    for changes in ({"start_ms": True}, {"end_ms": 180001}, {"cutoff_ms": -1}):
        with pytest.raises(ValueError):
            project_research_chart(bundle, **changes)


def test_actual_engine_take_profit_and_open_entry_without_closed_trade():
    seed = run_certified_fixture()
    take_profit = copy.deepcopy(seed["snapshot"])
    take_profit["candles"][1]["low"] = "99"
    result = run_synthetic_research(seed["strategy"], take_profit)
    assert result["result"]["trades"][0]["reason"] == "take_profit"
    opened = copy.deepcopy(seed["snapshot"])
    for candle in opened["candles"]:
        candle.update(open="100", high="101", low="99", close="100")
    result = run_synthetic_research(seed["strategy"], opened)
    assert result["result"]["trades"] == []
    assert result["result"]["open_position"] is not None
    assert all(marker["closed_trade_id"] is None for marker in result["chart"]["markers"])


def test_exact_vendored_source_and_fixture_bytes_are_pinned():
    root = Path(__file__).resolve().parents[2]
    pin = json.loads((root / "src/integration/F_SOURCE_PIN.json").read_bytes())
    assert pin["repository"] == "YehorBabenko88/tester"
    assert pin["source_sha"] == "998c5bc89d1b35c9ad3047b1e50dcb1295120e31"
    assert len(pin["files"]) == 18
    for name, identity in pin["files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == identity["sha256"], name


def test_actual_short_execution_side_is_not_the_position_direction():
    seed = run_certified_fixture()
    strategy, snapshot = seed["strategy"], seed["snapshot"]
    strategy["entry"] = {"short": strategy["entry"]["long"]}
    strategy["costs"]["funding_policy"] = "ignore_exploratory"
    snapshot["category"] = "linear"
    for candle in snapshot["candles"]:
        candle["category"] = "linear"
    value = run_synthetic_research(strategy, snapshot)
    assert value["result"]["fills"][0]["side"] == "short"
    assert value["chart"]["markers"][0]["side"] == "sell"
    assert value["chart"]["markers"][1]["side"] == "buy"


def test_actual_partial_exits_reuse_position_but_keep_distinct_closed_trade_ids():
    seed = run_certified_fixture()
    strategy, snapshot = seed["strategy"], seed["snapshot"]
    strategy["order"]["max_participation"] = "1"
    template = copy.deepcopy(snapshot["candles"][0])
    snapshot["end_ms"] = 300000
    snapshot["coverage"]["end_ms"] = 300000
    snapshot["candles"] = []
    for index, quantity in enumerate(("1", "1", "0.4", "0.2", "1")):
        candle = copy.deepcopy(template)
        start, end = index * 60000, (index + 1) * 60000
        candle.update(timestamp_ms=start, start_ms=start, end_ms=end)
        candle["coverage"]["end_ms"] = 300000
        if index >= 2:
            candle.update(open="94", high="94", low="90", close="94")
        candle["liquidity"] = dict(quantity=quantity, start_ms=start, end_ms=end,
            available_at_ms=end, evidence_refs=["fixture:executions"],
            availability=copy.deepcopy(candle["availability"]),
            coverage=dict(status="COMPLETE", start_ms=start, end_ms=end, evidence_refs=["fixture:executions"]),
            provenance=copy.deepcopy(candle["provenance"]))
        snapshot["candles"].append(candle)
    value = run_synthetic_research(strategy, snapshot)
    exits = [marker for marker in value["chart"]["markers"] if marker["role"] == "exit"]
    assert len(exits) == 3
    assert len({marker["position_id"] for marker in exits}) == 1
    assert len({marker["closed_trade_id"] for marker in exits}) == 3
    assert len({marker["order_id"] for marker in exits}) == 1
    assert all(marker["time_precision"] == "bar" for marker in exits)


def test_exploratory_strategy_cannot_turn_a_synthetic_grid_gap_into_complete_chart():
    value = run_certified_fixture()
    value["strategy"]["data_policy"] = {"partial": "exploratory", "gaps": "continue"}
    value["snapshot"]["candles"].pop(1)
    with pytest.raises(ValueError, match="gap"):
        run_synthetic_research(value["strategy"], value["snapshot"])
