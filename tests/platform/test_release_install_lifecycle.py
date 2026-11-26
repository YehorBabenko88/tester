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
from contextlib import contextmanager, nullcontext
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

    SERVICE_CONFIG_PRESHUTDOWN_INFO = 7

    def __init__(self):
        self.start_types = []
        self.preshutdown = {}
        self.drop_preshutdown = False

    def ChangeServiceConfig(self, handle, service_type, start_type, *rest):
        self.start_types.append((handle, start_type))

    def ChangeServiceConfig2(self, handle, level, value):
        if level == self.SERVICE_CONFIG_PRESHUTDOWN_INFO and not self.drop_preshutdown:
            self.preshutdown[handle] = value

    def QueryServiceConfig2(self, handle, level):
        assert level == self.SERVICE_CONFIG_PRESHUTDOWN_INFO
        return self.preshutdown.get(handle, 180000)


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


# --- #111(2) / round 2: empty pre-created root is replaced, never adopted ---------

ADMINS, SYSTEM, USERS, CREATOR_OWNER = "S-1-5-32-544", "S-1-5-18", "S-1-5-32-545", "S-1-3-0"
USER_SID = "S-1-5-21-1-2-3-1001"
CLEAN_ROOT = dict(
    owner=ADMINS,
    protected=True,
    aces=[(0, 3, 0x1F01FF, SYSTEM), (0, 3, 0x1F01FF, ADMINS)],
)


class _FakeRootOps:
    """Injected security/handle probe standing in for the native exclusive handle."""

    def __init__(self, record=CLEAN_ROOT, *, foreign_handle=False, is_dir=True):
        self.record, self.foreign_handle, self.is_dir = record, foreign_handle, is_dir
        self.calls = []

    def open_exclusive(self, path):
        self.calls.append("open")
        if self.foreign_handle:
            from src.api.release_provision import empty_root_refusal

            raise empty_root_refusal(path)  # ERROR_SHARING_VIOLATION on share mode 0.
        return types.SimpleNamespace(
            path=path, identity=[1, path.stat().st_ino], is_dir=self.is_dir
        )

    def security(self, obj):
        self.calls.append("security")
        return self.record

    def delete(self, obj):
        self.calls.append("delete")
        obj.path.rmdir()

    def close(self, obj):
        self.calls.append("close")

    def identity(self, path):
        return [2, path.stat().st_ino]


def _recover(root, ops):
    from src.api.release_provision import recover_or_validate_existing

    release = types.SimpleNamespace(manifest="{}", manifest_sha256="0" * 64, source_sha="a" * 40)
    return recover_or_validate_existing(
        root, release, trusted_public_key_sha256="0" * 64, mode="test", root_ops=ops
    )


def test_empty_unclaimed_clean_admin_root_is_routed_to_replacement(tmp_path):
    """Admin-owned clean empty root (e.g. TESTER's own interrupted create) stays recoverable."""
    root = tmp_path / ("TesterReleaseTest_" + uuid.uuid4().hex)
    root.mkdir()
    ops = _FakeRootOps()
    assert _recover(root, ops) is None
    assert ops.calls == ["open", "security", "close"]  # Proven, nothing deleted yet.
    (root / "foreign.txt").write_text("KEEP")
    with pytest.raises(ValueError):
        _recover(root, _FakeRootOps())
    assert (root / "foreign.txt").read_text() == "KEEP"


@pytest.mark.parametrize(
    "record",
    [
        # Standard user created the folder (cannot assign Administrators as owner).
        dict(CLEAN_ROOT, owner=USER_SID),
        # Admin-owned but a user may still write / add subdirectories.
        dict(CLEAN_ROOT, aces=[*CLEAN_ROOT["aces"], (0, 0x10, 0x1301BF, USERS)]),
        # Inherited "Authenticated Users: create folders" as under C:\.
        dict(CLEAN_ROOT, aces=[*CLEAN_ROOT["aces"], (0, 0x10, 0x4, "S-1-5-11")]),
        # Only WRITE_DAC for the user is enough to take the root back later.
        dict(CLEAN_ROOT, aces=[*CLEAN_ROOT["aces"], (0, 0, 0x40000, USER_SID)]),
        # CREATOR OWNER inherit-only full control is refused too.
        dict(CLEAN_ROOT, aces=[*CLEAN_ROOT["aces"], (0, 0x1B, 0x10000000, CREATOR_OWNER)]),
        # Unsupported (callback/conditional) allow entry fails closed.
        dict(CLEAN_ROOT, aces=[*CLEAN_ROOT["aces"], (9, 0, 0x1200A9, USERS)]),
    ],
    ids=[
        "user-owner",
        "users-write",
        "auth-users-add-subdir",
        "user-write-dac",
        "creator-owner",
        "callback-ace",
    ],
)
def test_empty_root_with_foreign_creator_or_write_access_is_refused(tmp_path, record):
    """Failed before: any empty root was adopted by SetFileSecurity (P1 privilege escalation)."""
    from src.api.release_provision import replace_empty_root

    root = tmp_path / "TesterLab"
    root.mkdir()
    ops = _FakeRootOps(record)
    with pytest.raises(ValueError, match="Remove the empty folder"):
        _recover(root, ops)
    created = []
    with pytest.raises(ValueError, match="Remove the empty folder"):
        replace_empty_root(root, lambda: created.append(True), ops=_FakeRootOps(record))
    assert root.is_dir() and not created
    assert "delete" not in ops.calls


def test_read_only_foreign_access_and_deny_entries_do_not_block_replacement():
    from src.api.release_provision import validate_replaceable_root_security

    validate_replaceable_root_security(
        dict(
            CLEAN_ROOT,
            aces=[
                *CLEAN_ROOT["aces"],
                (0, 0x10, 0x1200A9, USERS),
                (1, 0, 0x1F01FF, USER_SID),
            ],
        )
    )


def test_empty_root_open_by_foreign_handle_is_refused(tmp_path):
    from src.api.release_provision import replace_empty_root

    root = tmp_path / "TesterLab"
    root.mkdir()
    ops = _FakeRootOps(foreign_handle=True)
    with pytest.raises(ValueError, match="Remove the empty folder"):
        _recover(root, ops)
    created = []
    with pytest.raises(ValueError, match="Remove the empty folder"):
        replace_empty_root(root, lambda: created.append(True), ops=ops)
    assert root.is_dir() and not created and "delete" not in ops.calls


def test_clean_admin_empty_root_is_deleted_and_created_fresh(tmp_path):
    from src.api.release_provision import replace_empty_root

    root = tmp_path / "TesterLab"
    root.mkdir()
    ops = _FakeRootOps()
    old_inode = root.stat().st_ino
    order = []

    def create():
        assert not root.exists(), "old directory object must be gone before creation"
        order.append("create")
        root.mkdir()  # Atomic create-new: fails if anything reappeared.

    replace_empty_root(root, create, ops=ops)
    assert ops.calls == ["open", "security", "delete", "close"]
    assert order == ["create"] and root.is_dir()
    assert [1, old_inode] != ops.identity(root)


def test_replacement_refuses_when_name_is_reoccupied_or_delete_fails(tmp_path):
    from src.api.release_provision import replace_empty_root

    root = tmp_path / "TesterLab"
    root.mkdir()

    def raced():
        raise FileExistsError(str(root))  # Delete-pending name or attacker re-created it.

    with pytest.raises(ValueError, match="Remove the empty folder"):
        replace_empty_root(root, raced, ops=_FakeRootOps())

    root.mkdir()
    ops = _FakeRootOps()
    ops.delete = lambda obj: (_ for _ in ()).throw(OSError(145, "Directory not empty"))
    created = []
    with pytest.raises(ValueError, match="Remove the empty folder"):
        replace_empty_root(root, lambda: created.append(True), ops=ops)
    assert root.is_dir() and not created


def test_provisioning_root_is_never_adopted_by_descriptor_rewrite():
    import inspect

    from src.api import release_provision as rp

    source = inspect.getsource(rp.ProvisioningTransaction.root_created)
    assert "SetFileSecurity" not in source and "adopt_empty" not in source
    assert "replace_empty_root(self.root, create)" in source
    assert "tx.root_created(replace_empty=replace_empty_root)" in inspect.getsource(
        ri._install_locked
    )


def _admin_directory(path, extra_aces=(), *, admin_owner=True):
    import pywintypes
    import win32file
    import win32security

    descriptor = win32security.SECURITY_DESCRIPTOR()
    try:
        if admin_owner:
            descriptor.SetSecurityDescriptorOwner(
                win32security.ConvertStringSidToSid(ADMINS), False
            )
        acl = win32security.ACL()
        for sid, mask in ((SYSTEM, 0x1F01FF), (ADMINS, 0x1F01FF), *extra_aces):
            acl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION_DS, 3, mask, win32security.ConvertStringSidToSid(sid)
            )
        descriptor.SetSecurityDescriptorDacl(True, acl, False)
        descriptor.SetSecurityDescriptorControl(
            win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED
        )
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        win32file.CreateDirectory(str(path), attributes)
    except pywintypes.error as exc:
        if exc.winerror == 1307:  # ERROR_INVALID_OWNER: token cannot assign Administrators.
            pytest.skip("Token cannot assign the Administrators owner SID")
        raise


@windows_only
def test_native_empty_root_granting_users_write_is_refused(tmp_path):
    from src.api.release_provision import prove_replaceable_empty_root, replace_empty_root

    root = tmp_path / "TesterLab"
    # Default token owner: refused by the Users write ACE whatever the owner is.
    _admin_directory(root, extra_aces=[(USERS, 0x1301BF)], admin_owner=False)
    with pytest.raises(ValueError, match="Remove the empty folder"):
        prove_replaceable_empty_root(root)
    created = []
    with pytest.raises(ValueError, match="Remove the empty folder"):
        replace_empty_root(root, lambda: created.append(True))
    assert root.is_dir() and not created


@windows_only
def test_native_clean_empty_root_is_recreated_and_foreign_handle_refused(tmp_path):
    import win32con
    import win32file

    from src.api.release_provision import replace_empty_root

    root = tmp_path / "TesterLab"
    _admin_directory(root)
    previous = removal._identity(root)
    # Any foreign handle with data access (here FILE_ADD_SUBDIRECTORY) blocks share mode 0.
    foreign = win32file.CreateFile(
        str(root),
        0x4 | 0x40000,
        win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
        None,
        win32con.OPEN_EXISTING,
        win32file.FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    try:
        with pytest.raises(ValueError, match="Remove the empty folder"):
            replace_empty_root(root, lambda: pytest.fail("must not create"))
    finally:
        foreign.Close()
    assert removal._identity(root) == previous
    replace_empty_root(root, lambda: _admin_directory(root))
    assert root.is_dir() and removal._identity(root) != previous


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


def test_preshutdown_timeouts_registered_and_verified_for_both_services(monkeypatch):
    """Agent A request: bounded PRESHUTDOWN drain time, re-applied idempotently."""
    app = dict(name="app", preshutdown_timeout_ms=ri.PRESHUTDOWN_TIMEOUT_MS["app"])
    pg = dict(name="pg", preshutdown_timeout_ms=ri.PRESHUTDOWN_TIMEOUT_MS["postgres"])
    scm = _fake_scm(monkeypatch, {"app": "h-app", "pg": "h-pg"})
    for _ in range(2):
        scm.set_start_type([(app, "img"), (pg, "img")], automatic=True)
    assert scm.ws.preshutdown == {"h-app": 60000, "h-pg": 90000}
    promoted = _fake_scm(monkeypatch, {"app": "h-app"})
    promoted.promote(
        dict(name="app"),
        dict(app, dependency="pg", recovery_delays_ms=[1000, 5000, 0], python_class="c"),
        "img",
    )
    assert promoted.ws.preshutdown == {"h-app": 60000}
    ignored = _fake_scm(monkeypatch, {"app": "h-app", "pg": "h-pg"})
    ignored.ws.drop_preshutdown = True  # SCM silently ignoring the setting must fail.
    with pytest.raises(ValueError):
        ignored.set_start_type([(app, "img"), (pg, "img")], automatic=True)
    with pytest.raises(ValueError):
        scm._apply_preshutdown("h-app", 10**9)
    marker_plans = ri._with_preshutdown(dict(name="a"), dict(name="p"))
    assert [plan["preshutdown_timeout_ms"] for plan in marker_plans] == [60000, 90000]


# --- Re-audit #2 (P3): a root re-created by a third party before root_created ------
# The provisioning journal has no root_created receipt, so TESTER never owned the
# object now at the root path. Rollback must not touch it, must still clean this
# attempt's own metadata, and the original refusal must stay the reported error.


class _RootProbe:
    """Any native open/inspection of the root (or a tree under it) is recorded."""

    def __init__(self, monkeypatch, root):
        self.touched = []
        root = Path(root)
        native = removal._WindowsObject

        def refuse(name):
            self.touched.append(name)
            # What the real Windows proof raises for a user-created folder.
            raise ReleaseSecurityError("Unknown provisioning object has a foreign creator SID")

        def windows_object(path, *args, **kwargs):
            # Ancestors (e.g. the parent identity proof) stay native on Windows.
            if Path(path) == root or root in Path(path).parents:
                refuse("_WindowsObject")
            return native(path, *args, **kwargs)

        monkeypatch.setattr(removal, "_WindowsObject", windows_object)
        for name in ("_provisioning_entries", "_locked_tree"):
            monkeypatch.setattr(removal, name, lambda *a, _n=name, **k: refuse(_n))


@contextmanager
def _linux_protected_journal(path, *, write=False):
    # Isolates only the native protected-journal handle; parsing and signature
    # verification of the journal content stay real.
    class _Journal:
        def read_bytes(self, max_bytes):
            return Path(path).read_bytes()[:max_bytes]

    yield _Journal()


class _RecordingResources:
    def __init__(self):
        self.events = []

    def validate_resources(self, marker):
        self.events.append("validate")

    def stop_and_prove_exit(self, marker):
        self.events.append("stop")

    def remove_resources(self, marker):
        self.events.append("remove")


def _journal_without_root_created(tmp_path):
    from tests.platform.test_release_uninstall import provisioning_journal

    root, pin, header, rows, _ = provisioning_journal(tmp_path)
    (root / "app.txt").unlink()
    (root / "FOREIGN-USER-DATA.txt").write_text("KEEP")
    rows = [header, dict(event="phase", phase="interrupted")]
    journal = tmp_path / "TesterReleaseMaintenance_journal.jsonl"
    journal.write_bytes(b"".join(removal.canonical_json(row) + b"\n" for row in rows))
    return root, pin, header, rows, journal


def test_rollback_without_root_created_never_touches_the_foreign_root(tmp_path, monkeypatch):
    root, pin, header, rows, journal = _journal_without_root_created(tmp_path)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", _linux_protected_journal)
    probe = _RootProbe(monkeypatch, root)
    resources = _RecordingResources()
    result = removal.rollback_provisioning(
        root,
        journal_path=journal,
        trusted_public_key_sha256=pin,
        expected_source_sha="a" * 40,
        resources=resources,
        lifecycle_lock=lambda _: nullcontext(),
    )
    assert result["status"] == "rolled_back"
    assert result["root_ownership"] == "never_recorded"
    assert probe.touched == []
    assert (root / "FOREIGN-USER-DATA.txt").read_text() == "KEEP"
    assert resources.events == ["validate", "stop", "validate", "remove"]
    assert removal.read_evidence(journal.with_suffix(".rollback.jsonl"))["status"] == "rolled_back"


def test_rollback_refuses_creation_receipts_without_a_recorded_root(tmp_path, monkeypatch):
    from tests.platform.test_release_uninstall import provisioning_journal

    root, pin, header, rows, _ = provisioning_journal(tmp_path)
    journal = tmp_path / "journal.jsonl"
    rows = [row for row in rows if row.get("event") != "root_created"]
    journal.write_bytes(b"".join(removal.canonical_json(row) + b"\n" for row in rows))
    monkeypatch.setattr(removal, "_protected_provisioning_journal", _linux_protected_journal)
    probe = _RootProbe(monkeypatch, root)
    with pytest.raises(ReleaseSecurityError, match="without a recorded root"):
        removal.rollback_provisioning(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=_RecordingResources(),
            lifecycle_lock=lambda _: nullcontext(),
        )
    assert probe.touched == [] and (root / "app.txt").exists()


def test_rollback_after_root_created_still_proves_the_root(tmp_path, monkeypatch):
    from tests.platform.test_release_uninstall import provisioning_journal

    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(encoded)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", _linux_protected_journal)
    probe = _RootProbe(monkeypatch, root)
    with pytest.raises(ReleaseSecurityError, match="foreign creator"):
        removal.rollback_provisioning(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=_RecordingResources(),
            lifecycle_lock=lambda _: nullcontext(),
        )
    assert probe.touched == ["_provisioning_entries"]
    assert (root / "app.txt").exists()


def _drive_fresh_install(tmp_path, monkeypatch, *, record_root_created):
    """Run _install_locked up to tx.root_created with only native seams injected."""
    import shutil

    from src.api import release_provision as rp
    from tests.platform.test_release_uninstall import provisioning_journal

    (tmp_path / "template").mkdir()
    _, pin, template, _, _ = provisioning_journal(tmp_path / "template")
    bundle = tmp_path / "bundle"
    for relative in (
        "payload/src/api/supervisor.py",
        "payload/frontend/index.html",
        "payload/requirements-release-windows314.lock",
        "runtimes/python/python.exe",
        "runtimes/postgres/bin/initdb.exe",
        "runtimes/postgres/bin/pg_ctl.exe",
    ):
        (bundle / relative).parent.mkdir(parents=True, exist_ok=True)
        (bundle / relative).write_bytes(b"x")
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    root = tmp_path / "TesterLab"
    root.mkdir()  # Empty clean root: routed to replace_empty_root.
    root_identity = removal._identity(root)
    release = types.SimpleNamespace(
        source_sha="a" * 40, manifest_sha256=template["release_manifest_sha256"]
    )
    cleaned = []

    class FakeTransaction:
        def __init__(self, root, marker, release, maintenance):
            self.root, self.maintenance = root, maintenance
            self.path = maintenance / "provisioning.jsonl"
            self.root_identity = None
            header = dict(
                template,
                root=str(root),
                installation_id=marker["installation_id"],
                marker=marker,
                root_parent_identity=removal._identity(root.parent),
            )
            self.path.write_bytes(removal.canonical_json(header) + b"\n")

        def append(self, row):
            with self.path.open("ab") as stream:
                stream.write(removal.canonical_json(row) + b"\n")

        def root_created(self, *, replace_empty=False):
            assert replace_empty
            if record_root_created:
                self.root_identity = root_identity
                self.append(dict(event="root_created", identity=self.root_identity))
                raise RuntimeError("later provisioning failure")
            # Race: the old root was deleted by handle, then a standard user
            # re-created it before CreateDirectory, which therefore failed.
            (self.root / "FOREIGN-USER-DATA.txt").write_text("KEEP")
            raise rp.empty_root_refusal(self.root) from FileExistsError(str(self.root))

        def release_guards(self):
            pass

        def close(self):
            pass

    def cleanup(directory, *, installation_id):
        cleaned.append(directory)
        shutil.rmtree(directory)

    monkeypatch.setattr(rp, "recover_or_validate_existing", lambda *a, **k: None)
    monkeypatch.setattr(rp, "ProvisioningTransaction", FakeTransaction)
    for name in ("validate_resources", "stop_and_prove_exit", "remove_resources"):
        monkeypatch.setattr(rp, name, lambda marker: None)
    monkeypatch.setattr(ri, "protect_external_bundle", lambda directory: None)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", _linux_protected_journal)
    monkeypatch.setattr(removal, "remove_maintenance_directory", cleanup)
    probe = _RootProbe(monkeypatch, root)
    call = lambda: ri._install_locked(  # noqa: E731
        bundle, root, release, trusted_public_key_sha256=pin, mode="test", desktop=desktop
    )
    return root, call, probe, cleaned


def test_root_race_before_root_created_surfaces_original_refusal(tmp_path, monkeypatch):
    root, call, probe, cleaned = _drive_fresh_install(
        tmp_path, monkeypatch, record_root_created=False
    )
    with pytest.raises(ValueError, match="Remove the empty folder and run Setup again"):
        call()
    assert probe.touched == []
    assert (root / "FOREIGN-USER-DATA.txt").read_text() == "KEEP"
    assert len(cleaned) == 1 and not cleaned[0].exists()
    assert not list(tmp_path.glob("TesterReleaseMaintenance_*"))


def test_failure_after_root_created_keeps_fail_closed_rollback(tmp_path, monkeypatch):
    root, call, probe, cleaned = _drive_fresh_install(
        tmp_path, monkeypatch, record_root_created=True
    )
    with pytest.raises(ReleaseSecurityError, match="foreign creator"):
        call()
    assert probe.touched == ["_provisioning_entries"]
    assert root.is_dir() and cleaned == []
    (maintenance,) = tmp_path.glob("TesterReleaseMaintenance_*")
    assert (maintenance / "provisioning.jsonl").is_file()


def test_refused_rollback_before_root_created_never_masks_original(tmp_path, monkeypatch):
    from src.api import release_provision as rp

    root, call, probe, cleaned = _drive_fresh_install(
        tmp_path, monkeypatch, record_root_created=False
    )

    def refused(marker):
        raise ReleaseSecurityError("service of a different installation")

    monkeypatch.setattr(rp, "validate_resources", refused)
    with pytest.raises(ValueError, match="Remove the empty folder and run Setup again") as info:
        call()
    assert isinstance(info.value.__context__, ReleaseSecurityError)
    assert any("authenticated rollback failed" in n for n in info.value.__notes__)
    assert probe.touched == [] and cleaned == []
    assert (root / "FOREIGN-USER-DATA.txt").read_text() == "KEEP"
    (maintenance,) = tmp_path.glob("TesterReleaseMaintenance_*")
    assert (maintenance / "provisioning.jsonl").is_file()


def test_interrupted_journal_append_never_masks_original_root_refusal(tmp_path, monkeypatch):
    """C1: even a failed interrupted-phase receipt preserves the foreign root."""
    from src.api import release_provision as rp

    root, call, probe, cleaned = _drive_fresh_install(
        tmp_path, monkeypatch, record_root_created=False
    )
    # The helper owns its private FakeTransaction type. Patch the injected
    # constructor so a journal write failure can be simulated deterministically.
    saved = rp.ProvisioningTransaction

    def transaction(*args, **kwargs):
        tx = saved(*args, **kwargs)
        append = tx.append

        def inject(row):
            if row.get("event") == "phase" and row.get("phase") == "interrupted":
                raise OSError("injected journal disk failure")
            return append(row)

        tx.append = inject
        return tx

    monkeypatch.setattr(rp, "ProvisioningTransaction", transaction)
    with pytest.raises(ValueError, match="Remove the empty folder and run Setup again") as info:
        call()
    assert any("interrupted journal append failed" in n for n in info.value.__notes__)
    assert probe.touched == []
    assert (root / "FOREIGN-USER-DATA.txt").read_text() == "KEEP"
    assert cleaned == []
    # Incomplete journal evidence is retained for authenticated recovery.
    (maintenance,) = tmp_path.glob("TesterReleaseMaintenance_*")
    assert (maintenance / "provisioning.jsonl").is_file()


def test_root_creation_rollback_refusal_preserves_primary_failure(tmp_path, monkeypatch):
    """C1: owned-root rollback refusal remains secondary to original failure."""
    from src.api import release_provision as rp

    root, call, probe, cleaned = _drive_fresh_install(
        tmp_path, monkeypatch, record_root_created=True
    )
    saved = rp.ProvisioningTransaction

    def transaction(*args, **kwargs):
        tx = saved(*args, **kwargs)
        append = tx.append

        def inject(row):
            if row.get("event") == "phase" and row.get("phase") == "interrupted":
                raise OSError("injected journal disk failure")
            return append(row)

        tx.append = inject
        return tx

    monkeypatch.setattr(rp, "ProvisioningTransaction", transaction)
    with pytest.raises(RuntimeError, match="later provisioning failure") as info:
        call()
    assert any("interrupted journal append failed" in n for n in info.value.__notes__)
    # No deletion when the interruption journal did not persist.
    assert probe.touched == []
    assert root.is_dir() and cleaned == []
    (maintenance,) = tmp_path.glob("TesterReleaseMaintenance_*")
    assert (maintenance / "provisioning.jsonl").is_file()


def test_guard_release_failure_does_not_hide_original_refusal(tmp_path, monkeypatch):
    """A guard error must not silently change fail-closed rollback decisions."""
    from src.api import release_provision as rp

    root, call, probe, cleaned = _drive_fresh_install(
        tmp_path, monkeypatch, record_root_created=False
    )
    saved = rp.ProvisioningTransaction

    def transaction(*args, **kwargs):
        tx = saved(*args, **kwargs)

        def broken_release():
            raise OSError("injected guard close failure")

        tx.release_guards = broken_release
        return tx

    monkeypatch.setattr(rp, "ProvisioningTransaction", transaction)
    with pytest.raises(ValueError, match="Remove the empty folder and run Setup again") as info:
        call()
    assert any("provisioning guard release failed" in n for n in info.value.__notes__)
    assert (root / "FOREIGN-USER-DATA.txt").read_text() == "KEEP"
    assert probe.touched == []
    # Abnormal guard failure retains the journal for separately authenticated retry.
    assert cleaned == []
    assert len(list(tmp_path.glob("TesterReleaseMaintenance_*"))) == 1
