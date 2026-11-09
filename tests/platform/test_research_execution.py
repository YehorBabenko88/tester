import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from src.api.research_control import canonical_digest
from src.api.research_execution import ExecutionRequest, chart_command, execute_input, prepare_input


def inputs():
    root = Path(__file__).parents[2] / "src/integration/fixtures/f998"
    strategy = json.loads((root / "example.strategy.json").read_text())
    snapshot = json.loads((root / "example.snapshot.json").read_text())
    qualified = dict(
        mode="SYNTHETIC",
        admitted=True,
        snapshot=snapshot,
        snapshot_digest=canonical_digest(snapshot),
        dataset_revision="a" * 64,
        snapshot_id="synthetic:" + canonical_digest(snapshot),
        evidence_digest="b" * 64,
    )
    return strategy, qualified


def test_request_has_no_actor_strategy_data_or_code_and_requires_canonical_uuid():
    base = dict(request_id=str(uuid4()), input_digest="a" * 64)
    ExecutionRequest(**base)
    for extra in [
        dict(actor="operator"),
        dict(strategy={}),
        dict(snapshot={}),
        dict(code="eval(1)"),
    ]:
        with pytest.raises(ValueError):
            ExecutionRequest(**base, **extra)
    with pytest.raises(ValueError):
        ExecutionRequest(**{**base, "request_id": "not-a-uuid"})


@pytest.mark.parametrize(
    "change",
    [
        dict(mode="REAL"),
        dict(admitted=False),
        dict(snapshot=None),
        dict(snapshot_digest="0" * 64),
        dict(snapshot_id="unverified:" + "a" * 64),
    ],
)
def test_unqualified_unknown_real_or_hash_mismatch_never_reaches_f(change):
    strategy, qualified = inputs()
    qualified.update(change)
    with pytest.raises(ValueError):
        prepare_input(strategy, qualified)


def test_actual_f_execution_and_chart_binding_preserve_input_and_explicit_uncertainty():
    strategy, qualified = inputs()
    original = deepcopy((strategy, qualified))
    value = prepare_input(strategy, qualified)
    bundle = execute_input(value)
    command = chart_command(bundle, str(uuid4()))
    assert command.expected_artifact_digest == bundle["chart_digest"]
    assert command.snapshot_id == qualified["snapshot_id"]
    assert bundle["certification"]["scope"] == "SYNTHETIC_ONLY"
    assert bundle["result"]["fills"]
    assert (strategy, qualified) == original


def test_unknown_coverage_and_tampered_inputs_fail_closed():
    strategy, qualified = inputs()
    qualified["snapshot"]["coverage"]["status"] = "UNKNOWN"
    qualified["snapshot_digest"] = canonical_digest(qualified["snapshot"])
    qualified["snapshot_id"] = "synthetic:" + qualified["snapshot_digest"]
    with pytest.raises(ValueError):
        prepare_input(strategy, qualified)


def test_factory_is_opt_in_and_does_not_open_database_or_migrate(monkeypatch):
    from src.api.research_execution import ExecutionLedger, create_execution_app
    from src.api.research_ledger import PersistentResearchLedger

    def refuse(*args, **kwargs):
        raise AssertionError("factory must not connect")

    monkeypatch.setattr(PersistentResearchLedger, "connection", refuse)
    monkeypatch.setattr(ExecutionLedger, "connection", refuse)
    app = create_execution_app("host=127.0.0.1 port=55472 dbname=tester_research_combined_ci")
    paths = {route.path for route in app.routes}
    assert "/api/control/research-executions" in paths
    assert "/api/control/research/{job_id}/ack" in paths


def test_g_execution_binding_rejects_wrong_job_unknown_state_or_fake_certification():
    from src.api.research_execution_g import ExecutionGatewayClient

    client = ExecutionGatewayClient("http://127.0.0.1:18768", "t" * 40)
    job = str(uuid4())
    client._call = lambda *args: dict(job_id=job, state="pending")
    assert client.execution_result(job)["state"] == "pending"
    with pytest.raises(ValueError):
        client.execution_result(str(uuid4()))
    bundle = execute_input(prepare_input(*inputs()))
    result = dict(
        job_id=job,
        state="complete",
        chart_command=chart_command(bundle, job).model_dump(),
        certification=dict(scope="SYNTHETIC_ONLY", real_market_coverage="NOT_PROVEN"),
    )
    client._call = lambda *args: result
    assert client.execution_result(job) == result
    for field in ("scope", "real_market_coverage"):
        bad = deepcopy(result)
        bad["certification"][field] = "VERIFIED"
        client._call = lambda *args: bad
        with pytest.raises(ValueError):
            client.execution_result(job)
