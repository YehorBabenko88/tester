from copy import deepcopy

import pytest

from src.api.research_control import canonical_digest
from src.api.research_summary import project_summary
from src.integration.strategy_lab import run_certified_fixture


def test_exact_verified_f_metrics_history_brackets_and_digest_no_recalculation():
    bundle = run_certified_fixture()
    before = deepcopy(bundle)
    envelope = project_summary(bundle, "a" * 64)
    summary = envelope["summary"]
    assert summary["schema_version"] == "tester.research-summary/1"
    assert envelope["summary_digest"] == canonical_digest(summary)
    assert summary["input_digest"] == "a" * 64
    assert summary["artifact_digest"] == bundle["chart_digest"]
    assert summary["run_id"] == bundle["result"]["run_id"]
    for field in ("metrics", "trades", "equity_curve", "brackets"):
        assert summary[field] == bundle["result"][field]
    assert summary["metrics"]["profit_factor"] == "0E+5"
    assert summary["certification"] == {
        "scope": "SYNTHETIC_ONLY",
        "real_market_coverage": "NOT_PROVEN",
    }
    assert bundle == before


def test_levels_bind_real_entry_fill_window_and_copied_trade_sl_tp_only():
    bundle = run_certified_fixture()
    levels = project_summary(bundle, "a" * 64)["summary"]["levels"]
    assert len(levels) == 2
    trade = bundle["result"]["trades"][0]
    entry = next(m for m in bundle["chart"]["markers"] if m["fill_id"] == trade["entry_fill_id"])
    for level in levels:
        assert set(level) == {"entry_fill_id", "kind", "price", "start_ms", "end_ms", "known_at_ms"}
        assert level["entry_fill_id"] == entry["fill_id"]
        assert level["start_ms"] == entry["window_start_ms"]
        assert level["end_ms"] == entry["window_end_ms"]
        assert level["known_at_ms"] == entry["known_at_ms"]
        assert level["price"] == trade["stop_loss" if level["kind"] == "sl" else "take_profit"]


@pytest.mark.parametrize("field", ["chart_digest", "source_sha", "result_digest"])
def test_changed_bundle_binding_is_rejected(field):
    bundle = run_certified_fixture()
    bundle[field] = "0" * len(bundle[field])
    with pytest.raises(ValueError):
        project_summary(bundle, "a" * 64)


def test_rehashed_fabricated_metrics_never_become_verified_summary():
    bundle = run_certified_fixture()
    bundle["result"]["metrics"]["net_realized_pnl"] = "999999"
    bundle["result_digest"] = canonical_digest(bundle["result"])
    with pytest.raises(ValueError):
        project_summary(bundle, "a" * 64)


def test_presentation_only_dedup_preserves_identical_partial_exit_levels_and_rejects_conflict():
    from src.api.research_summary import _levels

    bundle = run_certified_fixture()
    result = deepcopy(bundle["result"])
    expected = _levels(result, bundle["chart"])
    result["trades"].append(deepcopy(result["trades"][0]))
    assert _levels(result, bundle["chart"]) == expected
    assert len(result["trades"]) == 2
    result["trades"][-1]["stop_loss"] = "90"
    with pytest.raises(ValueError, match="conflicting"):
        _levels(result, bundle["chart"])


def test_g_summary_requires_same_input_run_chart_digest_and_source():
    from uuid import uuid4

    from src.api.research_execution import chart_command
    from src.api.research_execution_g import ExecutionGatewayClient

    bundle = run_certified_fixture()
    command = chart_command(bundle, str(uuid4()))
    envelope = project_summary(bundle, "a" * 64)
    gateway = ExecutionGatewayClient("http://127.0.0.1:18768", "t" * 40)
    gateway._call = lambda *args: envelope
    job_id = str(uuid4())
    assert gateway.execution_summary(job_id, input_digest="a" * 64, command=command) == envelope
    with pytest.raises(ValueError):
        gateway.execution_summary(job_id, input_digest="b" * 64, command=command)
    for field in ("artifact_digest", "run_id", "source_sha"):
        bad = deepcopy(envelope)
        bad["summary"][field] = "0" * len(bad["summary"][field])
        bad["summary_digest"] = canonical_digest(bad["summary"])
        gateway._call = lambda *args: bad
        with pytest.raises(ValueError):
            gateway.execution_summary(job_id, input_digest="a" * 64, command=command)
