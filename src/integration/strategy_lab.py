"""Actual pinned F execution and bounded synthetic-only research projection.

This module has no canonical persistence, HTTP, credentials or real-order port.
F remains the sole authority for financial and replay semantics.
"""

import copy
import hashlib
import json
import re
from pathlib import Path

from src.backtest import run
from src.backtest.engine import ENGINE_VERSION
from src.strategy.dsl import digest, integer

F_SOURCE_SHA = "998c5bc89d1b35c9ad3047b1e50dcb1295120e31"
MAX_BYTES = 16 * 1024 * 1024
MAX_BARS = 2000
MAX_MARKERS = 500
MAX_TIME = 2**53 - 1


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _bounded_copy(value):
    raw = _canonical(value)
    if len(raw) > MAX_BYTES:
        raise ValueError("Research input/output exceeds bounded bytes")
    return json.loads(raw)


def _synthetic_metadata(value):
    """Qualify only synthetic names, never relabel or repair authentic evidence."""
    if isinstance(value, list):
        for item in value:
            _synthetic_metadata(item)
    elif isinstance(value, dict):
        if "coverage" in value:
            coverage = value["coverage"]
            if (not isinstance(coverage, dict) or coverage.get("status") != "COMPLETE"
                    or value.get("availability", {}).get("status") != "AVAILABLE"):
                raise ValueError("Only complete available synthetic evidence is admitted")
        if "evidence_refs" in value:
            refs = value["evidence_refs"]
            if (not isinstance(refs, list) or not refs
                    or not all(isinstance(ref, str) and ref.startswith(("fixture:", "synthetic:")) for ref in refs)):
                raise ValueError("Explicit synthetic evidence namespace required")
        if "source_kind" in value:
            if (value["source_kind"] != "synthetic_fixture"
                    or not isinstance(value.get("source_ref"), str)
                    or not value["source_ref"].startswith(("fixture:", "synthetic:"))):
                raise ValueError("Authentic/unknown sources cannot be certified as synthetic")
        for item in value.values():
            _synthetic_metadata(item)


def _price(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,30}(\.[0-9]{1,18})?", value)
            or not any(char in "123456789" for char in value)):
        raise ValueError("Price cannot be represented losslessly by research-chart/2")
    return value


def _relationships(result):
    """Resolve genuine producer IDs. Never manufacture a closed trade identity."""
    orders = {order["id"]: order for order in result["orders"]}
    fills = {fill["id"]: fill for fill in result["fills"]}
    trades = {trade["id"]: trade for trade in result["trades"]}
    if (len(orders) != len(result["orders"]) or len(fills) != len(result["fills"])
            or len(trades) != len(result["trades"])):
        raise ValueError("Duplicate producer identities")
    exits = {}
    for trade in result["trades"]:
        entry, exit_fill = fills[trade["entry_fill_id"]], fills[trade["exit_fill_id"]]
        if (entry["role"] != "entry" or exit_fill["role"] != "exit"
                or exit_fill["id"] in exits or entry["side"] != exit_fill["side"]):
            raise ValueError("Unresolved producer trade relationship")
        for field in ("price", "timestamp_ms"):
            name = "timestamp_ms" if field == "timestamp_ms" else "price"
            if trade["entry_" + name] != entry[field] or trade["exit_" + name] != exit_fill[field]:
                raise ValueError("Trade differs from its recorded fill")
        if trade["quantity"] != exit_fill["quantity"]:
            raise ValueError("Trade quantity differs from its recorded exit")
        exits[exit_fill["id"]] = trade
    links = {}
    for fill in result["fills"]:
        order = orders[fill["order_id"]]
        if order["role"] != fill["role"] or order["side"] != fill["side"]:
            raise ValueError("Order/fill relationship differs")
        if fill["role"] == "entry":
            links[fill["id"]] = ("position:" + fill["id"], None)
        elif fill["role"] == "exit" and fill["id"] in exits:
            trade = exits[fill["id"]]
            links[fill["id"]] = ("position:" + trade["entry_fill_id"], trade["id"])
        else:
            raise ValueError("Fill has no genuine position/trade relationship")
    opened = result["open_position"]
    if opened is not None and fills[opened["entry_fill_id"]]["role"] != "entry":
        raise ValueError("Open position has no genuine entry")
    overlay_by_fill = {marker["fill_id"]: marker for marker in result["overlays"]}
    if len(overlay_by_fill) != len(result["overlays"]) or set(overlay_by_fill) != set(fills):
        raise ValueError("Producer overlays do not account for all fills")
    for fill_id, fill in fills.items():
        for field in ("order_id", "timestamp_ms", "known_at_ms", "time_window", "time_precision",
                      "price", "quantity", "side", "role", "reason"):
            if overlay_by_fill[fill_id][field] != fill[field]:
                raise ValueError("Producer overlay changes fill semantics")
    return links


def _project(snapshot, result, *, start_ms, end_ms, cutoff_ms):
    for value in (start_ms, end_ms, cutoff_ms):
        integer(value, 0, MAX_TIME)
    if not snapshot["start_ms"] <= start_ms < end_ms <= snapshot["end_ms"]:
        raise ValueError("Projection range outside immutable snapshot")
    if end_ms - start_ms > 366 * 86400000:
        raise ValueError("Projection exceeds bounded view")
    links = _relationships(result)
    markers = []
    for fill in result["fills"]:
        if not start_ms <= fill["timestamp_ms"] < end_ms or fill["known_at_ms"] > cutoff_ms:
            continue
        position, closed_trade = links[fill["id"]]
        buying = (fill["side"], fill["role"]) in (("long", "entry"), ("short", "exit"))
        markers.append(dict(fill_id=fill["id"], order_id=fill["order_id"], position_id=position,
            closed_trade_id=closed_trade, role=fill["role"], side="buy" if buying else "sell",
            timestamp_ms=fill["timestamp_ms"], known_at_ms=fill["known_at_ms"],
            window_start_ms=fill["time_window"]["start_ms"], window_end_ms=fill["time_window"]["end_ms"],
            time_precision=fill["time_precision"], price=_price(fill["price"])))
    if len(markers) > MAX_MARKERS:
        raise ValueError("View has more than 500 fills; no silent truncation")
    candles = []
    for candle in snapshot["candles"]:
        if start_ms <= candle["start_ms"] and candle["end_ms"] <= min(end_ms, cutoff_ms):
            candles.append(dict(start_ms=candle["start_ms"], end_ms=candle["end_ms"],
                **{field: _price(candle[field]) for field in ("open", "high", "low", "close")}))
    return dict(schema_version="tester.research-chart/2", execution_mode="SIMULATED",
        run_id=result["run_id"], snapshot_id="synthetic:" + result["market_snapshot_hash"],
        revision=F_SOURCE_SHA, venue=snapshot["venue"], category=snapshot["category"],
        symbol=snapshot["symbol"], interval=snapshot["candles"][0]["interval"],
        start_ms=start_ms, end_ms=end_ms, cutoff_ms=cutoff_ms,
        coverage="complete" if cutoff_ms >= end_ms else "partial",
        synthetic=True, source_ids=["synthetic:" + digest(snapshot["provenance"])],
        candles=candles, markers=markers)


def run_synthetic_research(strategy: dict, snapshot: dict) -> dict:
    """Run genuine F against qualified synthetic evidence, with no I/O side effects."""
    strategy, snapshot = _bounded_copy(strategy), _bounded_copy(snapshot)
    if (not isinstance(snapshot.get("candles"), list) or not 1 <= len(snapshot["candles"]) <= MAX_BARS
            or not isinstance(snapshot.get("symbol"), str)
            or not re.fullmatch(r"[A-Z0-9_-]{1,40}", snapshot["symbol"])):
        raise ValueError("Bounded candles and representable market identity required")
    _synthetic_metadata(snapshot)
    result = run(strategy, snapshot)  # Exact vendored F engine: sole financial authority.
    if result["data_quality"]["gaps"]:
        raise ValueError("Synthetic grid gaps cannot certify complete coverage")
    if not {"SYNTHETIC_DATA", "NON_CERTIFIED"} <= set(result["warnings"]):
        raise ValueError("Producer did not retain explicit synthetic uncertainty")
    chart = _project(snapshot, result, start_ms=snapshot["start_ms"],
                     end_ms=snapshot["end_ms"], cutoff_ms=snapshot["end_ms"])
    bundle = dict(schema_version="tester.strategy-research-bundle/1", source_sha=F_SOURCE_SHA,
        engine_version=ENGINE_VERSION, synthetic=True, strategy=strategy, snapshot=snapshot,
        strategy_digest=digest(strategy), snapshot_digest=digest(snapshot), result=result,
        result_digest=digest(result), chart=chart, chart_digest=digest(chart),
        certification=dict(scope="SYNTHETIC_ONLY", real_market_coverage="NOT_PROVEN"))
    return _bounded_copy(bundle)


def run_certified_fixture() -> dict:
    """Load packaged exact producer fixtures and execute F, never a static result."""
    root = Path(__file__).parent
    pin = json.loads((root / "F_SOURCE_PIN.json").read_bytes())
    if pin["source_sha"] != F_SOURCE_SHA:
        raise ValueError("Producer source pin differs")
    values = []
    for name in ("example.strategy.json", "example.snapshot.json"):
        relative = "src/integration/fixtures/f998/" + name
        raw = (root / "fixtures/f998" / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != pin["files"][relative]["sha256"]:
            raise ValueError("Published synthetic fixture hash differs")
        values.append(json.loads(raw))
    return run_synthetic_research(*values)


def verify_research_bundle(bundle: dict) -> None:
    """Reject tamper, caller-rehash, source/scope drift via genuine deterministic replay."""
    value = _bounded_copy(bundle)
    if not isinstance(value, dict) or value.get("schema_version") != "tester.strategy-research-bundle/1":
        raise ValueError("Unknown research bundle")
    try:
        expected = run_synthetic_research(value["strategy"], value["snapshot"])
    except (KeyError, TypeError) as exc:
        raise ValueError("Missing immutable research inputs") from exc
    if value != expected:
        raise ValueError("Research bundle does not match genuine producer replay")


def serialize_research_bundle(bundle: dict) -> bytes:
    verify_research_bundle(bundle)
    return _canonical(bundle)


def project_research_chart(bundle: dict, *, start_ms=None, end_ms=None, cutoff_ms=None) -> dict:
    verify_research_bundle(bundle)
    snapshot = bundle["snapshot"]
    return copy.deepcopy(_project(snapshot, bundle["result"],
        start_ms=snapshot["start_ms"] if start_ms is None else start_ms,
        end_ms=snapshot["end_ms"] if end_ms is None else end_ms,
        cutoff_ms=snapshot["end_ms"] if cutoff_ms is None else cutoff_ms))
