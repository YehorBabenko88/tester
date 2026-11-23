"""Combined proposal-only integration and durable authority regressions."""

import hashlib
import json
from dataclasses import replace

import pytest

from src.api.guardians.code_guardian import CodeGuardian, Identity, Probe
from src.api.guardians.data_guardian import reconcile
from src.api.guardians.exchange_guardian import ExchangeGuardian, Observation
from src.api.guardians.storage_guardian import inventory
from src.api.guardians.supervisor import DurableSupervisor


def code_event(digest="a"):
    return dict(
        component="code",
        signal="service_unhealthy",
        scope="TesterAPI",
        environment="tester_preview",
        evidence_ref="sha256:" + digest * 64,
        ownership_verified=True,
        consecutive_failed_probes=3,
        restarts_last_hour=0,
    )


def test_reservation_cannot_move_admitted_scope(tmp_path):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True, restart_limit=1)
    first = s.submit(code_event())
    assert s.reserve(first, scope="other-service", owner="worker").reason == "scope_mismatch"
    lease = s.reserve(first, scope="TesterAPI", owner="worker")
    assert lease.status == "RESERVED"
    assert s.finish(lease, scope="TesterAPI", owner="worker").status == "CLOSED"
    second = s.submit(code_event("b"))
    assert s.reserve(second, scope="another-service", owner="worker").reason == "scope_mismatch"
    assert s.reserve(second, scope="TesterAPI", owner="worker").reason == "restart_budget"
    s.close()


def test_scope_alias_cannot_bypass_active_lease(tmp_path):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    first = s.submit(code_event())
    assert s.reserve(first, scope="TesterAPI", owner="one").status == "RESERVED"
    second = s.submit(code_event("b"))
    assert s.reserve(second, scope="alias", owner="two").reason == "scope_mismatch"
    assert s.reserve(second, scope="TesterAPI", owner="two").reason == "lease_busy"
    s.close()


def bridge(supervisor, **kwargs):
    from src.api.guardians.integration import ProposalBridge

    return ProposalBridge(supervisor, **kwargs)


def code_result():
    identity = Identity("TesterAPI", "TesterAPI", "a" * 40, "a" * 40, True, True, True, True)
    probes = tuple(
        Probe(source, str(n), 100, False, "sha256:" + str(n) * 64)
        for n, source in enumerate(("scm", "http_health", "worker_heartbeat"), 1)
    )
    return CodeGuardian(enabled=True).evaluate(identity, probes, now=100, restarts_last_hour=0)


def data_snapshot():
    digest = "a" * 64
    return dict(
        environment="tester_preview",
        schema_verified=True,
        dsn_isolated=True,
        postgres_port=55432,
        database_name="tester_preview",
        database_role="guardian_readonly",
        dsn_identity_verified=True,
        read_only_snapshot=True,
        baseline_verified=True,
        independent_integrity_check=True,
        ownership_verified=True,
        history_state="bounded_verified",
        baseline_sha256=digest,
        category="linear",
        symbol="BTCUSDT",
        interval="1",
        start_ms=0,
        end_ms=180000,
        source="bybit",
        source_verified=True,
        source_evidence_sha256=digest,
        source_gap_proof_verified=True,
        catalog_verified=True,
        source_gap_proof_sha256=digest,
        source_gap_proof_scope="bybit/linear/BTCUSDT/1",
        source_gap_proof_start_ms=0,
        source_gap_proof_end_ms=180000,
        catalog_handler="CatalogHandler",
        fenced_writer_verified=True,
        lease_epoch=12,
        overlapping_writer=False,
        lease_scope="bybit/linear/BTCUSDT/1",
        lease_evidence_sha256=digest,
        queue_identity_verified=True,
        rows=[],
    )


def exchange_pair():
    observation = Observation(
        "https://api.bybit.com/v5/market/kline", "timeout", 20, "sha256:" + "e" * 64
    )
    return observation, ExchangeGuardian(enabled=True).inspect(observation, now=100)


def storage_manifest(tmp_path):
    root = tmp_path / "runtime"
    (root / "tmp").mkdir(parents=True)
    # Real read-only inventory; force a measured low-space proposal by a large threshold.
    return inventory(root, min_free_bytes=10**30, quota_bytes=10**30, now=100, enabled=True)


def test_four_guardians_admit_through_one_master(tmp_path):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    b = bridge(s, enabled=True)
    kwargs = dict(environment="tester_preview", ownership_verified=True)
    snapshot = data_snapshot()
    observation, verdict = exchange_pair()
    admissions = [
        b.code(code_result(), **kwargs),
        b.data(snapshot, reconcile(snapshot, enabled=True), **kwargs),
        b.exchange(observation, verdict, now=100, **kwargs),
        b.storage(
            storage_manifest(tmp_path),
            scope="tester-preview/runtime",
            min_free_bytes=10**30,
            disk_telemetry_verified=True,
            **kwargs,
        ),
    ]
    assert [a.receipt.role for a in admissions] == ["code", "data", "exchange", "storage"]
    assert [a.receipt.reason for a in admissions] == [
        "PROPOSE_OWNED_RESTART",
        "PROPOSE_BOUNDED_BACKFILL",
        "PROPOSE_BOUNDED_RETRY",
        "PROPOSE_PAUSE_DOWNLOADS",
    ]
    assert all(
        a.receipt.status == "PROPOSED" and a.execution_authorized is False for a in admissions
    )
    assert s.verify_chain()
    with pytest.raises(PermissionError, match="proposal-only"):
        b.authorize_execution(admissions[0])
    s.close()


def test_bridge_defaults_disabled_and_refuses_unattested_or_lab(tmp_path):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    result = code_result()
    assert (
        bridge(s).code(result, environment="tester_preview", ownership_verified=True).reason
        == "bridge_disabled"
    )
    b = bridge(s, enabled=True)
    assert b.code(result, environment="tester_preview").reason == "ownership_unverified"
    assert (
        b.code(result, environment="tester_lab", ownership_verified=True).reason
        == "environment_out_of_scope"
    )
    assert s.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
    s.close()


def test_master_disabled_overrides_enabled_bridge(tmp_path):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=False)
    admission = bridge(s, enabled=True).code(
        code_result(), environment="tester_preview", ownership_verified=True
    )
    assert admission.receipt.reason == "disabled_or_killed"
    assert admission.execution_authorized is False
    s.close()


def test_combined_dedup_reopen_backpressure_failure_and_kill(tmp_path):
    path = tmp_path / "r.sqlite"
    s = DurableSupervisor(path, enabled=True, max_active=1)
    b = bridge(s, enabled=True)
    kwargs = dict(environment="tester_preview", ownership_verified=True)
    first = b.code(code_result(), **kwargs).receipt
    lease = s.reserve(first, scope="TesterAPI", owner="code")
    observation, verdict = exchange_pair()
    second = b.exchange(observation, verdict, now=100, **kwargs).receipt
    exchange_scope = "bybit/rest/api.bybit.com/v5/market/kline"
    assert s.reserve(second, scope=exchange_scope, owner="exchange").reason == "backpressure"
    assert (
        s.finish(lease, scope="TesterAPI", owner="code", verified=False).reason
        == "unverified_or_failed"
    )
    s.close()
    s = DurableSupervisor(path, enabled=True, max_active=1)
    b = bridge(s, enabled=True)
    assert b.code(code_result(), **kwargs).receipt.reason == "duplicate_event"
    reservation = s.reserve(second, scope=exchange_scope, owner="exchange")
    assert reservation.status == "RESERVED"
    s.kill()
    assert (
        s.finish(reservation, scope=exchange_scope, owner="exchange").reason
        == "stale_or_invalid_fence"
    )
    assert (
        b.exchange(observation, verdict, now=100, **kwargs).receipt.reason == "disabled_or_killed"
    )
    assert s.verify_chain()
    s.close()


def test_bridge_rejects_mutated_or_nonproposal_inputs(tmp_path):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    b = bridge(s, enabled=True)
    kwargs = dict(environment="tester_preview", ownership_verified=True)
    result = code_result()
    assert b.code(replace(result, action="REQUEST_CODE_REVIEW"), **kwargs).receipt is None
    snapshot = data_snapshot()
    finding = reconcile(snapshot, enabled=True)
    snapshot["ownership_verified"] = False
    assert b.data(snapshot, finding, **kwargs).receipt is None
    observation, verdict = exchange_pair()
    assert (
        b.exchange(observation, replace(verdict, proposal="NONE"), now=100, **kwargs).receipt
        is None
    )
    manifest = storage_manifest(tmp_path)
    manifest["free_bytes"] = 0
    assert (
        b.storage(
            manifest,
            scope="tester-preview/runtime",
            min_free_bytes=10**30,
            disk_telemetry_verified=True,
            **kwargs,
        ).receipt
        is None
    )
    assert s.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
    s.close()


def test_storage_requires_separate_disk_attestation_and_never_admits_cleanup(tmp_path):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    b = bridge(s, enabled=True)
    kwargs = dict(environment="tester_preview", ownership_verified=True)
    manifest = storage_manifest(tmp_path)
    assert (
        b.storage(manifest, scope="tester-preview/runtime", min_free_bytes=10**30, **kwargs).receipt
        is None
    )
    manifest["proposals"] = ["PROPOSE_DRY_RUN_TEMP_CLEANUP"]
    manifest["evidence_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in manifest.items() if k != "evidence_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert (
        b.storage(
            manifest,
            scope="tester-preview/runtime",
            min_free_bytes=10**30,
            disk_telemetry_verified=True,
            **kwargs,
        ).receipt
        is None
    )
    s.close()


def legacy_database(path, *, active=False):
    import sqlite3
    import time

    from src.api.guardians.supervisor import Receipt

    key = "a" * 64
    prior = "0" * 64
    fields = [prior, key, "code", "PROPOSED", "PROPOSE_OWNED_RESTART", None, 100.0]
    digest = hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE identities(event_key TEXT PRIMARY KEY, role TEXT NOT NULL)")
    db.execute("INSERT INTO identities VALUES (?, 'code')", (key,))
    db.execute(
        "CREATE TABLE receipts(seq INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT, "
        "role TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL, epoch INTEGER, "
        "at REAL NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL)"
    )
    db.execute(
        "INSERT INTO receipts(event_key,role,status,reason,epoch,at,previous_hash,hash) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (key, "code", "PROPOSED", "PROPOSE_OWNED_RESTART", None, 100.0, prior, digest),
    )
    if active:
        db.execute(
            "CREATE TABLE leases(scope TEXT PRIMARY KEY,event_key TEXT NOT NULL,epoch INTEGER NOT NULL,"
            "expires REAL NOT NULL,owner TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO leases VALUES ('TesterAPI',?,1,?,'worker')", (key, time.time() + 300)
        )
    db.commit()
    db.close()
    return Receipt(1, key, "code", "PROPOSED", "PROPOSE_OWNED_RESTART")


def test_legacy_scopeless_admission_cannot_reserve(tmp_path):
    path = tmp_path / "legacy.sqlite"
    first = legacy_database(path)
    s = DurableSupervisor(path, enabled=True)
    assert s.verify_chain()
    # Even a populated mutable index cannot upgrade unbound legacy authority.
    s.db.execute("UPDATE identities SET scope='testerapi' WHERE event_key=?", (first.event_key,))
    assert s.reserve(first, scope="TesterAPI", owner="worker").reason == "scope_mismatch"
    s.close()


def test_active_legacy_lease_blocks_admission_after_upgrade(tmp_path):
    path = tmp_path / "legacy.sqlite"
    legacy_database(path, active=True)
    with pytest.raises(RuntimeError, match="guardian_legacy_unbound_lease"):
        DurableSupervisor(path, enabled=True)


@pytest.mark.parametrize("method", ["code", "data", "exchange", "storage"])
def test_every_adapter_rejects_lab_without_relabeling(tmp_path, method):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    b = bridge(s, enabled=True)
    kwargs = dict(environment="tester_lab", ownership_verified=True)
    if method == "code":
        admission = b.code(code_result(), **kwargs)
    elif method == "data":
        snapshot = data_snapshot()
        admission = b.data(snapshot, reconcile(snapshot, enabled=True), **kwargs)
    elif method == "exchange":
        observation, verdict = exchange_pair()
        admission = b.exchange(observation, verdict, now=100, **kwargs)
    else:
        admission = b.storage({}, scope="runtime", min_free_bytes=1, **kwargs)
    assert admission.reason == "environment_out_of_scope"
    assert admission.receipt is None
    assert admission.execution_authorized is False
    assert s.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
    s.close()


@pytest.mark.parametrize("proposals", [None, 1, "PROPOSE_PAUSE_NEW_DOWNLOADS"])
def test_storage_malformed_proposal_list_is_denied(tmp_path, proposals):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    manifest = storage_manifest(tmp_path)
    manifest["proposals"] = proposals
    manifest["evidence_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in manifest.items() if k != "evidence_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    admission = bridge(s, enabled=True).storage(
        manifest,
        scope="tester-preview/runtime",
        min_free_bytes=10**30,
        disk_telemetry_verified=True,
        environment="tester_preview",
        ownership_verified=True,
    )
    assert admission.receipt is None
    s.close()


@pytest.mark.parametrize("now", [None, True, float("nan"), -1])
def test_exchange_malformed_time_is_denied(tmp_path, now):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    observation, verdict = exchange_pair()
    admission = bridge(s, enabled=True).exchange(
        observation, verdict, now=now, environment="tester_preview", ownership_verified=True
    )
    assert admission.receipt is None
    s.close()


def test_disabled_child_outputs_never_gain_admission(tmp_path):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    b = bridge(s, enabled=True)
    kwargs = dict(environment="tester_preview", ownership_verified=True)
    snapshot = data_snapshot()
    observation, _ = exchange_pair()
    admissions = [
        b.code(CodeGuardian().evaluate(None, None, now=100, restarts_last_hour=0), **kwargs),
        b.data(snapshot, reconcile(snapshot), **kwargs),
        b.exchange(
            observation, ExchangeGuardian().inspect(observation, now=100), now=100, **kwargs
        ),
        b.storage(
            inventory(tmp_path, min_free_bytes=1, quota_bytes=1),
            scope="runtime",
            min_free_bytes=1,
            disk_telemetry_verified=True,
            **kwargs,
        ),
    ]
    assert all(a.receipt is None and a.execution_authorized is False for a in admissions)
    assert s.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
    s.close()


@pytest.mark.parametrize(
    "first_scope,second_scope", [("TesterAPI", "TesterApi"), ("TESTERAPI", "testerapi")]
)
def test_code_service_case_aliases_share_lease_and_budget(tmp_path, first_scope, second_scope):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True, restart_limit=1)
    first = s.submit({**code_event(), "scope": first_scope})
    lease = s.reserve(first, scope=first_scope, owner="one")
    assert lease.status == "RESERVED"
    second = s.submit({**code_event("b"), "scope": second_scope})
    assert s.reserve(second, scope=second_scope, owner="two").reason == "lease_busy"
    assert s.finish(lease, scope=second_scope, owner="one").status == "CLOSED"
    assert s.reserve(second, scope=second_scope, owner="two").reason == "restart_budget"
    s.close()


def test_noncode_scopes_preserve_case(tmp_path):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    for scope in ("runtime/A", "runtime/a"):
        event = dict(
            component="storage",
            signal="low_disk",
            scope=scope,
            environment="tester_preview",
            evidence_ref="sha256:" + "f" * 64,
            disk_telemetry_verified=True,
        )
        proposal = s.submit(event)
        assert s.reserve(proposal, scope=scope, owner="storage").status == "RESERVED"
    s.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("fingerprint", None),
        ("fingerprint", []),
        ("action", []),
        ("event", {"component": "code", "signal": []}),
    ],
)
def test_malformed_typed_code_result_is_denied(tmp_path, field, value):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    admission = bridge(s, enabled=True).code(
        replace(code_result(), **{field: value}),
        environment="tester_preview",
        ownership_verified=True,
    )
    assert admission.receipt is None
    s.close()


@pytest.mark.parametrize(
    "field,value",
    [("proposal", []), ("signal", []), ("attempts", []), ("retry_at", []), ("evidence_ref", [])],
)
def test_malformed_typed_exchange_verdict_is_denied(tmp_path, field, value):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    observation, verdict = exchange_pair()
    admission = bridge(s, enabled=True).exchange(
        observation,
        replace(verdict, **{field: value}),
        now=100,
        environment="tester_preview",
        ownership_verified=True,
    )
    assert admission.receipt is None
    s.close()


def test_existing_noncanonical_code_scope_fails_closed_on_reopen(tmp_path):
    path = tmp_path / "r.sqlite"
    s = DurableSupervisor(path, enabled=True)
    proposal = s.submit(code_event())
    s.db.execute("UPDATE identities SET scope='TesterAPI' WHERE event_key=?", (proposal.event_key,))
    s.close()
    with pytest.raises(RuntimeError, match="guardian_receipt_chain_corrupt"):
        DurableSupervisor(path, enabled=True)


def test_admitted_scope_authority_is_bound_to_receipt_chain(tmp_path):
    path = tmp_path / "r.sqlite"
    s = DurableSupervisor(path, enabled=True)
    proposal = s.submit(code_event())
    s.db.execute(
        "UPDATE identities SET scope='other-service' WHERE event_key=?", (proposal.event_key,)
    )
    assert not s.verify_chain()
    with pytest.raises(RuntimeError, match="guardian_receipt_chain_corrupt"):
        s.reserve(proposal, scope="other-service", owner="worker")
    s.close()
    with pytest.raises(RuntimeError, match="guardian_receipt_chain_corrupt"):
        DurableSupervisor(path, enabled=True)


@pytest.mark.parametrize("target", ["identities", "receipts"])
def test_scoped_admission_cannot_downgrade_to_legacy_hash(tmp_path, target):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    proposal = s.submit(code_event())
    s.db.execute(f"UPDATE {target} SET scope=NULL WHERE event_key=?", (proposal.event_key,))
    assert not s.verify_chain()
    s.close()


def test_legacy_restart_charges_keep_case_insensitive_budget(tmp_path):
    import time

    path = tmp_path / "legacy.sqlite"
    legacy_database(path)
    s = DurableSupervisor(path, enabled=True, restart_limit=1)
    s.db.execute("INSERT INTO restart_attempts VALUES ('TesterAPI',?)", (time.time(),))
    s.close()
    s = DurableSupervisor(path, enabled=True, restart_limit=1)
    proposal = s.submit(code_event("b"))
    assert s.reserve(proposal, scope="testerapi", owner="worker").reason == "restart_budget"
    s.close()


@pytest.mark.parametrize(
    "field,value", [("action", []), ("evidence_ref", []), ("scope", []), ("proposal", [])]
)
def test_malformed_typed_data_finding_is_denied(tmp_path, field, value):
    s = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    snapshot = data_snapshot()
    finding = replace(reconcile(snapshot, enabled=True), **{field: value})
    admission = bridge(s, enabled=True).data(
        snapshot, finding, environment="tester_preview", ownership_verified=True
    )
    assert admission.receipt is None
    s.close()
