"""Regression against the published, synthetic-only F998 producer pin.

This is an actual engine runtime check, not a trading certification. The
historic source/fixture hashes are committed independently in F_SOURCE_PIN.json.
A *separate archived output replay_digest* is still required for full acceptance.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from src.backtest import Backtest, restore, run
from src.integration.qualified_snapshot import (
    certified_terminal_fixture_snapshot,
    certified_terminal_fixture_strategy,
)
from src.integration.strategy_lab import run_certified_fixture, run_synthetic_research
from src.analytics.simulation_economics import hypothetical_mark

ROOT = Path(__file__).resolve().parents[2]
PIN = ROOT / "src/integration/F_SOURCE_PIN.json"


def _assert_legacy_files_pinned():
    pin = json.loads(PIN.read_text(encoding="utf-8"))
    assert pin["source_sha"] == "998c5bc89d1b35c9ad3047b1e50dcb1295120e31"
    for relative, entry in pin["files"].items():
        # C's follow-up deliberately adds tests and never redefines the F
        # production engine. Existing producer fixtures are independently pinned.
        if relative.startswith(("src/backtest/", "src/strategy/",
                               "src/integration/fixtures/f998/")):
            file = ROOT / relative
            assert file.is_file(), relative
            data = file.read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], relative
    # SHA-1 is the Git object identity, not a signature or security primitive.
    raw = (ROOT / "src/backtest/engine.py").read_bytes()
    git_blob = b"blob " + str(len(raw)).encode("ascii") + bytes([0]) + raw
    assert hashlib.sha1(git_blob).hexdigest() == (
        "30b5f60736e1df738793598441142a6aad54e079"
    )


@pytest.mark.parametrize("category", ["spot", "linear"])
def test_historical_f998_runtime_replay_survives_isolated_v2_math(category):
    """Run both historical fixture classes on the exact published v1 engine.

    The golden source SHA and fixture bytes predate hypothetical_mark. Compare
    actual runtime replay_digest, not merely serialized source or PnL numbers.
    This only proves repeatability under a pinned historical producer, NOT
    equality to a separately archived output-digest manifest.
    """
    _assert_legacy_files_pinned()
    if category == "spot":
        baseline = run_certified_fixture()
    else:
        snapshot = certified_terminal_fixture_snapshot().snapshot
        strategy = certified_terminal_fixture_strategy()
        baseline = run_synthetic_research(strategy, snapshot)
    strategy = copy.deepcopy(baseline["strategy"])
    snapshot = copy.deepcopy(baseline["snapshot"])
    expected = baseline["result"]
    assert expected["engine_version"] == baseline["engine_version"] == "strategy-bar-1.1.0"
    assert expected["status"] == "completed"
    digest = expected["replay_digest"]
    assert isinstance(digest, str) and len(digest) == 64
    assert all(ch in "0123456789abcdef" for ch in digest)

    # The v2 research-only calculator cannot mutate engine global state.
    probe = hypothetical_mark(
        "inverse", "long", "100", "100", "200", leverage="10"
    )
    assert probe["admitted_to_f"] is False
    assert probe["liquidation_status"] == "NOT_MODELED"

    fresh = run(copy.deepcopy(strategy), copy.deepcopy(snapshot))
    assert fresh["replay_digest"] == digest
    assert fresh == expected

    state = Backtest(copy.deepcopy(strategy), copy.deepcopy(snapshot))
    assert state.step()
    token = json.loads(json.dumps(state.checkpoint()))
    resumed = restore(copy.deepcopy(strategy), copy.deepcopy(snapshot), token)
    while resumed.step():
        pass
    assert resumed.result()["replay_digest"] == digest
    assert resumed.result() == expected
    assert resumed.checkpoint()["prefix_digest"] == digest

    # No historical inverse strategy admission can be manufactured by v2.
    inverse = copy.deepcopy(snapshot)
    inverse["category"] = "inverse"
    for row in inverse["candles"]:
        row["category"] = "inverse"
    with pytest.raises(ValueError, match="only spot and linear payoff supported"):
        run(strategy, inverse)
