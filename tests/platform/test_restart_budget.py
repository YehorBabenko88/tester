"""Lifetime launch reservations survive parent crashes and concurrent starts."""

import concurrent.futures
import json
import subprocess
import sys
import uuid

import pytest

from src.api.restart_budget import BudgetDenied, RestartBudget


def budget(tmp_path):
    return RestartBudget(tmp_path, str(uuid.UUID(int=42)), "a" * 40)


def test_parent_recreation_and_initialization_cannot_reset_lifetime_budget(tmp_path):
    original = budget(tmp_path)
    original.initialize()
    for _ in range(3):
        budget(tmp_path).admit("host")
    with pytest.raises(BudgetDenied, match="exhausted"):
        budget(tmp_path).admit("host")
    with pytest.raises(BudgetDenied, match="exists"):
        original.initialize()


def test_concurrent_reservations_do_not_exceed_shared_limit(tmp_path):
    budget(tmp_path).initialize()

    def reserve(_):
        try:
            budget(tmp_path).admit("worker")
            return True
        except BudgetDenied:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(reserve, range(24))) == 6


@pytest.mark.parametrize("damage", [b"", b"{", b"{}\n", b"null\n"])
def test_corruption_is_fail_closed_without_reset(tmp_path, damage):
    value = budget(tmp_path)
    value.initialize()
    value.path.write_bytes(damage)
    with pytest.raises(BudgetDenied):
        value.admit("api")
    assert value.path.read_bytes() == damage


def test_wrong_installation_source_missing_state_and_kill_switch_refuse(tmp_path):
    value = budget(tmp_path)
    with pytest.raises(BudgetDenied):
        value.admit("host")
    value.initialize()
    for wrong in (
        RestartBudget(tmp_path, str(uuid.UUID(int=43)), "a" * 40),
        RestartBudget(tmp_path, str(uuid.UUID(int=42)), "b" * 40),
    ):
        with pytest.raises(BudgetDenied, match="identity"):
            wrong.admit("host")
    before = value.path.read_bytes()
    (tmp_path / "restart.disabled").touch()
    with pytest.raises(BudgetDenied, match="disabled"):
        value.admit("host")
    assert value.path.read_bytes() == before


def test_reservation_is_durable_before_caller_can_spawn(tmp_path):
    value = budget(tmp_path)
    value.initialize()
    value.admit("api")
    lines = [json.loads(line) for line in value.path.read_bytes().splitlines()]
    assert lines[-1] == {"sequence": 1, "role": "api"}
    assert value.counts() == {"host": 0, "api": 1, "worker": 0}


def test_hardlinked_journal_is_never_mutated(tmp_path):
    value = budget(tmp_path)
    value.initialize()
    foreign = tmp_path / "foreign"
    foreign.hardlink_to(value.path)
    before = foreign.read_bytes()
    with pytest.raises(BudgetDenied, match="regular"):
        value.admit("host")
    assert foreign.read_bytes() == before


def test_separate_processes_share_reservations(tmp_path):
    budget(tmp_path).initialize()
    program = (
        "import sys; from src.api.restart_budget import RestartBudget, BudgetDenied; "
        "b=RestartBudget(sys.argv[1], sys.argv[2], sys.argv[3]); "
        "\ntry: b.admit('host')\nexcept BudgetDenied: sys.exit(3)"
    )
    children = [
        subprocess.Popen(
            [sys.executable, "-c", program, str(tmp_path), str(uuid.UUID(int=42)), "a" * 40]
        )
        for _ in range(8)
    ]
    results = [child.wait(timeout=20) for child in children]
    assert results.count(0) == 3 and results.count(3) == 5
    assert budget(tmp_path).counts()["host"] == 3


def test_supervisor_denial_prevents_spawn_even_after_parent_recreation(tmp_path, monkeypatch):
    from src.api import supervisor

    value = budget(tmp_path)
    value.initialize()
    launches = []
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *args, **kw: launches.append(args))
    for _ in range(6):
        supervisor.launch_component("worker", budget(tmp_path))
    with pytest.raises(BudgetDenied):
        supervisor.launch_component("worker", budget(tmp_path))
    assert len(launches) == 6


def test_environment_requires_complete_bound_identity(tmp_path):
    assert RestartBudget.from_environment({}) is None
    with pytest.raises(BudgetDenied):
        RestartBudget.from_environment({"TESTER_RESTART_BUDGET_DIR": str(tmp_path)})


def test_missing_journal_after_enrollment_cannot_be_reinitialized(tmp_path):
    value = budget(tmp_path)
    value.initialize()
    value.path.unlink()
    with pytest.raises(BudgetDenied):
        value.initialize()
    with pytest.raises(BudgetDenied):
        value.admit("host")
