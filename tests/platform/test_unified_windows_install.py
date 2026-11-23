import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from src.api import unified_windows_install as install


@pytest.fixture
def tmp_path():
    # Acceptance basetemp may be in the checkout; installer always requires OS temp.
    directory = Path(tempfile.mkdtemp(prefix="tester-unified-")).resolve()
    yield directory
    import psutil

    for process in psutil.process_iter(["cwd", "cmdline"]):
        if (
            str(directory).casefold()
            in " ".join([process.info["cwd"] or "", *(process.info["cmdline"] or [])]).casefold()
        ):
            pytest.fail(
                "Live fixture process; preserving temporary directory for explicit recovery"
            )
    shutil.rmtree(directory)


def fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "hello.txt").write_text("pinned")
    inventory = {"hello.txt": hashlib.sha256(b"pinned").hexdigest()}
    return source, inventory


def system_pg_observation():
    """Read service/listener/config metadata only; never connect to system PG."""
    import psutil

    try:
        service = psutil.win_service_get("postgresql-x64-18").as_dict()
    except psutil.NoSuchProcess:
        service = None
    listeners = sorted(
        (connection.laddr.ip, connection.laddr.port, connection.pid)
        for connection in psutil.net_connections(kind="tcp")
        if connection.status == "LISTEN" and connection.laddr.port == 5432
    )
    configs = {}
    if service:
        match = re.search(r'-D\s+"([^"]+)"', service["binpath"])
        if match:
            cluster = Path(match.group(1))
            for name in ("PG_VERSION", "postgresql.conf", "postgresql.auto.conf", "pg_hba.conf"):
                try:
                    configs[name] = hashlib.sha256((cluster / name).read_bytes()).hexdigest()
                except OSError:
                    configs[name] = "unreadable"
    return {"service": service, "listeners": listeners, "config_sha256": configs}


def test_canonical_target_is_plan_only(tmp_path):
    assert install.canonical_plan()["root"] == r"C:\TesterLab"
    with pytest.raises(ValueError):
        install.UnifiedInstaller(
            Path(r"C:\TesterLab"), tmp_path / "Desktop", disposable_parent=tmp_path
        )


def test_stage_verifies_pin_and_creates_owned_tree(tmp_path):
    source, inventory = fixture(tmp_path)
    manager = install.UnifiedInstaller(
        tmp_path / "owned", tmp_path / "Desktop", disposable_parent=tmp_path
    )
    digest = manager.stage(source, inventory, "a" * 40)
    assert len(digest) == 64
    assert manager.verify()["source_sha"] == "a" * 40
    (manager.root / "app/hello.txt").write_text("tamper")
    with pytest.raises(ValueError):
        manager.verify()


def test_uninstall_requires_both_confirmations_and_removes_owned_data(tmp_path):
    source, inventory = fixture(tmp_path)
    foreign = tmp_path / "foreign.txt"
    foreign.write_text("preserve")
    manager = install.UnifiedInstaller(
        tmp_path / "owned", tmp_path / "Desktop", disposable_parent=tmp_path
    )
    manager.stage(source, inventory, "a" * 40)
    for name in (
        "data/candles",
        "data/results",
        "logs/history",
        "postgres-data/dummy",
        "config/secret",
    ):
        path = manager.root / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text("dummy")
    for phrase, second in [("no", True), ("DELETE TESTER", False)]:
        with pytest.raises(ValueError):
            manager.uninstall(phrase, second_confirmation=second)
        assert manager.root.exists()
    assert manager.uninstall("DELETE TESTER", second_confirmation=True)["status"] == "removed"
    assert not manager.root.exists()
    assert foreign.read_text() == "preserve"
    assert (
        manager.uninstall("DELETE TESTER", second_confirmation=True)["status"] == "already_absent"
    )


def test_uninstall_refuses_tampered_manifest(tmp_path):
    source, inventory = fixture(tmp_path)
    manager = install.UnifiedInstaller(
        tmp_path / "owned", tmp_path / "Desktop", disposable_parent=tmp_path
    )
    manager.stage(source, inventory, "a" * 40)
    path = manager.root / "install/install.json"
    manifest = json.loads(path.read_text())
    manifest["desktop"] = str(tmp_path)
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        manager.uninstall("DELETE TESTER", second_confirmation=True)
    assert manager.root.exists()


def test_operation_lock_excludes_other_manager(tmp_path):
    first = install.UnifiedInstaller(
        tmp_path / "owned", tmp_path / "Desktop", disposable_parent=tmp_path
    )
    second = install.UnifiedInstaller(
        tmp_path / "owned", tmp_path / "Desktop", disposable_parent=tmp_path
    )
    with first.operation():
        with pytest.raises((ValueError, OSError)):
            with second.operation():
                pass


def test_removal_refuses_live_orphan_in_owned_tree(tmp_path):
    source, inventory = fixture(tmp_path)
    manager = install.UnifiedInstaller(
        tmp_path / "owned", tmp_path / "Desktop", disposable_parent=tmp_path
    )
    manager.stage(source, inventory, "a" * 40)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"], cwd=manager.root / "app"
    )
    try:
        with pytest.raises(ValueError, match="Live process"):
            manager.uninstall("DELETE TESTER", second_confirmation=True)
        assert manager.root.exists()
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_cli_typed_removal_does_not_confuse_controller_with_orphan(tmp_path):
    source, inventory = fixture(tmp_path)
    manager = install.UnifiedInstaller(
        tmp_path / "owned", tmp_path / "Desktop", disposable_parent=tmp_path
    )
    manager.stage(source, inventory, "a" * 40)
    command = [
        sys.executable,
        "-B",
        "-m",
        "src.api.unified_windows_install",
        "uninstall",
        "--root",
        str(manager.root),
        "--desktop",
        str(manager.desktop),
        "--disposable-parent",
        str(manager.parent),
        "--manifest-sha256",
        manager.pin,
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        input="WRONG\nYES\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode != 0 and manager.root.exists()
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        input="DELETE TESTER\nYES\n",
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not manager.root.exists()


def test_uninstall_refuses_reparse_descendant(tmp_path):
    source, inventory = fixture(tmp_path)
    manager = install.UnifiedInstaller(
        tmp_path / "owned", tmp_path / "Desktop", disposable_parent=tmp_path
    )
    manager.stage(source, inventory, "a" * 40)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    try:
        (manager.root / "data/link").symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(ValueError):
        manager.uninstall("DELETE TESTER", second_confirmation=True)
    assert foreign.exists()


@pytest.mark.timeout(180)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("TESTER_UNIFIED_WINDOWS_REAL") != "1",
    reason="Explicit disposable real Windows fixture opt-in required",
)
def test_real_windows_postgres_supervisor_shortcuts_and_full_delete(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    before = system_pg_observation()
    source = tmp_path / "source"
    shutil.copytree(
        repo / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    for name in ("contracts", "frontend"):
        if (repo / name).is_dir():
            shutil.copytree(
                repo / name, source / name, ignore=shutil.ignore_patterns("node_modules", "dist")
            )
    script = source / "scripts/windows/Manage-UnifiedTester.ps1"
    script.parent.mkdir(parents=True)
    shutil.copyfile(repo / "scripts/windows/Manage-UnifiedTester.ps1", script)
    inventory = {
        p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.rglob("*")
        if p.is_file()
    }
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, env=env, text=True
    ).strip()
    manager = install.UnifiedInstaller(
        tmp_path / "owned", tmp_path / "Desktop", disposable_parent=tmp_path
    )
    manager.stage(source, inventory, sha)
    pg_bin = Path(os.environ.get("TESTER_UNIFIED_PG_BIN", r"C:\Program Files\PostgreSQL\18\bin"))
    if not (pg_bin / "initdb.exe").is_file():
        pytest.fail("Opted-in Windows fixture requires installed PostgreSQL binaries")

    def free_port():
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    pg_port, api_port = free_port(), free_port()
    assert pg_port != 5432
    manager.initialize(pg_bin, sys.executable, pg_port=pg_port, api_port=api_port)
    runtime = json.loads((manager.root / "install/runtime.json").read_text())
    assert (
        Path(runtime["supervisor_module"]).resolve() == manager.root / "app/src/api/supervisor.py"
    )
    sentinel = manager.desktop / "foreign.txt"
    sentinel.write_text("preserve")
    manager.create_shortcuts()
    assert sorted(p.stem for p in manager.desktop.glob("*.lnk")) == sorted(install.NAMES)
    try:
        powershell = (
            Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )

        def launch(action, answer=None):
            return subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-File",
                    str(manager.root / "app/scripts/windows/Manage-UnifiedTester.ps1"),
                    "-Python",
                    sys.executable,
                    "-Root",
                    str(manager.root),
                    "-Desktop",
                    str(manager.desktop),
                    "-DisposableParent",
                    str(manager.parent),
                    "-ManifestSHA256",
                    manager.pin,
                    "-Action",
                    action,
                ],
                input=answer,
                text=True,
                capture_output=True,
                cwd=tmp_path,
                timeout=90,
            )

        result = launch("start")
        assert result.returncode == 0, result.stderr
        with pytest.raises(ValueError, match="Already started"):
            manager.start()
        assert manager._process().is_running()
        assert manager.stop()["status"] == "stopped"
        readiness = None
        for _ in range(1):
            readiness = manager.start()
            assert readiness["status"] == "ready"
            result = launch("stop")
            assert result.returncode == 0, result.stderr
        (manager.root / "data/candles").mkdir()
        (manager.root / "data/candles/dummy").write_text("dummy history")
        (manager.root / "data/results").mkdir()
        (manager.root / "data/results/dummy").write_text("dummy results")
        with pytest.raises(ValueError):
            manager.uninstall("WRONG", second_confirmation=True)
        assert manager.root.exists()
        result = launch("uninstall", "DELETE TESTER\nYES\n")
        assert result.returncode == 0, result.stderr
        assert not manager.root.exists()
        assert not list(manager.desktop.glob("*.lnk"))
        assert sentinel.read_text() == "preserve"
        assert (
            manager.uninstall("DELETE TESTER", second_confirmation=True)["status"]
            == "already_absent"
        )
        after = system_pg_observation()
        assert after == before
        evidence = repo / ".scratch/unified-evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "windows-process-lifecycle.json").write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "backend": "disposable_process",
                    "source_sha": sha,
                    "payload_inventory_sha256": hashlib.sha256(
                        json.dumps(inventory, sort_keys=True).encode()
                    ).hexdigest(),
                    "manifest_sha256": manager.pin,
                    "pg_port": pg_port,
                    "api_port": api_port,
                    "installed_supervisor_module": runtime["supervisor_module"],
                    "config_sha256": runtime["config_sha256"],
                    "logical_roles": ["postgres", "api", "worker"],
                    "readiness": readiness,
                    "powershell_start_stop_typed_uninstall": True,
                    "shortcuts": list(install.NAMES),
                    "owned_tree_absent": not manager.root.exists(),
                    "foreign_sentinel_preserved": sentinel.read_text() == "preserve",
                    "system_pg_5432_before": before,
                    "system_pg_5432_after": after,
                    "system_pg_5432_service_listener_config_unchanged": before == after,
                    "native_scm_boot_acceptance": False,
                    "canonical_deployment_executed": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        if manager.root.exists():
            manager.stop()
