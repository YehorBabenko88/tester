"""Isolated opt-in probe of hash-verified published F source; no app binding."""

import copy
import json
import sys
from decimal import Decimal
from pathlib import Path


def probe(root, fixtures, output):
    sys.path.insert(0, str(root))
    from src.backtest import Backtest, restore, run
    from src.backtest.artifacts import load_artifact, save_artifact

    strategy = json.loads((fixtures / "example.strategy.json").read_text())
    snapshot = json.loads((fixtures / "example.snapshot.json").read_text())
    result = run(strategy, snapshot)
    assert result == run(strategy, snapshot)
    trade = result["trades"][0]
    assert trade["reason"] == "stop_loss"
    assert Decimal(trade["exit_price"]) == Decimal("95")
    assert Decimal(trade["net_pnl"]) == Decimal("-5.195")
    # The fixture may re-enter after its closed trade; account for that separately.
    assert sum(Decimal(f["fee"]) for f in result["fills"][:2]) == Decimal("0.195")
    assert result["fills"][0]["timestamp_ms"] == 60000
    assert {"SYNTHETIC_DATA", "NON_CERTIFIED"} <= set(result["warnings"])

    backtest = Backtest(strategy, snapshot)
    backtest.step()
    checkpoint = json.loads(json.dumps(backtest.checkpoint()))
    resumed = restore(strategy, snapshot, checkpoint)
    while resumed.step():
        pass
    assert resumed.result() == result
    future = copy.deepcopy(snapshot)
    future["candles"][-1]["close"] = "100.5"
    changed = Backtest(strategy, future)
    changed.step()
    assert changed.checkpoint()["prefix_digest"] == checkpoint["prefix_digest"]
    assert changed.result()["signals"] == backtest.result()["signals"]

    # This is portable JSON persistence only; B/E canonical persistence is unbound.
    path = save_artifact(result, strategy, snapshot, output)
    assert save_artifact(result, strategy, snapshot, output) == path
    assert load_artifact(path)["result"] == result
    altered = json.loads(Path(path).read_text())
    altered["payload"]["result"]["fills"][0]["price"] = "999"
    Path(path).write_text(json.dumps(altered))
    try:
        load_artifact(path)
    except ValueError:
        pass
    else:
        raise AssertionError("Tampered persisted artifact accepted")
    try:
        save_artifact(result, strategy, future, output)
    except ValueError:
        pass
    else:
        raise AssertionError("Changed snapshot accepted for persisted run")

    # TP-only bar and unclosed position remain explicitly synthetic derivations.
    tp = copy.deepcopy(snapshot)
    tp["candles"][1]["low"] = "99"
    assert run(strategy, tp)["trades"][0]["reason"] == "take_profit"
    open_snapshot = copy.deepcopy(snapshot)
    for candle in open_snapshot["candles"]:
        candle.update(open="100", high="101", low="99", close="100")
    opened = run(strategy, open_snapshot)
    assert opened["trades"] == [] and opened["open_position"] is not None
    assert opened["fills"] and opened["overlays"]
    for candidate in (result, opened):
        assert len(candidate["fills"]) == len(candidate["overlays"])
        assert len({m["fill_id"] for m in candidate["overlays"]}) == len(candidate["fills"])
        fills = {f["id"]: f for f in candidate["fills"]}
        for marker in candidate["overlays"]:
            fill = fills[marker["fill_id"]]
            for key in ("order_id", "timestamp_ms", "known_at_ms", "time_window",
                        "time_precision", "price", "quantity", "role", "reason"):
                assert marker[key] == fill[key]
            assert "trade_id" not in marker  # Never invent a closed trade identity.
    uncertain = next(f for f in result["fills"] if f["role"] == "exit")
    assert uncertain["time_precision"] == "bar"
    assert uncertain["known_at_ms"] == uncertain["time_window"]["end_ms"]
    assert uncertain["timestamp_ms"] < uncertain["known_at_ms"]
    print(json.dumps(dict(classification="SYNTHETIC_ONLY", replay="PASS",
                         portable_artifact="PASS", fill_overlay_correspondence="PASS",
                         canonical_persistence="NOT_INTEGRATED", g_overlay="NOT_INTEGRATED")))


if __name__ == "__main__":
    probe(*(Path(arg) for arg in sys.argv[1:]))
