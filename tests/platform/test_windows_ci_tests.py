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


def test_owned_descendants_reject_escape_and_reparse_before_io(tmp_path, monkeypatch):
    import stat
    from pathlib import Path
    from types import SimpleNamespace

    from scripts.windows_ci_tests import owned_ci_descendant

    root = tmp_path / "checkout"
    root.mkdir()
    scratch = root / ".scratch"
    assert (
        owned_ci_descendant(root, scratch / "fresh" / "probe.txt")
        == scratch / "fresh" / "probe.txt"
    )
    with pytest.raises(ValueError, match="descendant"):
        owned_ci_descendant(root, tmp_path / "foreign" / "probe.txt")
    real = Path.lstat

    def redirected(path, *args, **kwargs):
        if path == scratch:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", redirected)
    for path in (scratch, scratch / "fresh", scratch / "fresh" / "probe.txt"):
        with pytest.raises(ValueError, match="Reparse"):
            owned_ci_descendant(root, path)


def test_spawn_never_inherits_parent_handles(tmp_path):
    from scripts.windows_ci_tests import spawn_no_inherit

    class AdversarialParent:
        def CreateProcessAsUserW(self, *args):
            # An unrelated privileged inheritable handle must never cross here.
            assert args[5] is False
            return 1

    spawn_no_inherit(AdversarialParent(), None, None, None, tmp_path)


def test_reparse_blocks_acl_subprocess_boundary(tmp_path, monkeypatch):
    import stat
    from pathlib import Path
    from types import SimpleNamespace

    from scripts import windows_ci_tests

    root = tmp_path / "checkout"
    root.mkdir()
    scratch = root / ".scratch"
    original = Path.lstat

    def redirected(path, *args, **kwargs):
        if path == scratch:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", redirected)

    def forbidden(*args, **kwargs):
        raise AssertionError("ACL subprocess reached redirected checkout path")

    monkeypatch.setattr(windows_ci_tests.subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="Reparse"):
        windows_ci_tests._powershell("must not execute", root=root, paths=[scratch / "probe.txt"])
    monkeypatch.setattr(windows_ci_tests.tempfile, "mkdtemp", forbidden)
    with pytest.raises(ValueError, match="Reparse"):
        windows_ci_tests._child_proof_and_pytest([], root, scratch)
