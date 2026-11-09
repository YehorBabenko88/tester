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
