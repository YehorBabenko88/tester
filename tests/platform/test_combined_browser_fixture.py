import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "combined_browser_ci", Path(__file__).parents[2] / "scripts/combined_browser_ci.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def environment():
    return dict(
        GITHUB_ACTIONS="true",
        TESTER_COMBINED_CI="synthetic-disposable",
        TEST_RESEARCH_DATABASE_URL="postgresql://tester_ci:disposable-ci-only@127.0.0.1:55472/tester_research_combined_ci",
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_ACTIONS", "false"),
        ("TESTER_COMBINED_CI", ""),
        ("TEST_RESEARCH_DATABASE_URL", "postgresql://127.0.0.1:5432/postgres"),
        (
            "TEST_RESEARCH_DATABASE_URL",
            "postgresql://example.com:55472/tester_research_combined_ci",
        ),
        ("TEST_RESEARCH_DATABASE_URL", "postgresql://127.0.0.1:55472/tester_research_other"),
    ],
)
def test_guard_refuses_non_exact_disposable_scope(key, value):
    env = environment()
    env[key] = value
    with pytest.raises(ValueError):
        fixture.require_ci(env)


def test_guard_accepts_exact_ci_configuration_without_connecting():
    assert fixture.require_ci(environment()) == environment()["TEST_RESEARCH_DATABASE_URL"]


def test_genuine_f_command_preserves_every_binding_and_digest_without_mutation():
    from src.api.research_control import Binding, canonical_digest
    from src.integration.strategy_lab import run_certified_fixture

    bundle = run_certified_fixture()
    before = deepcopy(bundle)
    command = fixture.command_for(bundle)
    for key in Binding.model_fields:
        assert getattr(command, key) == bundle["chart"][key]
    assert command.expected_artifact_digest == canonical_digest(bundle["chart"])
    assert bundle == before
    assert bundle["chart"]["synthetic"] is True
    assert bundle["chart"]["execution_mode"] == "SIMULATED"
