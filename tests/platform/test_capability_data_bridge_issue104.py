"""Issue #104: /api/capabilities execution probe is bounded and fails closed."""

from types import SimpleNamespace

from src.api import capability_data_bridge as bridge
from tests.test_execution_contract_bridge import END, START, compact_store, fixture


def query(**overrides):
    values = dict(
        category="linear",
        symbol="BTCUSDT",
        start_ms=START,
        end_ms=END,
        information_cutoff_ms=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_probe_uses_resource_bounded_read_with_tight_budget():
    store = compact_store([fixture()])
    result = bridge.observed_execution_evidence(SimpleNamespace(store=store), query())
    assert result["observed_events"] == 1
    assert result["status"] == "UNVERIFIED"
    assert result["admitted_to_f"] is False
    assert store.read_bounded.call_args.kwargs["budget"] == bridge._PROBE_BUDGET


def test_probe_budget_never_exceeds_contract_ceiling():
    from src.ingest.execution_contract import READ_BUDGET

    assert set(bridge._PROBE_BUDGET) == set(READ_BUDGET)
    assert all(0 < bridge._PROBE_BUDGET[k] <= READ_BUDGET[k] for k in READ_BUDGET)


def test_probe_refuses_large_evidence_and_observation_fanout():
    large = fixture()
    evidence = large["provenance"]["observations"][0]["evidence"]
    evidence["padding"] = "x" * (bridge._PROBE_BUDGET["max_evidence_bytes"] + 1)
    fanout = fixture()
    fanout["provenance"]["observations"] *= bridge._PROBE_BUDGET["max_observations_per_record"] + 1
    for rows, name in (([large], "max_evidence_bytes"), ([fanout], "max_observations_per_record")):
        result = bridge.observed_execution_evidence(
            SimpleNamespace(store=compact_store(rows)), query()
        )
        assert result["status"] == "UNVERIFIED"
        assert result["reason"] == "data72_read_budget_exceeded"
        assert result["budget"] == name
        assert "observed_events" not in result and result["admitted_to_f"] is False


def test_probe_store_failure_is_unverified_not_http_500():
    class Broken:
        def read_bounded(self, *args, **kwargs):
            raise MemoryError("simulated")

    result = bridge.observed_execution_evidence(SimpleNamespace(store=Broken()), query())
    assert result["reason"] == "data72_source_readback_unavailable"


def test_probe_without_store_or_scope_never_reads():
    assert (
        bridge.observed_execution_evidence(SimpleNamespace(), query())["reason"]
        == "data72_storage_reader_not_connected"
    )
    store = compact_store([fixture()])
    reader = SimpleNamespace(store=store)
    wide = query(end_ms=START + 8 * 86400000)
    assert (
        bridge.observed_execution_evidence(reader, wide)["reason"]
        == "data72_bounded_window_required"
    )
    store.read_bounded.assert_not_called()


def test_probe_reads_inverse_with_its_contract_unit_and_refuses_spot():
    # Before: every non-linear query returned linear_quantity_required.
    store = compact_store([fixture(category="inverse")])
    result = bridge.observed_execution_evidence(
        SimpleNamespace(store=store), query(category="inverse")
    )
    assert result["observed_events"] == 1
    assert result["quantity_unit"] == "source_native_inverse_contract"
    assert result["admitted_to_f"] is False
    assert store.read_bounded.call_args.args[2] == "inverse"
    linear = bridge.observed_execution_evidence(
        SimpleNamespace(store=compact_store([fixture()])), query()
    )
    assert linear["quantity_unit"] == "source_native_linear_size"
    spot_store = compact_store([fixture()])
    spot = bridge.observed_execution_evidence(
        SimpleNamespace(store=spot_store), query(category="spot")
    )
    assert spot["reason"] == "execution_category_unit_unsupported"
    spot_store.read_bounded.assert_not_called()


def test_probe_refuses_events_with_another_category_unit():
    from src.ingest.execution_contract import project_storage_execution

    event = project_storage_execution(
        fixture(category="inverse"),
        category="inverse",
        symbol="BTCUSDT",
        start_ms=START,
        end_ms=END,
    )

    def read_events(store, **kwargs):
        return [dict(event, quantity_unit=unit)]

    unit = event["quantity_unit"]
    ok = bridge.observed_execution_evidence(
        SimpleNamespace(store=object()), query(category="inverse"), read_events=read_events
    )
    assert ok["observed_events"] == 1
    unit = "source_native_linear_size"
    result = bridge.observed_execution_evidence(
        SimpleNamespace(store=object()), query(category="inverse"), read_events=read_events
    )
    assert result["reason"] == "data72_event_contract_invalid"


def test_probe_refuses_producer_unit_skew(monkeypatch):
    fake = SimpleNamespace(
        VERSION=bridge.DATA_EXECUTION_VERSION,
        SOURCE_QUANTITY_UNITS={"inverse": "source_native_linear_size"},
        read_execution_events=lambda *a, **k: [],
    )
    monkeypatch.setattr(bridge.importlib, "import_module", lambda name: fake)
    store = compact_store([fixture(category="inverse")])
    result = bridge.observed_execution_evidence(
        SimpleNamespace(store=store), query(category="inverse")
    )
    assert result["reason"] == "data72_quantity_unit_skew"
    store.read_bounded.assert_not_called()
