"""Synthetic contract harness; no database, network, installed runtime or engine."""

import json
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.research_control import (
    Artifact,
    Command,
    Control,
    Denied,
    Identity,
    Marker,
    Unavailable,
    canonical_digest,
    create_control_app,
)


class Store:
    """Test double ONLY. Production requires PostgreSQL atomic ledger/leases/audit."""

    def __init__(self):
        self.rows = {}
        self.audit = []

    def submit(self, actor, command, digest):
        key = (actor, str(command.request_id))
        if key in self.rows:
            if self.rows[key]["digest"] != digest:
                raise ValueError("idempotency conflict")
            return deepcopy(self.rows[key])
        row = dict(
            job_id=str(uuid4()),
            actor=actor,
            command=command.model_dump(mode="json"),
            digest=digest,
            artifact=None,
            state="pending",
        )
        self.rows[key] = row
        self.audit.append(("submitted", row["job_id"]))
        return deepcopy(row)

    def read(self, actor, job_id):
        return deepcopy(
            next(
                row
                for row in self.rows.values()
                if row["job_id"] == job_id and row["actor"] == actor
            )
        )

    def complete(self, actor, job_id, digest, artifact):
        row = next(row for row in self.rows.values() if row["job_id"] == job_id)
        assert row["actor"] == actor and row["digest"] == digest
        if row["artifact"] is not None and row["artifact"] != artifact:
            raise ValueError("immutable completion conflict")
        if row["artifact"] is None:
            row.update(
                artifact=deepcopy(artifact),
                artifact_digest=canonical_digest(artifact),
                state="complete",
            )
            self.audit.append(("completed", job_id))
        return deepcopy(row)

    def acknowledge(self, actor, job_id, digest, rendered_fill_ids):
        row = self.read(actor, job_id)
        event = ("render_ack", job_id, digest)
        if event not in self.audit:
            self.audit.append(event)
        return row


@pytest.fixture
def harness():
    store = Store()
    allowed = {"alice"}
    identity = Identity(actor="alice")

    def authorize(actor, command):
        if actor not in allowed:
            raise Denied("revoked")

    command = Command(
        request_id=str(uuid4()),
        expected_artifact_digest="0" * 64,
        action="chart.overlay",
        venue="bybit",
        category="linear",
        interval="1",
        run_id="run-1",
        snapshot_id="snapshot-1",
        revision="a" * 40,
        symbol="BTCUSDT",
        start_ms=100,
        end_ms=200,
        cutoff_ms=200,
    )
    artifact = dict(
        schema_version="tester.research-chart/2",
        execution_mode="SIMULATED",
        run_id="run-1",
        snapshot_id="snapshot-1",
        revision="a" * 40,
        venue="bybit",
        category="linear",
        interval="1",
        symbol="BTCUSDT",
        start_ms=100,
        end_ms=200,
        cutoff_ms=200,
        coverage="partial",
        source_ids=["synthetic:source"],
        synthetic=True,
        candles=[],
        markers=[
            dict(
                role="entry",
                side="buy",
                fill_id="fill-1",
                order_id="order-1",
                position_id="position-1",
                closed_trade_id=None,
                timestamp_ms=150,
                known_at_ms=190,
                window_start_ms=140,
                window_end_ms=190,
                time_precision="bar",
                price="1.2300",
            )
        ],
    )
    command = command.model_copy(update={"expected_artifact_digest": canonical_digest(artifact)})

    def provider(cmd):
        return Artifact.model_validate(deepcopy(artifact))

    control = Control(store=store, authorize=authorize, provider=provider)
    return control, store, identity, command, artifact, allowed


def test_synthetic_submit_restart_result_ack_and_audit(harness):
    control, store, actor, command, artifact, allowed = harness
    job = control.submit(actor, command)
    assert control.submit(actor, command)["job_id"] == job["job_id"]
    restarted = Control(store=store, authorize=control.authorize, provider=control.provider)
    restarted.dispatch(actor, job["job_id"])
    restarted.dispatch(actor, job["job_id"])
    result = restarted.result(actor, job["job_id"])
    assert result["artifact"] == artifact
    assert result["artifact"]["markers"][0]["closed_trade_id"] is None
    assert result["artifact"]["coverage"] == "partial"
    digest = canonical_digest(artifact)
    restarted.ack(actor, job["job_id"], digest, ["fill-1"])
    restarted.ack(actor, job["job_id"], digest, ["fill-1"])
    assert [x[0] for x in store.audit] == ["submitted", "completed", "render_ack"]


def test_idempotency_conflict_and_actor_isolation(harness):
    control, store, actor, command, *_ = harness
    job = control.submit(actor, command)
    with pytest.raises(ValueError):
        control.submit(actor, command.model_copy(update={"symbol": "ETHUSDT"}))
    with pytest.raises(Denied):
        control.result(Identity(actor="mallory"), job["job_id"])


def test_revocation_checked_before_dispatch_replay_result_and_ack(harness):
    control, store, actor, command, artifact, allowed = harness
    job = control.submit(actor, command)
    allowed.clear()
    for operation in [
        lambda: control.dispatch(actor, job["job_id"]),
        lambda: control.submit(actor, command),
        lambda: control.result(actor, job["job_id"]),
        lambda: control.ack(actor, job["job_id"], canonical_digest(artifact), ["fill-1"]),
    ]:
        with pytest.raises(Denied):
            operation()
    assert len(store.audit) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"snapshot_id": "wrong"},
        {"run_id": "wrong"},
        {"revision": "b" * 40},
        {"symbol": "ETHUSDT"},
        {"start_ms": 99},
        {"cutoff_ms": 201},
    ],
)
def test_provider_binding_mismatch_rejected(harness, change):
    control, store, actor, command, artifact, _ = harness
    artifact.update(change)
    job = control.submit(actor, command)
    with pytest.raises(ValueError):
        control.dispatch(actor, job["job_id"])
    assert store.read(actor.actor, job["job_id"])["state"] == "pending"


@pytest.mark.parametrize(
    "change",
    [
        {"known_at_ms": 201},
        {"time_precision": "exact"},
        {"price": "NaN"},
        {"window_start_ms": 160},
        {"order_id": ""},
    ],
)
def test_uncertainty_and_fill_contract_rejected(harness, change):
    control, store, actor, command, artifact, _ = harness
    artifact["markers"][0].update(change)
    job = control.submit(actor, command)
    with pytest.raises(ValueError):
        control.dispatch(actor, job["job_id"])


def test_digest_corruption_and_ack_mismatch(harness):
    control, store, actor, command, artifact, _ = harness
    job = control.submit(actor, command)
    with pytest.raises(ValueError):
        control.ack(actor, job["job_id"], canonical_digest(artifact), ["fill-1"])
    control.dispatch(actor, job["job_id"])
    with pytest.raises(ValueError):
        control.ack(actor, job["job_id"], "0" * 64, ["fill-1"])
    next(iter(store.rows.values()))["artifact"]["symbol"] = "ETHUSDT"
    with pytest.raises(ValueError):
        control.result(actor, job["job_id"])


def test_api_auth_origin_host_typed_input_and_unavailable(harness):
    control, store, actor, command, *_ = harness

    def authenticate(token):
        if token != "Bearer fixture":
            raise Denied("invalid credentials")
        return actor

    client = TestClient(create_control_app(control, authenticate), base_url="http://localhost")
    body = command.model_dump(mode="json")
    assert client.post("/api/control/research", json=body).status_code == 401
    headers = {"Authorization": "Bearer fixture"}
    assert (
        client.post(
            "/api/control/research", json=body, headers={**headers, "Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/control/research", json=body, headers={**headers, "Host": "evil.test"}
        ).status_code
        == 400
    )
    for bad in [{"action": "shell"}, {"actor": "mallory"}, {"sql": "SELECT 1"}]:
        assert (
            client.post("/api/control/research", json={**body, **bad}, headers=headers).status_code
            == 422
        )
    response = client.post("/api/control/research", json=body, headers=headers)
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    control.dispatch(actor, job_id)
    result = client.get(f"/api/control/research/{job_id}", headers=headers)
    assert result.status_code == 200
    ack = client.post(
        f"/api/control/research/{job_id}/ack",
        json={"artifact_digest": result.json()["artifact_digest"], "rendered_fill_ids": ["fill-1"]},
        headers=headers,
    )
    assert ack.status_code == 200
    unavailable = TestClient(create_control_app(None, authenticate), base_url="http://localhost")
    assert unavailable.post("/api/control/research", json=body, headers=headers).status_code == 503


def test_revocation_during_provider_resolution_does_not_commit(harness):
    control, store, actor, command, artifact, allowed = harness
    job = control.submit(actor, command)

    def revoke(cmd):
        allowed.clear()
        return artifact

    control.provider = revoke
    with pytest.raises(Denied):
        control.dispatch(actor, job["job_id"])
    assert len(store.audit) == 1


def test_unavailable_provider_and_command_tampering(harness):
    control, store, actor, command, *_ = harness
    job = control.submit(actor, command)
    control.provider = None
    with pytest.raises(Unavailable):
        control.dispatch(actor, job["job_id"])
    next(iter(store.rows.values()))["command"]["symbol"] = "ETHUSDT"
    with pytest.raises(ValueError, match="command digest"):
        control.dispatch(actor, job["job_id"])
    assert len(store.audit) == 1


@pytest.mark.parametrize("mode", ["duplicate", "legacy_six_keys", "too_many", "missing_order"])
def test_reject_lossy_or_unbounded_chart_contract(harness, mode):
    control, store, actor, command, artifact, _ = harness
    if mode == "duplicate":
        artifact["markers"] *= 2
    elif mode == "too_many":
        artifact["markers"] *= 501
    elif mode == "missing_order":
        del artifact["markers"][0]["order_id"]
    else:
        artifact["markers"] = [
            dict(
                timestamp_ms=150,
                kind="fill",
                price="1.23",
                trade_id="invented-trade",
                role="entry",
                side="buy",
                fill_id="fill-1",
                reason="entry",
            )
        ]
    job = control.submit(actor, command)
    with pytest.raises(ValueError):
        control.dispatch(actor, job["job_id"])
    assert len(store.audit) == 1


def test_pinned_digest_rejects_valid_but_changed_artifact(harness):
    control, store, actor, command, artifact, _ = harness
    artifact["markers"][0]["price"] = "9.99"
    job = control.submit(actor, command)
    with pytest.raises(ValueError, match="pinned artifact digest"):
        control.dispatch(actor, job["job_id"])


def test_expired_or_revoked_credential_is_rejected_on_each_api_call(harness):
    control, store, actor, command, *_ = harness
    current = [True]

    def authenticate(token):
        if not current[0]:
            raise Denied("expired/revoked credential")
        return actor

    client = TestClient(create_control_app(control, authenticate), base_url="http://localhost")
    response = client.post("/api/control/research", json=command.model_dump(mode="json"))
    assert response.status_code == 202
    current[0] = False
    assert client.get("/api/control/research/" + response.json()["job_id"]).status_code == 401


@pytest.mark.parametrize("field", ["venue", "category", "interval"])
def test_market_identity_must_be_explicit(harness, field):
    _, _, _, command, *_ = harness
    body = command.model_dump(mode="json")
    del body[field]
    with pytest.raises(ValueError):
        Command.model_validate_json(json.dumps(body))


@pytest.mark.parametrize(
    "precision,timestamp,known",
    [
        ("modeled_open", 60000, 60000),
        ("bar", 119999, 120000),
    ],
)
def test_pinned_f_fill_timing_preserved(harness, precision, timestamp, known):
    # F 998c5bc engine.py: modeled open is known at start; bar fills at end-1.
    _, _, _, _, artifact, _ = harness
    marker = deepcopy(artifact["markers"][0])
    marker.update(
        window_start_ms=60000,
        window_end_ms=120000,
        timestamp_ms=timestamp,
        known_at_ms=known,
        time_precision=precision,
    )
    validated = Marker.model_validate(marker)
    assert validated.model_dump() == marker


@pytest.mark.parametrize(
    "precision,timestamp,known,start,end",
    [
        ("bar", 60000, 60000, 60000, 60000),  # empty window
        ("bar", 120000, 120000, 60000, 120000),  # end is excluded
        ("bar", 119999, 119999, 60000, 120000),  # unknown before bar close
        ("modeled_open", 60001, 120000, 60000, 120000),  # not modeled start
        ("modeled_open", 60000, 120000, 60000, 120000),  # invented delay
    ],
)
def test_reject_invalid_precision_specific_fill_timing(
    harness, precision, timestamp, known, start, end
):
    _, _, _, _, artifact, _ = harness
    marker = deepcopy(artifact["markers"][0])
    marker.update(
        window_start_ms=start,
        window_end_ms=end,
        timestamp_ms=timestamp,
        known_at_ms=known,
        time_precision=precision,
    )
    with pytest.raises(ValueError):
        Marker.model_validate(marker)
