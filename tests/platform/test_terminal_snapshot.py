"""Explicit terminal SYNTHETIC LINEAR input; genuine F, no PostgreSQL/network."""

import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.integration.qualified_snapshot import (
    certified_fixture_snapshot,
    certified_terminal_fixture_snapshot,
    certified_terminal_fixture_strategy,
    verify_qualified_snapshot,
)
from src.integration.snapshot_repository import QualifiedSnapshotRepository
from src.integration.strategy_lab import run_synthetic_research, verify_research_bundle


def test_distinct_pinned_linear_fixture_runs_genuine_f_with_hypothetical_funding():
    original = certified_fixture_snapshot()
    value = certified_terminal_fixture_snapshot()
    strategy = certified_terminal_fixture_strategy()
    assert original.snapshot["category"] == "spot"
    assert value.snapshot_id != original.snapshot_id
    assert value.snapshot["category"] == "linear"
    assert value.snapshot["symbol"] == "BTCUSDT"
    assert strategy["costs"]["funding_policy"] == "required"
    assert value.certification == "SYNTHETIC_ONLY" and value.synthetic and value.admitted
    assert value.provenance["fixture_namespace"] == "synthetic:pc4-terminal-linear/v1"
    assert value.provenance["real_market_coverage"] == "NOT_PROVEN"
    bundle = run_synthetic_research(strategy, value.snapshot)
    verify_research_bundle(bundle)
    assert bundle["chart"]["category"] == "linear"
    assert bundle["chart"]["symbol"] == "BTCUSDT"
    assert bundle["chart"]["markers"]
    assert bundle["result"]["trades"]
    assert bundle["result"]["metrics"]["funding_costs"] == "0.0100"
    assert bundle["result"]["trades"][0]["reason"] == "take_profit"
    assert {"SYNTHETIC_DATA", "NON_CERTIFIED"} <= set(bundle["result"]["warnings"])
    assert "FUNDING_IGNORED" not in bundle["result"]["warnings"]
    assert verify_qualified_snapshot(value.as_dict()).as_dict() == value.as_dict()
    assert verify_qualified_snapshot(original.as_dict()).as_dict() == original.as_dict()


def test_relabel_or_caller_rehash_does_not_add_third_fixture():
    from src.strategy.dsl import digest

    for value in (certified_fixture_snapshot(), certified_terminal_fixture_snapshot()):
        document = value.as_dict()
        document["snapshot"]["candles"][0]["open"] = "100.5"
        document["snapshot_digest"] = digest(document["snapshot"])
        document["snapshot_id"] = "synthetic:" + document["snapshot_digest"]
        with pytest.raises(ValueError):
            verify_qualified_snapshot(document)
    strategy = certified_terminal_fixture_strategy()
    strategy["costs"]["funding_policy"] = "ignore_exploratory"
    assert certified_terminal_fixture_strategy()["costs"]["funding_policy"] == "required"


def test_repository_explicit_terminal_publish_and_read_never_overwrites(monkeypatch):
    value = certified_terminal_fixture_snapshot()
    conn = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    conn.execute.return_value.fetchone.return_value = dict(encoded=json.dumps(value.as_dict()))
    monkeypatch.setattr(QualifiedSnapshotRepository, "connection", lambda self: conn)
    repo = QualifiedSnapshotRepository("host=127.0.0.1 port=55472 dbname=tester_research_terminal")
    assert repo.publish_terminal_fixture().as_dict() == value.as_dict()
    assert "ON CONFLICT (snapshot_id) DO NOTHING" in conn.execute.call_args_list[0].args[0]
    assert repo.read(value.snapshot_id).as_dict() == value.as_dict()
    original = deepcopy(value.as_dict())
    assert original == value.as_dict()


def test_terminal_fixture_files_are_included_in_distribution():
    import fnmatch
    import tomllib
    from pathlib import Path

    project = Path(__file__).parents[2]
    config = tomllib.loads((project / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["src.integration"]
    for name in ("manifest.json", "strategy.json", "snapshot.json"):
        relative = "fixtures/pc4-terminal-linear-v1/" + name
        assert any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)
