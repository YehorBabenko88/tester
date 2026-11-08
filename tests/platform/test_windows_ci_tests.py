"""Policy tests only; no token, account, ACL, service or database operation."""

import pytest

from scripts.windows_ci_tests import require_ci_workspace, validate_pytest_args


def test_requires_explicit_matching_github_workspace(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    for env in (
        {},
        {"GITHUB_ACTIONS": "false", "GITHUB_WORKSPACE": str(root)},
        {"GITHUB_ACTIONS": "true", "GITHUB_WORKSPACE": str(tmp_path)},
    ):
        with pytest.raises(ValueError):
            require_ci_workspace(env, root)
    assert (
        require_ci_workspace({"GITHUB_ACTIONS": "true", "GITHUB_WORKSPACE": str(root)}, root)
        == root
    )


def test_launcher_accepts_only_pytest_python_module_arguments():
    assert validate_pytest_args(("-m", "pytest", "-q")) == ["-m", "pytest", "-q"]
    for args in [(), ("-c", "pass"), ("-m", "src.api.worker"), ("-m", "pytest", "bad\x00arg")]:
        with pytest.raises(ValueError):
            validate_pytest_args(args)


def test_no_native_token_api_outside_ci(monkeypatch, tmp_path):
    from scripts import windows_ci_tests

    def forbidden():
        raise AssertionError("native token API reached without CI scope")

    monkeypatch.setattr(windows_ci_tests, "_api", forbidden)
    with pytest.raises(ValueError, match="GitHub"):
        windows_ci_tests.run_pytest(("-m", "pytest"), cwd=tmp_path, env={})
