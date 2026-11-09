from pathlib import Path

import pytest

from src.integration.pc4_runtime import MODULE, validate_receipt, worker_state
from src.integration.runtime_identity import require_runtime_argv


def receipt():
    return dict(
        schema="tester-pc4-combined/1",
        application_source_sha="a" * 40,
        lab_source_sha="b" * 40,
        root="owned-root",
        source_root="published-source",
        private_sha256="c" * 64,
        input_digest="d" * 64,
        mode="SYNTHETIC_ONLY",
        real_market_coverage="NOT_PROVEN",
        api_port=18767,
        postgres_port=55471,
    )


def test_receipt_exact_source_owner_private_hash_and_no_real_claim():
    value = receipt()
    validate_receipt(
        value, Path("owned-root"), Path("published-source"), "b" * 40, "a" * 40, "c" * 64
    )
    for field in value:
        bad = {**value, field: "wrong"}
        with pytest.raises(ValueError):
            validate_receipt(
                bad, Path("owned-root"), Path("published-source"), "b" * 40, "a" * 40, "c" * 64
            )


def test_new_module_guard_exact_argv_and_old_module_cannot_adopt():
    args = [
        "python",
        "-m",
        MODULE,
        "serve",
        "--root",
        "owned-root",
        "--lab-source-sha",
        "b" * 40,
        "--application-source-sha",
        "a" * 40,
    ]
    require_runtime_argv(args, "owned-root", "b" * 40, "a" * 40, module=MODULE)
    require_runtime_argv(
        [args[0], "-B", *args[1:]], "owned-root", "b" * 40, "a" * 40, module=MODULE
    )
    for bad in [
        [*args, "--extra"],
        [args[0], "-c", "pass", *args[1:]],
        [args[0], "-B", "-B", *args[1:]],
    ]:
        with pytest.raises(ValueError):
            require_runtime_argv(bad, "owned-root", "b" * 40, "a" * 40, module=MODULE)
    with pytest.raises(ValueError):
        require_runtime_argv(
            args, "owned-root", "b" * 40, "a" * 40, module="src.integration.market_runtime"
        )


def test_failure_state_is_finite_and_not_exception_text():
    assert worker_state("running", 0) == {"state": "RUNNING", "failures": 0}
    assert worker_state("secret DSN", 99999) == {"state": "FAILED", "failures": 3}


def test_factory_constructs_routes_without_database_ddl_or_os_actions(monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI

    from src.api import main
    from src.api.research_execution import ExecutionLedger
    from src.integration.pc4_runtime import application

    monkeypatch.setattr(main, "create_app", lambda settings: FastAPI())

    def refuse(*args, **kwargs):
        raise AssertionError("factory must not connect or spawn")

    monkeypatch.setattr(ExecutionLedger, "connection", refuse)
    settings = SimpleNamespace(dsn="host=127.0.0.1 port=55471 dbname=tester_research_unit")
    app = application(
        settings,
        Path("owned-root"),
        {"actor": "actor", "application_source_sha": "a" * 40},
        object(),
    )
    paths = {route.path for route in app.routes}
    assert "/api/control/research-executions" in paths
    assert "/api/control/research" in paths
    assert "/api/pc4/identity" in paths


def limited_role():
    return dict(
        current_user="research_owner",
        session_user="research_owner",
        rolsuper=False,
        rolcreatedb=False,
        rolcreaterole=False,
        rolreplication=False,
        rolbypassrls=False,
        rolcanlogin=True,
        owns_database=False,
        owns_objects=False,
        has_create=False,
        other_membership=False,
        read_settings=True,
    )


def test_runtime_role_refuses_all_privileged_flags_ownership_create_and_membership():
    from src.integration.pc4_runtime import validate_runtime_role

    validate_runtime_role(limited_role())
    for field in limited_role():
        value = limited_role()
        value[field] = "wrong" if isinstance(value[field], str) else not value[field]
        with pytest.raises(ValueError):
            validate_runtime_role(value)


def test_operator_secret_written_once_with_mocked_database_handoff(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import psycopg

    from src.integration import pc4_runtime as runtime

    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    dsn = "host=127.0.0.1 port=55471 user=research_owner password=disposable-test-only dbname=tester_research_unit"
    monkeypatch.setattr(
        runtime,
        "context",
        lambda *a, **k: (
            source,
            root,
            {"database": "tester_research_unit"},
            SimpleNamespace(dsn=dsn),
            {},
            None,
        ),
    )
    monkeypatch.setattr(runtime, "_cluster_identity", lambda *a: "123456")
    monkeypatch.setattr(runtime, "assert_runtime_role", lambda *a: None)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, *args):
            result = None
            if isinstance(statement, str):
                if statement.startswith("SELECT oid,rolsuper FROM"):
                    result = {"oid": 42, "rolsuper": True}
                elif statement.startswith("SELECT current_user AS name"):
                    result = {"name": "tester_pc4_admin"}
                elif statement.startswith("SELECT oid,rolsuper,rolcanlogin"):
                    result = {"oid": 42, "rolsuper": True, "rolcanlogin": False}
                elif statement == "SHOW data_directory":
                    result = {"data_directory": str(root / "pgdata")}
            return SimpleNamespace(fetchone=lambda: result)

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: Connection())
    monkeypatch.setattr(runtime.ExecutionLedger, "connection", lambda *a: Connection())
    actual_save = runtime._save
    writes = []

    def record(path, value, *, new=False):
        if path.name == "operator-admin-private.json":
            writes.append((new, json.dumps(value, sort_keys=True)))
        return actual_save(path, value, new=new)

    monkeypatch.setattr(runtime, "_save", record)
    result = runtime.limit_role(root, "b" * 40, "a" * 40)
    assert result["runtime_privileged"] is False
    assert len(writes) == 1 and writes[0][0] is True
    secret = json.loads((root / "operator-admin-private.json").read_text())
    assert secret["root"] == str(root)
    assert secret["system_identifier"] == "123456"
    assert "stage" not in secret


def test_prepare_terminal_pair_uses_exact_pinned_linear_source_and_actual_f():
    import os

    import src.integration

    provider = os.environ.get("TEST_SNAPSHOT_PROVIDER_ROOT")
    if provider:
        src.integration.__path__.append(str(Path(provider).resolve() / "src/integration"))
    try:
        from src.integration.qualified_snapshot import certified_terminal_fixture_snapshot
    except ModuleNotFoundError:
        pytest.skip("separate pinned B terminal producer required")
    from src.integration.pc4_runtime import terminal_inputs
    from src.integration.strategy_lab import run_synthetic_research

    class Repository:
        @staticmethod
        def publish_terminal_fixture():
            return certified_terminal_fixture_snapshot()

    snapshot, strategy = terminal_inputs(Repository())
    assert snapshot.mode == "SYNTHETIC" and snapshot.admitted is True
    assert snapshot.snapshot["category"] == "linear"
    assert snapshot.snapshot["symbol"] == "BTCUSDT"
    assert strategy["costs"]["funding_policy"] == "required"
    bundle = run_synthetic_research(strategy, snapshot.snapshot)
    assert bundle["result"]["metrics"]["funding_costs"] == "0.0100"
    assert bundle["certification"]["real_market_coverage"] == "NOT_PROVEN"
