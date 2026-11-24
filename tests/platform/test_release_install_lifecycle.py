"""Regression tests for interrupted install, kill switch, shortcut re-run and leftovers.

Issues #107, #108 and #111. Destructive Windows tests use only fresh pytest
temporary directories; administrator-only ACL gates are stubbed exactly where the
CI host's restricted token cannot assign the Administrators owner SID.
"""

import json
import os
import subprocess
import sys
import types
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from src.api import release_install as ri
from src.api import release_uninstall as removal
from src.api.release_security import ReleaseSecurityError

windows_only = pytest.mark.skipif(os.name != "nt", reason="Native Windows handles required")
WRAPPER = Path(__file__).resolve().parents[2] / "scripts/windows/Uninstall-ReleaseTester.ps1"
APP_SID = "S-1-5-80-1-2-3-4-5"
INHERITED_RUNTIME_ACL = [
    (0, 0x10, 0x1F01FF, "S-1-5-18"),
    (0, 0x10, 0x1F01FF, "S-1-5-32-544"),
    (0, 0x10, 0x1301BF, APP_SID),
]


# --- #107: services stay DEMAND_START until the commit point ---------------------


class _FakeWS:
    SERVICE_NO_CHANGE = 0xFFFFFFFF
    SERVICE_AUTO_START = 2
    SERVICE_DEMAND_START = 3
    SERVICE_CHANGE_CONFIG = 2
    SERVICE_CONFIG_FAILURE_ACTIONS_FLAG = 4
    SERVICE_CONFIG_FAILURE_ACTIONS = 2
    SC_ACTION_RESTART = 1
    SC_ACTION_NONE = 0

    def __init__(self):
        self.start_types = []

    def ChangeServiceConfig(self, handle, service_type, start_type, *rest):
        self.start_types.append((handle, start_type))

    def ChangeServiceConfig2(self, *args):
        pass


def _fake_scm(monkeypatch, bound):
    from src.api import lab_scm

    scm = object.__new__(lab_scm.NativeSCM)
    scm.ws = _FakeWS()
    scm._bound = None
    scm._bound_plans = {}

    @contextmanager
    def lease(registrations):
        scm._bound = {plan["name"]: bound.get(plan["name"]) for plan, _ in registrations}
        scm._bound_plans = {plan["name"]: (plan, image) for plan, image in registrations}
        try:
            yield
        finally:
            scm._bound = None

    scm.lease = lease
    scm._inspect_handle = lambda name, handle: {"state": "stopped"}
    monkeypatch.setattr(lab_scm, "owned", lambda *args: None)

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setitem(
        sys.modules,
        "winreg",
        types.SimpleNamespace(
            HKEY_LOCAL_MACHINE="HKLM",
            KEY_SET_VALUE=2,
            REG_SZ=1,
            OpenKey=lambda *args: Key(),
            SetValueEx=lambda *args: None,
        ),
    )
    return scm


def test_promotion_keeps_demand_start_before_install_commit(monkeypatch):
    """Failed before: promote() hard-coded SERVICE_AUTO_START (power-loss window)."""
    scm = _fake_scm(monkeypatch, {"TesterReleaseApp_x": "app-handle"})
    initial = dict(name="TesterReleaseApp_x")
    final = dict(
        name="TesterReleaseApp_x",
        dependency="TesterReleasePG_x",
        auto_start=False,
        recovery_delays_ms=[1000, 5000, 0],
        python_class="signed.ReleaseService",
    )
    scm.promote(initial, final, "host.exe")
    assert scm.ws.start_types == [("app-handle", _FakeWS.SERVICE_DEMAND_START)]
    scm.ws.start_types.clear()
    scm.promote(initial, dict(final, auto_start=True), "host.exe")
    assert scm.ws.start_types == [("app-handle", _FakeWS.SERVICE_AUTO_START)]


def test_post_commit_auto_start_switch_is_exact_and_idempotent(monkeypatch):
    scm = _fake_scm(monkeypatch, {"app": "h-app", "pg": "h-pg"})
    registrations = [(dict(name="app"), "img"), (dict(name="pg"), "img")]
    for _ in range(2):  # Re-run after a crash between commit and switch.
        scm.set_start_type(registrations, automatic=True)
    assert scm.ws.start_types == [("h-app", 2), ("h-pg", 2)] * 2
    missing = _fake_scm(monkeypatch, {"app": "h-app"})
    with pytest.raises(ValueError):
        missing.set_start_type(registrations, automatic=True)
    assert missing.ws.start_types == []


def _provisioning_header():
    from src.api.release_service import service_sid

    ident = uuid.uuid4().hex
    services = {"app": "TesterReleaseApp_" + ident, "postgres": "TesterReleasePG_" + ident}
    return (
        dict(actor_sid=service_sid(services["app"]), marker=dict(services=services)),
        service_sid(services["postgres"]),
    )


def test_rollback_accepts_own_postgres_sid_objects_only_inside_postgres_data():
    """Failed before: PostgreSQL-SID WAL/pid files made rollback refuse forever."""
    header, pg_sid = _provisioning_header()
    record = dict(
        owner=pg_sid,
        protected=False,
        aces=[
            (0, 0x10, 0x1F01FF, "S-1-5-18"),
            (0, 0x10, 0x1F01FF, "S-1-5-32-544"),
            (0, 0x10, 0x1301BF, pg_sid),
        ],
    )
    removal._provisioning_object_security(
        record, header, "postgres-data/pg_wal/000000010000000000000002", initial=False
    )
    for name, initial in (
        ("runtime/foreign", False),  # App tree: only the actor SID.
        ("dependencies/venv/x", False),
        ("postgres-data/x", True),  # Planned installer objects: never.
    ):
        with pytest.raises(ReleaseSecurityError):
            removal._provisioning_object_security(record, header, name, initial=initial)
    foreign = dict(record, owner="S-1-5-80-9-9-9-9-9")
    with pytest.raises(ReleaseSecurityError):
        removal._provisioning_object_security(foreign, header, "postgres-data/x", initial=False)
    widened = dict(record, aces=[*record["aces"], (0, 0x10, 0x1F01FF, "S-1-1-0")])
    with pytest.raises(ReleaseSecurityError):
        removal._provisioning_object_security(widened, header, "postgres-data/x", initial=False)


# --- #108A: documented kill switch ------------------------------------------------


def test_admin_created_kill_switch_is_accepted_but_nothing_else(monkeypatch):
    """Failed before: an Administrators-owned restart.disabled was a foreign creator."""
    boundary = dict(path="runtime", service_name="TesterReleaseApp_x", service_sid=APP_SID)
    record = dict(owner="S-1-5-32-544", protected=False, aces=list(INHERITED_RUNTIME_ACL))
    monkeypatch.setattr(removal, "_security_record", lambda obj: obj.record)

    def obj(**kwargs):
        values = dict(is_dir=False, size=0, record=record)
        values.update(kwargs)
        return types.SimpleNamespace(**values)

    for owner in ("S-1-5-32-544", "S-1-5-18", APP_SID):
        removal._writer_proof(
            obj(record=dict(record, owner=owner)), removal.KILL_SWITCH, boundary, known=False
        )
    refused = (
        (removal.KILL_SWITCH, obj(record=dict(record, owner="S-1-5-21-1-2-3-1001"))),
        (removal.KILL_SWITCH, obj(is_dir=True)),
        (removal.KILL_SWITCH, obj(size=removal.KILL_SWITCH_MAX_BYTES + 1)),
        (
            removal.KILL_SWITCH,
            obj(record=dict(record, aces=[*INHERITED_RUNTIME_ACL, (0, 0x10, 0x1F01FF, "S-1-1-0")])),
        ),
        (
            removal.KILL_SWITCH,
            obj(record=dict(record, aces=[*INHERITED_RUNTIME_ACL[:2], (0, 0, 0x1301BF, APP_SID)])),
        ),
        ("runtime/other.disabled", obj()),  # Only the exact documented name.
    )
    for name, item in refused:
        with pytest.raises(ReleaseSecurityError):
            removal._writer_proof(item, name, boundary, known=False)
    pg_boundary = dict(path="postgres-data", service_name="TesterReleasePG_x", service_sid=APP_SID)
    with pytest.raises(ReleaseSecurityError):
        removal._writer_proof(obj(), "postgres-data/restart.disabled", pg_boundary, known=False)


# --- R6: pre-stop preflight tolerates churn only of vanished objects -------------


def test_prestop_preflight_tolerates_vanished_objects_only_when_requested(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "kept").write_text("x")
    ledger = dict(
        root_identity=removal._identity(root),
        marker={},
        writer_boundaries=[],
        entries={
            "kept": dict(identity=removal._identity(root / "kept"), kind="file"),
            "vanished": dict(identity=[1, 2], kind="file"),
        },
    )
    monkeypatch.setattr(
        removal, "_tree", lambda base: {"kept": base / "kept", "vanished": base / "vanished"}
    )
    with pytest.raises(FileNotFoundError):
        removal._preflight(root, ledger)
    entries = removal._preflight(root, ledger, tolerate_churn=True)
    assert set(entries) == {"kept", "vanished"}
    (root / "unknown").write_text("foreign")
    monkeypatch.setattr(removal, "_tree", lambda base: {"unknown": base / "unknown"})
    with pytest.raises(ReleaseSecurityError):
        removal._preflight(root, ledger, tolerate_churn=True)


# --- #111(2): empty pre-created root ----------------------------------------------


def test_empty_unclaimed_root_is_routed_to_adoption_not_refused(tmp_path):
    """Failed before: empty roots reached root_created() and were refused forever."""
    from src.api.release_provision import recover_or_validate_existing

    root = tmp_path / ("TesterReleaseTest_" + uuid.uuid4().hex)
    root.mkdir()
    release = types.SimpleNamespace(manifest="{}", manifest_sha256="0" * 64, source_sha="a" * 40)
    assert (
        recover_or_validate_existing(root, release, trusted_public_key_sha256="0" * 64, mode="test")
        is None
    )
    (root / "foreign.txt").write_text("KEEP")
    with pytest.raises(ValueError):
        recover_or_validate_existing(root, release, trusted_public_key_sha256="0" * 64, mode="test")
    assert (root / "foreign.txt").read_text() == "KEEP"


def test_installer_registration_source_keeps_demand_until_runtime_exposed():
    import inspect

    source = inspect.getsource(ri._install_locked)
    commit = source.index('event="runtime_exposed"')
    assert source.index("dict(pg, auto_start=False)") < commit
    assert source.index("dict(app, auto_start=False)") < commit
    assert source.index("enable_automatic_start(marker)", commit) > commit
    assert "lifecycle_lock=_held_lock" in source  # R9: recovery runs under the lock.


# --- #111(1): lifecycle lock file -------------------------------------------------


def test_lifecycle_lock_path_is_outside_root(tmp_path):
    root = tmp_path / "TesterReleaseTest_x"
    path = ri.lifecycle_lock_path(root)
    assert path.parent == tmp_path and path.name.startswith(".tester-lifecycle-")
    if os.name != "nt":
        with ri.lifecycle_lock(root):
            pass
        assert ri.remove_lifecycle_lock(root) is False  # POSIX: unlink would break flock.


@windows_only
def test_lifecycle_lock_removed_only_when_not_held(tmp_path):
    root = tmp_path / "TesterReleaseTest_x"
    with ri.lifecycle_lock(root):
        assert ri.remove_lifecycle_lock(root) is False
        assert ri.lifecycle_lock_path(root).exists()
    assert ri.remove_lifecycle_lock(root) is True
    assert not ri.lifecycle_lock_path(root).exists()
    assert ri.remove_lifecycle_lock(root) is False


# --- #111(1): maintenance metadata removal and sweep ------------------------------


def _maintenance(tmp_path, ident, *, root, extra=()):
    directory = tmp_path / ("TesterReleaseMaintenance_" + ident.hex)
    (directory / "evidence").mkdir(parents=True)
    services = {"app": "TesterReleaseApp_" + ident.hex, "postgres": "TesterReleasePG_" + ident.hex}
    header = dict(
        schema="tester-provisioning/v1",
        root=str(root),
        installation_id=str(ident),
        marker=dict(installation_id=str(ident), services=services),
    )
    rows = [header, *extra]
    (directory / "provisioning.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (directory / "provisioning.rollback.jsonl").write_text("{}\n")
    (directory / "ownership.json").write_text("{}")
    (directory / "test-pause.json").write_text("{}")
    (directory / "evidence" / ("removal-" + uuid.uuid4().hex + ".jsonl")).write_text("{}\n")
    return directory


@windows_only
def test_maintenance_directory_removed_only_with_known_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(removal, "_validate_admin_security", lambda record: None)
    ident = uuid.uuid4()
    directory = _maintenance(tmp_path, ident, root=tmp_path / "root")
    with pytest.raises(ReleaseSecurityError):
        removal.remove_maintenance_directory(directory, installation_id=str(uuid.uuid4()))
    (directory / "foreign.txt").write_text("KEEP")
    with pytest.raises(ReleaseSecurityError):
        removal.remove_maintenance_directory(directory, installation_id=str(ident))
    assert (directory / "foreign.txt").read_text() == "KEEP"
    assert (directory / "provisioning.jsonl").exists()
    (directory / "foreign.txt").unlink()
    (directory / "trust.json").write_text("{}")  # A live installation's trust anchor.
    with pytest.raises(ReleaseSecurityError):
        removal.remove_maintenance_directory(directory, installation_id=str(ident))
    (directory / "trust.json").unlink()
    removal.remove_maintenance_directory(directory, installation_id=str(ident))
    assert not directory.exists()


@windows_only
def test_sweep_removes_only_provably_finished_attempts_for_this_root(tmp_path, monkeypatch):
    monkeypatch.setattr(removal, "_validate_admin_security", lambda record: None)
    root = tmp_path / "root"
    root.mkdir()
    current = uuid.uuid4()
    finished = _maintenance(tmp_path, uuid.uuid4(), root=root)
    other_root = _maintenance(tmp_path, uuid.uuid4(), root=tmp_path / "other")
    live = _maintenance(tmp_path, uuid.uuid4(), root=root)
    (live / "trust.json").write_text("{}")
    owner = _maintenance(
        tmp_path,
        uuid.uuid4(),
        root=root,
        extra=[dict(event="root_created", identity=removal._identity(root))],
    )
    registered_ident = uuid.uuid4()
    registered = _maintenance(tmp_path, registered_ident, root=root)
    mine = _maintenance(tmp_path, current, root=root)
    result = removal.sweep_orphan_maintenance(
        root,
        tmp_path,
        keep_installation_id=str(current),
        services_absent=lambda marker: marker["installation_id"] != str(registered_ident),
    )
    assert result["removed"] == [str(finished)]
    assert not finished.exists()
    for kept in (other_root, live, owner, registered, mine):
        assert kept.exists()


# --- #108B: Setup re-run recreated a deleted shortcut -----------------------------


class _Resources:
    def validate_resources(self, marker):
        return [None, None]

    def stop_and_prove_exit(self, marker):
        pass

    def remove_resources(self, marker):
        pass


@contextmanager
def _no_lock(root):
    yield


@windows_only
def test_recreated_shortcut_is_reenrolled_and_uninstall_completes(tmp_path, monkeypatch):
    """Failed before: uninstall refused with 'External shortcut identity changed'."""
    import hashlib

    from tests.platform.test_release_security import signed_bundle, verify

    monkeypatch.setattr(removal, "_validate_admin_security", lambda record: None)
    bundle, key_pin = signed_bundle(tmp_path)
    release = verify(bundle, key_pin)
    ident = uuid.uuid4()
    root = tmp_path / ("fresh-owned-" + ident.hex)
    root.mkdir()
    (root / "app.txt").write_bytes(b"TESTER")
    maintenance = tmp_path / ("TesterReleaseMaintenance_" + ident.hex)
    maintenance.mkdir()
    marker = dict(
        schema="tester-release-install/v1",
        installation_id=str(ident),
        root=str(root),
        source_sha="a" * 40,
        release_manifest_sha256=release.manifest_sha256,
        trust_config=str(maintenance / "trust.json"),
    )
    (root / ".tester-release.json").write_text(json.dumps(marker))
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    links = [
        desktop / "TESTER — Запуск.lnk",
        desktop / "TESTER — Удаление.lnk",
    ]
    for link in links:
        link.write_bytes(b"authorized dummy shortcut bytes")
    (desktop / "foreign.lnk").write_text("KEEP")
    ledger = maintenance / "ownership.json"
    pin = removal.write_ownership(
        root,
        marker,
        release=release,
        ledger_path=ledger,
        owned_paths=("app.txt", ".tester-release.json"),
        external_paths=links,
        trust_config_path=maintenance / "trust.json",
    )
    (maintenance / "trust.json").write_text(
        json.dumps(
            dict(
                schema="tester-maintenance-trust/v1",
                installation_id=str(ident),
                root=str(root),
                ledger=str(ledger),
                trusted_ledger_sha256=pin,
                trusted_public_key_sha256=key_pin,
                source_sha="a" * 40,
                manifest_sha256=release.manifest_sha256,
            )
        )
    )
    links[1].unlink()  # User deletes the uninstall shortcut; Setup recreates it.
    links[1].write_bytes(b"authorized dummy shortcut bytes")

    def run(ledger_path, digest, evidence):
        return removal.uninstall(
            root,
            ledger_path=ledger_path,
            trusted_ledger_sha256=digest,
            trusted_public_key_sha256=key_pin,
            expected_source_sha="a" * 40,
            confirmations=("DELETE TESTER", "YES"),
            evidence_path=tmp_path / evidence,
            resources=_Resources(),
            lifecycle_lock=_no_lock,
        )

    with pytest.raises(ReleaseSecurityError, match="External shortcut identity changed"):
        run(ledger, pin, "before.jsonl")
    protected = []
    new_ledger = removal.reenroll_external_shortcuts(
        root,
        marker,
        trusted_public_key_sha256=key_pin,
        expected_source_sha="a" * 40,
        protect=protected.append,
    )
    assert new_ledger and Path(new_ledger).parent == maintenance and not ledger.exists()
    assert Path(new_ledger) in protected
    config = json.loads((maintenance / "trust.json").read_text())
    assert config["ledger"] == new_ledger
    assert (
        config["trusted_ledger_sha256"] == hashlib.sha256(Path(new_ledger).read_bytes()).hexdigest()
    )
    assert (
        removal.reenroll_external_shortcuts(
            root,
            marker,
            trusted_public_key_sha256=key_pin,
            expected_source_sha="a" * 40,
            protect=protected.append,
        )
        is None
    )
    assert run(new_ledger, config["trusted_ledger_sha256"], "after.jsonl")["status"] == "removed"
    assert not root.exists() and not any(link.exists() for link in links)
    assert (desktop / "foreign.lnk").read_text() == "KEEP"


# --- PowerShell: CWD release (R1) and full maintenance purge ----------------------


def _prelude():
    return WRAPPER.read_text(encoding="utf-8").split("if (-not $TrustConfig)", 1)[0]


@windows_only
def test_uninstaller_releases_a_current_directory_inside_the_root(tmp_path):
    """Failed before: a shortcut CWD inside the root blocked the root deletion."""
    root = tmp_path / "fresh-root"
    root.mkdir()
    script = tmp_path / "cwd-proof.ps1"
    script.write_text(
        _prelude() + "\n[Console]::WriteLine('READY')\n[Console]::ReadLine() | Out-Null\n",
        encoding="utf-8",
    )
    child = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script)],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert child.stdout.readline().strip() == "READY"
        root.rename(tmp_path / "moved-root")
    finally:
        output, error = child.communicate("\n", timeout=30)
    assert child.returncode == 0, error + output


@windows_only
def test_powershell_purges_exact_maintenance_directory_and_refuses_foreign(tmp_path):
    ident = uuid.uuid4()
    directory = tmp_path / ("TesterReleaseMaintenance_" + ident.hex)
    (directory / "evidence").mkdir(parents=True)
    for name in (
        "provisioning.jsonl",
        "provisioning.rollback.jsonl",
        "ownership.json",
        "ownership-" + uuid.uuid4().hex + ".json",
        "trust.json",
        "maintenance-recovery.jsonl",
        "evidence/removal-" + uuid.uuid4().hex + ".jsonl",
    ):
        (directory / name).write_text("{}\n")
    script = tmp_path / "purge-proof.ps1"
    script.write_text(
        _prelude()
        + """
function Assert-AdminTrust([string]$Path, [bool]$StrictConfig=$false) { }
$dir = $env:TESTER_MAINTENANCE
$lease = New-Object TesterMaintenanceLease((Join-Path $dir 'trust.json'), $false)
$identity = $lease.Identity
$lease.Dispose()
Remove-OwnedMaintenanceDirectory $dir $env:TESTER_INSTALLATION $identity
""",
        encoding="utf-8",
    )

    def purge():
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script)],
            env={
                **os.environ,
                "TESTER_MAINTENANCE": str(directory),
                "TESTER_INSTALLATION": str(ident),
            },
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    (directory / "evidence" / "foreign.txt").write_text("KEEP")
    refused = purge()
    assert refused.returncode != 0
    assert (directory / "evidence" / "foreign.txt").read_text() == "KEEP"
    assert (directory / "trust.json").exists() and (directory / "provisioning.jsonl").exists()
    (directory / "evidence" / "foreign.txt").unlink()
    done = purge()
    assert done.returncode == 0, done.stderr + done.stdout
    assert not directory.exists()
