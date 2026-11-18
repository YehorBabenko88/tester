from src.api.guardian_supervisor import GuardianSupervisor

DIGEST = "sha256:" + "a" * 64


def event(**overrides):
    result = {
        "component": "code",
        "signal": "service_unhealthy",
        "scope": "tester-preview/api",
        "environment": "tester_preview",
        "evidence_ref": DIGEST,
        "ownership_verified": True,
        "consecutive_failed_probes": 3,
        "restarts_last_hour": 0,
    }
    result.update(overrides)
    return result


def test_supervisor_disabled_by_default():
    proposal = GuardianSupervisor().propose(event())
    assert proposal.action == "ALERT"
    assert not proposal.allowed
    assert proposal.reason == "guardians_disabled"


def test_enabled_supervisor_only_proposes_and_deduplicates():
    coordinator = GuardianSupervisor(enabled=True)
    first = coordinator.propose(event())
    assert first.action == "PROPOSE_OWNED_RESTART"
    assert first.allowed
    assert len(first.key) == 64
    second = coordinator.propose(event(restarts_last_hour=1))
    assert second.key == first.key
    assert not second.allowed
    assert second.reason == "duplicate_event"


def test_unverified_or_foreign_events_fail_closed():
    coordinator = GuardianSupervisor(enabled=True)
    for bad in (
        event(evidence_ref="source:unverified"),
        event(environment="production"),
        event(scope=""),
        event(component="other"),
        event(ownership_verified=False),
        event(consecutive_failed_probes=2),
    ):
        result = coordinator.propose(bad)
        assert not result.allowed


def test_isolated_scopes_have_distinct_idempotency_keys():
    coordinator = GuardianSupervisor(enabled=True)
    one = coordinator.propose(event(scope="tester-preview/api"))
    two = coordinator.propose(event(scope="tester-preview/worker"))
    assert one.allowed and two.allowed
    assert one.key != two.key


def test_cross_component_policy_does_not_grant_other_actions():
    coordinator = GuardianSupervisor(enabled=True)
    result = coordinator.propose(
        event(
            component="storage",
            signal="low_disk",
            scope="tester-preview/runtime",
            disk_telemetry_verified=True,
        )
    )
    assert result.allowed
    assert result.action == "PROPOSE_PAUSE_DOWNLOADS"
    assert result.action != "PROPOSE_OWNED_RESTART"


from src.api.guardians.supervisor import DurableSupervisor


def test_durable_proposals_fencing_kill_and_reopen(tmp_path):
    path = tmp_path / "guardians.sqlite"
    supervisor = DurableSupervisor(path, enabled=True, max_active=1)
    first = supervisor.submit(event())
    assert first.status == "PROPOSED"
    assert supervisor.submit(event()).reason == "duplicate_event"
    lease = supervisor.reserve(first, scope="tester-preview/api", owner="code")
    assert lease.status == "RESERVED"
    assert lease.epoch == 1
    assert supervisor.reserve(first, scope="tester-preview/api", owner="other").reason == "lease_busy"
    assert supervisor.finish(lease, scope="tester-preview/api", owner="other").status == "DENIED"
    assert supervisor.finish(lease, scope="tester-preview/api", owner="code").status == "CLOSED"
    assert supervisor.finish(lease, scope="tester-preview/api", owner="code").status == "DENIED"
    assert supervisor.kill().status == "STOPPED"
    assert supervisor.verify_chain()
    supervisor.close()
    reopened = DurableSupervisor(path, enabled=True)
    assert reopened.submit(event()).reason == "disabled_or_killed"
    assert reopened.verify_chain()
    reopened.close()


def test_durable_restart_budget_and_tamper(tmp_path):
    supervisor = DurableSupervisor(tmp_path / "r.sqlite", enabled=True, restart_limit=1)
    first = supervisor.submit(event())
    lease = supervisor.reserve(first, scope="api", owner="worker")
    assert lease.status == "RESERVED"
    assert supervisor.finish(lease, scope="api", owner="worker").status == "CLOSED"
    second = supervisor.submit(event(evidence_ref="sha256:" + "b" * 64))
    assert supervisor.reserve(second, scope="api", owner="worker").reason == "restart_budget"
    assert supervisor.verify_chain()
    supervisor.db.execute("UPDATE receipts SET reason='tampered' WHERE seq=1")
    assert not supervisor.verify_chain()
    supervisor.close()


def test_durable_disabled_and_invalid(tmp_path, monkeypatch):
    monkeypatch.delenv("GUARDIANS_ENABLED", raising=False)
    supervisor = DurableSupervisor(tmp_path / "r.sqlite")
    assert supervisor.submit(event()).reason == "disabled_or_killed"
    supervisor.close()
    supervisor = DurableSupervisor(tmp_path / "r.sqlite", enabled=True)
    assert supervisor.submit(None).status == "DENIED"
    assert supervisor.submit(event(scope="../unsafe")).status == "DENIED"
    assert supervisor.submit(event(environment="production")).status == "DENIED"
    assert supervisor.verify_chain()
    supervisor.close()
