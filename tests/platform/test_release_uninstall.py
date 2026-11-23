"""Destructive tests operate only on newly created pytest temporary installations."""

import json
import os
import uuid
from contextlib import contextmanager

import pytest

from src.api import release_uninstall as removal
from src.api.release_security import ReleaseSecurityError
from tests.platform.test_release_security import signed_bundle, verify

windows_only = pytest.mark.skipif(
    os.name != "nt", reason="Native Windows handle/SCM/PowerShell acceptance requires Windows"
)


class Resources:
    def __init__(self):
        self.events = []

    def validate_resources(self, marker):
        self.events.append("validate")

    def stop_and_prove_exit(self, marker):
        self.events.append("stop")

    def remove_resources(self, marker):
        self.events.append("remove")


@contextmanager
def fixture_lock(root):
    yield


def installation(tmp_path):
    bundle, key_pin = signed_bundle(tmp_path)
    release = verify(bundle, key_pin)
    root = tmp_path / ("fresh-owned-" + uuid.uuid4().hex)
    root.mkdir()
    (root / "app.txt").write_bytes(b"TESTER")
    (root / "state").mkdir()
    (root / "state" / "candles").write_text("authorized dummy candles")
    ident = str(uuid.uuid4())
    marker = dict(
        schema="tester-release-install/v1",
        installation_id=ident,
        root=str(root),
        source_sha="a" * 40,
        release_manifest_sha256=release.manifest_sha256,
    )
    (root / ".tester-release.json").write_text(json.dumps(marker))
    ledger = tmp_path / "protected-ledger.json"
    paths = tuple(p.relative_to(root).as_posix() for p in root.rglob("*"))
    pin = removal.write_ownership(
        root, marker, release=release, ledger_path=ledger, owned_paths=paths
    )
    return root, ledger, pin, key_pin


def run(tmp_path, values, resources=None, **kwargs):
    root, ledger, pin, key_pin = values
    return removal.uninstall(
        root,
        ledger_path=ledger,
        trusted_ledger_sha256=pin,
        trusted_public_key_sha256=key_pin,
        expected_source_sha="a" * 40,
        confirmations=("DELETE TESTER", "YES"),
        evidence_path=tmp_path / "removal.json",
        resources=resources or Resources(),
        lifecycle_lock=fixture_lock,
        **kwargs,
    )


@windows_only
def test_full_owned_dummy_install_removal_and_idempotence(tmp_path):
    values = installation(tmp_path)
    foreign = tmp_path / "foreign-sentinel"
    foreign.write_text("KEEP")
    resources = Resources()
    result = run(tmp_path, values, resources)
    assert result["status"] == "removed"
    assert not values[0].exists()
    assert foreign.read_text() == "KEEP"
    assert resources.events == ["validate", "stop", "validate", "remove"]
    assert removal.read_evidence(tmp_path / "removal.json")["status"] == "removed"
    assert run(tmp_path, values)["status"] == "already_removed"


@pytest.mark.parametrize(
    "mutation", ["foreign", "hardlink", "directory_swap", "file_swap", "marker", "ledger"]
)
def test_unknown_or_swapped_resources_never_deleted(tmp_path, mutation):
    values = installation(tmp_path)
    root, ledger, _, _ = values
    if mutation == "foreign":
        (root / "unknown").write_text("FOREIGN")
    elif mutation == "hardlink":
        (tmp_path / "foreign").hardlink_to(root / "app.txt")
    elif mutation == "directory_swap":
        (root / "state").rename(root / "state-original")
        (root / "state").mkdir()
    elif mutation == "file_swap":
        (root / "state" / "candles").rename(tmp_path / "original")
        (root / "state" / "candles").write_text("FOREIGN")
    elif mutation == "marker":
        (root / ".tester-release.json").write_text("{}")
    else:
        ledger.write_text("{}")
    with pytest.raises((ReleaseSecurityError, OSError)):
        run(tmp_path, values)
    assert root.exists()
    assert (root / "app.txt").exists()
    assert removal.read_evidence(tmp_path / "removal.json")["status"] == "refused_or_partial"


def test_wrong_confirmation_has_no_side_effects(tmp_path):
    values = installation(tmp_path)
    root, ledger, pin, key_pin = values
    with pytest.raises(ReleaseSecurityError):
        removal.uninstall(
            root,
            ledger_path=ledger,
            trusted_ledger_sha256=pin,
            trusted_public_key_sha256=key_pin,
            expected_source_sha="a" * 40,
            confirmations=("DELETE TESTER", "NO"),
            evidence_path=tmp_path / "evidence.json",
            resources=Resources(),
            lifecycle_lock=fixture_lock,
        )
    assert root.exists()


def test_refuses_adopting_foreign_files_in_ownership_writer(tmp_path):
    bundle, pin = signed_bundle(tmp_path)
    release = verify(bundle, pin)
    root = tmp_path / "fresh"
    root.mkdir()
    (root / "foreign").write_text("KEEP")
    marker = {"root": str(root), "installation_id": str(uuid.uuid4())}
    with pytest.raises(ReleaseSecurityError):
        removal.write_ownership(
            root, marker, release=release, ledger_path=tmp_path / "ledger.json", owned_paths=()
        )


@windows_only
def test_locked_file_refuses_all_deletion_then_retry(tmp_path):
    values = installation(tmp_path)
    root = values[0]
    with (root / "state" / "candles").open("rb"):
        with pytest.raises(OSError):
            run(tmp_path, values)
    assert (root / "app.txt").exists()
    assert root.exists()
    (tmp_path / "removal.json").rename(tmp_path / "first-failure.json")
    assert run(tmp_path, values)["status"] == "removed"


@windows_only
def test_directory_swap_attempt_during_destruction_is_blocked_by_handles(tmp_path):
    values = installation(tmp_path)
    root = values[0]

    class Swap(Resources):
        def remove_resources(self, marker):
            super().remove_resources(marker)
            with pytest.raises(OSError):
                (root / "state").rename(tmp_path / "stolen")

    assert run(tmp_path, values, Swap())["status"] == "removed"
    assert not (tmp_path / "stolen").exists()


@windows_only
def test_partial_deletion_has_durable_evidence_and_can_resume(tmp_path, monkeypatch):
    values = installation(tmp_path)
    original = removal._WindowsObject.delete
    calls = []

    def interrupted(obj):
        if len(calls) == 1:
            raise PermissionError("simulated access denial after one real deletion")
        original(obj)
        calls.append(str(obj.path))

    monkeypatch.setattr(removal._WindowsObject, "delete", interrupted)
    with pytest.raises(PermissionError):
        run(tmp_path, values)
    evidence = removal.read_evidence(tmp_path / "removal.json")
    assert evidence["status"] == "refused_or_partial"
    assert evidence["deleted_count"] == 1
    assert values[0].exists()
    monkeypatch.setattr(removal._WindowsObject, "delete", original)
    (tmp_path / "removal.json").rename(tmp_path / "partial.json")
    assert run(tmp_path, values)["status"] == "removed"


@pytest.mark.parametrize("kind", ["file_symlink", "dir_symlink"])
def test_symlink_foreign_targets_preserved(tmp_path, kind):
    values = installation(tmp_path)
    root = values[0]
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "sentinel").write_text("KEEP")
    try:
        if kind == "file_symlink":
            (root / "link").symlink_to(foreign / "sentinel")
        else:
            (root / "link").symlink_to(foreign, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip(
                "Windows token lacks SeCreateSymbolicLinkPrivilege; actual symlink acceptance NOT RUN"
            )
        raise
    with pytest.raises(ReleaseSecurityError):
        run(tmp_path, values)
    assert (foreign / "sentinel").read_text() == "KEEP"
    assert (root / "app.txt").exists()


@windows_only
def test_foreign_file_created_after_lock_never_deleted(tmp_path):
    values = installation(tmp_path)
    root = values[0]

    class Foreign(Resources):
        def remove_resources(self, marker):
            (root / "foreign-late").write_text("KEEP")

    with pytest.raises(OSError):
        run(tmp_path, values, Foreign())
    assert (root / "foreign-late").read_text() == "KEEP"
    assert root.exists()


def test_resource_swap_rejection_stops_before_file_deletion(tmp_path):
    values = installation(tmp_path)

    class ForeignSCM(Resources):
        def validate_resources(self, marker):
            raise ReleaseSecurityError("SCM ownership swapped")

    with pytest.raises(ReleaseSecurityError):
        run(tmp_path, values, ForeignSCM())
    assert (values[0] / "app.txt").read_bytes() == b"TESTER"
    assert (values[0] / "state" / "candles").exists()


def test_root_directory_swap_before_removal_rejected(tmp_path):
    values = installation(tmp_path)
    root = values[0]
    root.rename(tmp_path / "original-root")
    root.mkdir()
    (root / "FOREIGN").write_text("KEEP")
    with pytest.raises(ReleaseSecurityError):
        run(tmp_path, values)
    assert (root / "FOREIGN").read_text() == "KEEP"
    assert (tmp_path / "original-root" / "app.txt").exists()


def test_ledger_cannot_be_swapped_between_digest_and_parse(tmp_path, monkeypatch):
    values = installation(tmp_path)
    root, ledger_path, _, _ = values
    (root / "foreign").write_text("KEEP")
    original = removal.sha256

    def swapped(path):
        digest = original(path)
        if path == ledger_path:
            ledger = json.loads(ledger_path.read_text())
            ledger["entries"]["foreign"] = {
                "identity": removal._identity(root / "foreign"),
                "kind": "file",
            }
            ledger_path.write_bytes(removal.canonical_json(ledger))
        return digest

    monkeypatch.setattr(removal, "sha256", swapped)
    with pytest.raises(ReleaseSecurityError):
        run(tmp_path, values)
    assert (root / "foreign").read_text() == "KEEP"


@windows_only
def test_real_junction_is_rejected_without_touching_foreign_target(tmp_path):
    import _winapi

    values = installation(tmp_path)
    root = values[0]
    foreign = tmp_path / "foreign-junction-target"
    foreign.mkdir()
    (foreign / "sentinel").write_text("KEEP")
    _winapi.CreateJunction(str(foreign), str(root / "junction"))
    with pytest.raises(ReleaseSecurityError):
        run(tmp_path, values)
    assert (foreign / "sentinel").read_text() == "KEEP"
    assert (root / "app.txt").exists()


@windows_only
def test_evidence_preserves_previous_durable_events(tmp_path):
    values = installation(tmp_path)
    run(tmp_path, values)
    records = (tmp_path / "removal.json").read_bytes().splitlines()
    assert len(records) >= 3
    assert json.loads(records[0])["status"] == "refused_or_partial"
    assert json.loads(records[-1])["status"] == "removed"


def test_writer_boundary_policy_requires_unique_service_owner_and_exact_inherited_acl():
    sid = "S-1-5-80-1-2-3-4-5"
    valid = {
        "owner": sid,
        "protected": False,
        "aces": [
            (0, 0x10, 0x1F01FF, "S-1-5-18"),
            (0, 0x10, 0x1F01FF, "S-1-5-32-544"),
            (0, 0x10, 0x1301BF, sid),
        ],
    }
    removal._validate_writer_security(valid, sid)
    for mutation in (
        "foreign_owner",
        "shared_service",
        "foreign_ace",
        "widened_service",
        "explicit_ace",
    ):
        value = {**valid, "aces": list(valid["aces"])}
        if mutation == "foreign_owner":
            value["owner"] = "S-1-5-21-123"
        elif mutation == "shared_service":
            value["owner"] = "S-1-5-19"
        elif mutation == "foreign_ace":
            value["aces"].append((0, 0x10, 0x1F01FF, "S-1-1-0"))
        elif mutation == "widened_service":
            value["aces"][-1] = (0, 0x10, 0x1F01FF, sid)
        else:
            value["aces"][-1] = (0, 0, 0x1301BF, sid)
        with pytest.raises(ReleaseSecurityError):
            removal._validate_writer_security(value, sid)


@windows_only
def test_os_owned_dynamic_file_with_foreign_creator_sid_is_refused(tmp_path):
    values = installation(tmp_path)
    root = values[0]
    (root / "state" / "foreign-created").write_text("KEEP")
    handle = removal._WindowsObject(root / "state" / "foreign-created")
    try:
        record = removal._security_record(handle)
        assert record["owner"] != "S-1-5-80-1-2-3-4-5"
        with pytest.raises(ReleaseSecurityError):
            removal._validate_writer_security(record, "S-1-5-80-1-2-3-4-5")
    finally:
        handle.close()
    assert (root / "state" / "foreign-created").read_text() == "KEEP"


@windows_only
def test_explicit_external_shortcuts_removed_by_identity_without_foreign_desktop_deletion(tmp_path):
    bundle, key_pin = signed_bundle(tmp_path)
    release = verify(bundle, key_pin)
    root = tmp_path / ("fresh-owned-" + uuid.uuid4().hex)
    root.mkdir()
    (root / "app.txt").write_bytes(b"TESTER")
    marker = dict(
        schema="tester-release-install/v1",
        installation_id=str(uuid.uuid4()),
        root=str(root),
        source_sha="a" * 40,
        release_manifest_sha256=release.manifest_sha256,
    )
    (root / ".tester-release.json").write_text(json.dumps(marker))
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    links = [
        desktop / "TESTER \u2014 \u0417\u0430\u043f\u0443\u0441\u043a.lnk",
        desktop / "TESTER \u2014 \u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435.lnk",
    ]
    for link in links:
        link.write_bytes(b"authorized dummy shortcut bytes")
    (desktop / "foreign.lnk").write_text("KEEP")
    ledger = tmp_path / "ownership.json"
    pin = removal.write_ownership(
        root,
        marker,
        release=release,
        ledger_path=ledger,
        owned_paths=("app.txt", ".tester-release.json"),
        external_paths=links,
    )
    values = root, ledger, pin, key_pin
    assert run(tmp_path, values)["status"] == "removed"
    assert all(not link.exists() for link in links)
    assert (desktop / "foreign.lnk").read_text() == "KEEP"
    assert desktop.exists()


@windows_only
def test_powershell_bootstrap_lease_blocks_native_file_and_directory_swaps(tmp_path):
    import os
    import subprocess
    from pathlib import Path

    wrapper = Path(__file__).resolve().parents[2] / "scripts/windows/Uninstall-ReleaseTester.ps1"
    prelude = wrapper.read_text(encoding="utf-8").split("function Assert-PlainPath", 1)[0]
    fixture = tmp_path / "fresh-maintenance"
    fixture.mkdir()
    (fixture / "payload").write_text("authorized dummy signed code")
    script = tmp_path / "lease-proof.ps1"
    script.write_text(
        prelude
        + """
Lock-MaintenancePath $env:TESTER_LEASE_FIXTURE
Lock-MaintenancePath (Join-Path $env:TESTER_LEASE_FIXTURE 'payload')
[Console]::WriteLine('READY')
[Console]::ReadLine() | Out-Null
foreach ($lease in $maintenanceLeases) { $lease.Dispose() }
""",
        encoding="utf-8",
    )
    child = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script)],
        env={**os.environ, "TESTER_LEASE_FIXTURE": str(fixture)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert child.stdout.readline().strip() == "READY"
        assert (fixture / "payload").read_text() == "authorized dummy signed code"
        with pytest.raises(OSError):
            (fixture / "payload").write_text("tamper")
        with pytest.raises(OSError):
            fixture.rename(tmp_path / "swapped")
    finally:
        output, error = child.communicate("\n", timeout=30)
    assert child.returncode == 0, error + output
    fixture.rename(tmp_path / "released")


@windows_only
def test_deferred_completion_does_not_report_full_product_removal(tmp_path):
    values = installation(tmp_path)
    result = run(tmp_path, values, defer_completion=True)
    assert result["status"] == "root_removed_pending_maintenance_cleanup"
    assert not values[0].exists()
    assert (
        removal.read_evidence(tmp_path / "removal.json")["status"]
        == "root_removed_pending_maintenance_cleanup"
    )


@windows_only
def test_native_powershell_transient_cleanup_deletes_only_recorded_identities(tmp_path):
    import subprocess
    from pathlib import Path

    wrapper = Path(__file__).resolve().parents[2] / "scripts/windows/Uninstall-ReleaseTester.ps1"
    prelude = wrapper.read_text(encoding="utf-8").split("if (-not $TrustConfig)", 1)[0]
    fixture = tmp_path / "fresh-maintenance-cleanup"
    fixture.mkdir()
    (fixture / "known").write_text("authorized dummy code")
    script = tmp_path / "cleanup-proof.ps1"
    script.write_text(
        prelude
        + """
$stageRecords = New-Object 'System.Collections.Generic.List[object]'
Add-StageRecord $env:TESTER_LEASE_FIXTURE $true
Add-StageRecord (Join-Path $env:TESTER_LEASE_FIXTURE 'known') $false
Remove-TransientMaintenance
""",
        encoding="utf-8",
    )
    child = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script)],
        env={**__import__("os").environ, "TESTER_LEASE_FIXTURE": str(fixture)},
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert child.returncode == 0, child.stderr
    assert not fixture.exists()


def test_mapped_signed_payload_and_derived_maintenance_inventory_exclude_user_data(tmp_path):
    import hashlib

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from src.api import release_security

    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    bundle = tmp_path / "bundle"
    for name in (
        "payload/src/app.py",
        "runtimes/python/python.exe",
        "runtimes/postgres/bin/pg_ctl.exe",
        "wheelhouse/library.whl",
    ):
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"authorized dummy artifact")
    release_security.sign_release(
        bundle, private_key_path=key_path, source_sha="a" * 40, password=None
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    release = verify(bundle, hashlib.sha256(public).hexdigest())
    root = tmp_path / "fresh-owned-mapped"
    root.mkdir()
    mapping = {
        "payload/src/app.py": "releases/" + "a" * 40 + "/src/app.py",
        "runtimes/python/python.exe": "dependencies/python/python.exe",
        "runtimes/postgres/bin/pg_ctl.exe": "dependencies/postgres/bin/pg_ctl.exe",
    }
    for source, target in mapping.items():
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((bundle / source).read_bytes())
    path = root / "dependencies/venv/Lib/site-packages/derived.py"
    path.parent.mkdir(parents=True)
    path.write_text("authorized derived dependency")
    (root / "USER-DATA").write_text("must never stage this")
    marker = dict(
        schema="tester-release-install/v1",
        installation_id=str(uuid.uuid4()),
        root=str(root),
        source_sha="a" * 40,
        release_manifest_sha256=release.manifest_sha256,
    )
    (root / ".tester-release.json").write_text(json.dumps(marker))
    ledger = tmp_path / "ownership.json"
    created = tuple(p.relative_to(root).as_posix() for p in root.rglob("*"))
    with pytest.raises(ReleaseSecurityError):
        removal.write_ownership(
            root,
            marker,
            release=release,
            ledger_path=ledger,
            owned_paths=created,
            installed_files={"payload/src/app.py": mapping["payload/src/app.py"]},
        )
    removal.write_ownership(
        root,
        marker,
        release=release,
        ledger_path=ledger,
        owned_paths=created,
        installed_files=mapping,
    )
    record = json.loads(ledger.read_bytes())
    assert "USER-DATA" not in record["maintenance_files"]
    assert "dependencies/venv/Lib/site-packages/derived.py" in record["maintenance_files"]
    assert "dependencies/python/python.exe" in record["maintenance_files"]
    assert not any(n.startswith("dependencies/postgres/") for n in record["maintenance_files"])


@pytest.mark.skipif(
    os.name == "nt", reason="Exercises explicit unsupported-platform refusal on Linux"
)
def test_nonwindows_destructive_backend_refuses_without_mutating_owned_data(tmp_path):
    values = installation(tmp_path)
    with pytest.raises(ReleaseSecurityError, match="supports Windows only"):
        run(tmp_path, values)
    assert (values[0] / "app.txt").read_bytes() == b"TESTER"
    assert (values[0] / "state" / "candles").exists()


def test_pinned_boundary_identity_survives_service_account_deregistration(monkeypatch):
    import sys
    from types import SimpleNamespace

    ident = uuid.uuid4()
    service = "TesterReleaseApp_" + ident.hex
    boundary = dict(path="runtime", service_name=service, service_sid="S-1-5-80-1-2-3-4-5")
    ledger = dict(
        writer_boundaries=[boundary],
        entries={"runtime": {"kind": "dir"}},
        marker={
            "installation_id": str(ident),
            "services": {"app": service, "postgres": "TesterReleasePG_" + ident.hex},
        },
    )

    def missing_account(*args):
        raise OSError("SCM service was already deregistered by a partial removal")

    monkeypatch.setitem(
        sys.modules, "win32security", SimpleNamespace(LookupAccountName=missing_account)
    )
    assert removal._boundaries(ledger) == [boundary]


@windows_only
def test_native_metadata_identity_refusal_after_trusted_acl_gate(tmp_path):
    import subprocess
    from pathlib import Path

    wrapper = Path(__file__).resolve().parents[2] / "scripts/windows/Uninstall-ReleaseTester.ps1"
    prelude = wrapper.read_text(encoding="utf-8").split("if (-not $TrustConfig)", 1)[0]
    metadata = tmp_path / "fresh-owned-metadata.json"
    metadata.write_text("authorized dummy metadata")
    script = tmp_path / "metadata-proof.ps1"
    # The host cannot assign the Administrators owner SID. Isolate the native ID
    # gate after a trusted ACL check; the real ACL evaluator is tested separately.
    script.write_text(
        prelude
        + """
function Assert-AdminTrust([string]$Path, [bool]$StrictConfig=$false) { }
try {
    Remove-ProtectedMetadata $env:TESTER_METADATA_PATH 'WRONG-IDENTITY'
    exit 9
} catch {
    if (-not (Test-Path -LiteralPath $env:TESTER_METADATA_PATH)) { exit 8 }
    exit 0
}
""",
        encoding="utf-8",
    )
    child = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script)],
        env={**os.environ, "TESTER_METADATA_PATH": str(metadata)},
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert child.returncode == 0, child.stderr
    assert metadata.read_text() == "authorized dummy metadata"


def provisioning_journal(tmp_path):
    import hashlib

    bundle, pin = signed_bundle(tmp_path)
    release = verify(bundle, pin)
    root = tmp_path / ("fresh-provision-" + uuid.uuid4().hex)
    root.mkdir()
    (root / "app.txt").write_bytes(b"TESTER")
    ident = uuid.uuid4()
    marker = dict(
        schema="tester-release-install/v1",
        root=str(root),
        installation_id=str(ident),
        source_sha="a" * 40,
        release_manifest_sha256=release.manifest_sha256,
        services={
            "app": "TesterReleaseApp_" + ident.hex,
            "postgres": "TesterReleasePG_" + ident.hex,
        },
    )
    header = dict(
        schema="tester-provisioning/v1",
        root=str(root),
        installation_id=str(ident),
        marker=marker,
        actor_sid="S-1-5-80-1-2-3-4-5",
        release_envelope=json.loads(release.manifest),
        release_manifest_sha256=release.manifest_sha256,
        root_parent_identity=removal._identity(root.parent),
        allowed_actor_prefixes=[
            "runtime",
            "state",
            "private",
            "backups",
            "postgres-data",
            "dependencies/venv",
        ],
        allowed_actor_files=["config.json", "config.pending"],
    )
    temporary = ".tester-copy_" + uuid.uuid4().hex + ".pending"
    rows = [
        header,
        dict(event="root_created", identity=removal._identity(root)),
        dict(
            event="planned_file",
            path="app.txt",
            temp=temporary,
            signed_source="app.txt",
            sha256=hashlib.sha256(b"TESTER").hexdigest(),
        ),
        dict(
            event="created",
            path="app.txt",
            identity=removal._identity(root / "app.txt"),
            kind="file",
        ),
        dict(
            event="finalized",
            path="app.txt",
            identity=removal._identity(root / "app.txt"),
            sha256=hashlib.sha256(b"TESTER").hexdigest(),
        ),
    ]
    encoded = b"".join(removal.canonical_json(row) + b"\n" for row in rows)
    return root, pin, header, rows, encoded


def test_partial_journal_authenticates_signed_source_and_exact_created_ids(tmp_path):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    parsed = removal._parse_provisioning_journal(
        encoded, root=root, trusted_public_key_sha256=pin, expected_source_sha="a" * 40
    )
    assert parsed["entries"]["app.txt"]["identity"] == removal._identity(root / "app.txt")
    assert parsed["root_identity"] == removal._identity(root)
    assert parsed["header"] == header


@pytest.mark.parametrize(
    "mutation", ["key", "source", "traversal", "signed_hash", "unknown_event", "committed"]
)
def test_partial_journal_rejects_forged_authority_or_unsafe_events(tmp_path, mutation):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    if mutation == "key":
        pin = "0" * 64
    elif mutation == "source":
        header["release_envelope"]["manifest"]["source_sha"] = "b" * 40
    elif mutation == "traversal":
        rows[2]["path"] = "../FOREIGN"
    elif mutation == "signed_hash":
        rows[2]["sha256"] = "0" * 64
    elif mutation == "unknown_event":
        rows.append({"event": "adopt_foreign_snapshot"})
    else:
        rows.append({"event": "phase", "phase": "ready"})
    encoded = b"".join(removal.canonical_json(row) + b"\n" for row in rows)
    with pytest.raises(ReleaseSecurityError):
        removal._parse_provisioning_journal(
            encoded, root=root, trusted_public_key_sha256=pin, expected_source_sha="a" * 40
        )


def test_provisioning_acl_proof_rejects_foreign_creator_and_foreign_access():
    sid = "S-1-5-80-1-2-3-4-5"
    initial = dict(
        owner=sid,
        protected=True,
        aces=[(0, 0, 0x1F01FF, "S-1-5-18"), (0, 0, 0x1F01FF, "S-1-5-32-544")],
    )
    removal._validate_provisioning_actor_security(initial, sid, initial=True)
    mutable = {**initial, "aces": initial["aces"] + [(0, 0, 0x1F01FF, sid)]}
    removal._validate_provisioning_actor_security(mutable, sid)
    for record in (
        {**initial, "owner": "S-1-5-19"},
        {**mutable, "aces": mutable["aces"] + [(0, 0, 0x120089, "S-1-1-0")]},
    ):
        with pytest.raises(ReleaseSecurityError):
            removal._validate_provisioning_actor_security(record, sid)


@contextmanager
def fixture_protected_provisioning(path, *, write=False):
    # Isolate ACL trust enrollment on this non-elevated host; native IDs, signature,
    # handle locking, hashes and destructive operations below remain real.
    obj = removal._WindowsObject(path, write=write)
    try:
        yield obj
    finally:
        obj.close()


def write_provisioning_fixture(tmp_path, rows):
    path = tmp_path / "protected-provisioning.jsonl"
    path.write_bytes(b"".join(removal.canonical_json(row) + b"\n" for row in rows))
    return path


@windows_only
def test_native_partial_install_rollback_and_repeat_preserve_foreign_neighbor(
    tmp_path, monkeypatch
):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    journal = write_provisioning_fixture(tmp_path, rows)
    foreign = tmp_path / "FOREIGN"
    foreign.write_text("KEEP")
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    resources = Resources()
    result = removal.rollback_provisioning(
        root,
        journal_path=journal,
        trusted_public_key_sha256=pin,
        expected_source_sha="a" * 40,
        resources=resources,
        lifecycle_lock=fixture_lock,
    )
    assert result["status"] == "rolled_back"
    assert not root.exists() and foreign.read_text() == "KEEP"
    assert journal.exists()
    assert (
        removal.rollback_provisioning(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=resources,
            lifecycle_lock=fixture_lock,
        )["status"]
        == "already_rolled_back"
    )


@windows_only
@pytest.mark.parametrize(
    "mutation", ["foreign", "actor_prefix_foreign", "hardlink", "swapped", "locked"]
)
def test_native_partial_rollback_refuses_foreign_or_locked_objects(tmp_path, monkeypatch, mutation):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    journal = write_provisioning_fixture(tmp_path, rows)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    held = None
    if mutation == "foreign":
        (root / "FOREIGN").write_text("KEEP")
    elif mutation == "actor_prefix_foreign":
        (root / "runtime").mkdir()
        (root / "runtime" / "FOREIGN").write_text("KEEP")
    elif mutation == "hardlink":
        (tmp_path / "foreign-hardlink").hardlink_to(root / "app.txt")
    elif mutation == "swapped":
        (root / "app.txt").rename(tmp_path / "old-code")
        (root / "app.txt").write_bytes(b"TESTER")
    else:
        held = (root / "app.txt").open("rb")
    try:
        with pytest.raises((ReleaseSecurityError, OSError)):
            removal.rollback_provisioning(
                root,
                journal_path=journal,
                trusted_public_key_sha256=pin,
                expected_source_sha="a" * 40,
                resources=Resources(),
                lifecycle_lock=fixture_lock,
            )
        assert root.exists() and (root / "app.txt").exists()
    finally:
        if held:
            held.close()


@windows_only
def test_native_provisioning_seal_appends_fsynced_derived_code_identity(tmp_path, monkeypatch):
    import hashlib

    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    for name in ("dependencies", "dependencies/venv"):
        (root / name).mkdir()
        rows += [
            dict(event="planned_dir", path=name),
            dict(event="created", path=name, identity=removal._identity(root / name), kind="dir"),
        ]
    path = root / "dependencies/venv/derived.py"
    path.write_bytes(b"authorized derived dependency")
    name = "dependencies/venv/derived.py"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rows += [
        dict(
            event="planned_file",
            path=name,
            temp="dependencies/venv/.tester-copy_" + uuid.uuid4().hex + ".pending",
            sha256=digest,
            signed_source=None,
        ),
        dict(event="created", path=name, identity=removal._identity(path), kind="file"),
    ]
    journal = write_provisioning_fixture(tmp_path, rows)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    proof = removal.seal_provisioning_outputs(
        root,
        journal_path=journal,
        trusted_public_key_sha256=pin,
        expected_source_sha="a" * 40,
        resources=Resources(),
        lifecycle_lock=fixture_lock,
    )
    assert name in proof
    final = json.loads(journal.read_bytes().splitlines()[-1])
    assert final == dict(
        event="finalized", path=name, identity=removal._identity(path), sha256=digest
    )
    removal.rollback_provisioning(
        root,
        journal_path=journal,
        trusted_public_key_sha256=pin,
        expected_source_sha="a" * 40,
        resources=Resources(),
        lifecycle_lock=fixture_lock,
    )
    assert not root.exists()


@windows_only
def test_partial_rollback_recovers_recorded_temp_rename_before_rename_event(tmp_path, monkeypatch):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    temp = rows[2]["temp"]
    (root / "app.txt").rename(root / temp)
    rows = rows[:3] + [
        dict(event="created", path=temp, identity=removal._identity(root / temp), kind="file")
    ]
    journal = write_provisioning_fixture(tmp_path, rows)
    (root / temp).rename(root / "app.txt")
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    assert (
        removal.rollback_provisioning(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=Resources(),
            lifecycle_lock=fixture_lock,
        )["status"]
        == "rolled_back"
    )
    assert not root.exists()


@windows_only
def test_planned_unrecorded_temp_with_foreign_owner_is_never_adopted(tmp_path, monkeypatch):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    foreign = root / rows[2]["temp"]
    foreign.write_text("KEEP FOREIGN")
    journal = write_provisioning_fixture(tmp_path, rows)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    with pytest.raises(ReleaseSecurityError, match="foreign creator"):
        removal.rollback_provisioning(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=Resources(),
            lifecycle_lock=fixture_lock,
        )
    assert foreign.read_text() == "KEEP FOREIGN"
    assert (root / "app.txt").exists()


@windows_only
def test_partial_rollback_failure_retains_evidence_and_retries(tmp_path, monkeypatch):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    journal = write_provisioning_fixture(tmp_path, rows)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    original = removal._WindowsObject.delete
    count = []

    def interrupted(obj):
        if count:
            raise PermissionError("authorized dummy interruption after real file removal")
        original(obj)
        count.append(True)

    monkeypatch.setattr(removal._WindowsObject, "delete", interrupted)
    with pytest.raises(PermissionError):
        removal.rollback_provisioning(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=Resources(),
            lifecycle_lock=fixture_lock,
        )
    assert root.exists() and not (root / "app.txt").exists()
    receipt = journal.with_suffix(".rollback.jsonl")
    assert removal.read_evidence(receipt)["status"] == "refused_or_partial"
    assert removal.read_evidence(receipt)["deleted_count"] == 1
    monkeypatch.setattr(removal._WindowsObject, "delete", original)
    assert (
        removal.rollback_provisioning(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=Resources(),
            lifecycle_lock=fixture_lock,
        )["status"]
        == "rolled_back"
    )


@windows_only
def test_torn_provisioning_append_can_rollback_but_never_seal_over_corruption(
    tmp_path, monkeypatch
):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    journal = write_provisioning_fixture(tmp_path, rows)
    with journal.open("ab") as stream:
        stream.write(b'{"event":"created"')
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    with pytest.raises(ReleaseSecurityError, match="Incomplete provisioning append"):
        removal.seal_provisioning_outputs(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=Resources(),
            lifecycle_lock=fixture_lock,
        )
    assert (
        removal.rollback_provisioning(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=Resources(),
            lifecycle_lock=fixture_lock,
        )["status"]
        == "rolled_back"
    )


@windows_only
def test_partial_rollback_refuses_marker_from_different_installation_even_with_created_id(
    tmp_path, monkeypatch
):
    import hashlib

    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    wrong = {**header["marker"], "installation_id": str(uuid.uuid4())}
    path = root / removal.MARKER
    path.write_text(json.dumps(wrong))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    rows += [
        dict(
            event="planned_file",
            path=removal.MARKER,
            temp=".tester-copy_" + uuid.uuid4().hex + ".pending",
            signed_source=None,
            sha256=digest,
        ),
        dict(event="created", path=removal.MARKER, identity=removal._identity(path), kind="file"),
    ]
    journal = write_provisioning_fixture(tmp_path, rows)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    with pytest.raises(ReleaseSecurityError, match="marker"):
        removal.rollback_provisioning(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=Resources(),
            lifecycle_lock=fixture_lock,
        )
    assert path.exists()


def test_partial_journal_accepts_final_ledger_proof_but_completed_forbids_rollback(tmp_path):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    event = dict(
        event="final_ledger_sealed",
        ledger_path=str(tmp_path / "ownership.json"),
        ledger_sha256="0" * 64,
        trust_config_path=str(tmp_path / "trust.json"),
    )
    rows.append(event)
    encoded = b"".join(removal.canonical_json(row) + b"\n" for row in rows)
    assert (
        removal._parse_provisioning_journal(
            encoded, root=root, trusted_public_key_sha256=pin, expected_source_sha="a" * 40
        )["final_ledger"]
        == event
    )
    rows.append(dict(event="phase", phase="completed"))
    with pytest.raises(ReleaseSecurityError):
        removal._parse_provisioning_journal(
            b"".join(removal.canonical_json(row) + b"\n" for row in rows),
            root=root,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
        )


@windows_only
def test_partial_rollback_removes_only_created_external_shortcut_ids(tmp_path, monkeypatch):
    import hashlib

    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    desktop = tmp_path / "Desktop_owned"
    desktop.mkdir()
    links = [
        desktop / "TESTER \u2014 \u0417\u0430\u043f\u0443\u0441\u043a.lnk",
        desktop / "TESTER \u2014 \u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435.lnk",
    ]
    header["marker"]["desktop"] = str(desktop)
    header["external_paths"] = [str(path) for path in links]
    for path in links:
        path.write_bytes(b"authorized dummy shortcut")
        rows += [
            dict(event="planned_external", path=str(path), kind="file", purpose="desktop-shortcut"),
            dict(event="external_created", path=str(path), identity=removal._identity(path)),
            dict(
                event="external_finalized",
                path=str(path),
                identity=removal._identity(path),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            ),
        ]
    foreign = desktop / "FOREIGN.lnk"
    foreign.write_text("KEEP")
    journal = write_provisioning_fixture(tmp_path, rows)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    assert (
        removal.rollback_provisioning(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=Resources(),
            lifecycle_lock=fixture_lock,
        )["status"]
        == "rolled_back"
    )
    assert all(not path.exists() for path in links)
    assert foreign.read_text() == "KEEP"


@windows_only
def test_final_ledger_commit_routes_fresh_transaction_to_full_owned_uninstaller(
    tmp_path, monkeypatch
):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    release = verify(tmp_path / "bundle", pin)
    config = tmp_path / "trust.json"
    header["marker"]["trust_config"] = str(config)
    (root / removal.MARKER).write_text(json.dumps(header["marker"]))
    ledger = tmp_path / "final-ownership.json"
    ledger_pin = removal.write_ownership(
        root,
        header["marker"],
        release=release,
        ledger_path=ledger,
        owned_paths=("app.txt", removal.MARKER),
        trust_config_path=config,
    )
    rows.append(
        dict(
            event="final_ledger_sealed",
            ledger_path=str(ledger),
            ledger_sha256=ledger_pin,
            trust_config_path=str(config),
        )
    )
    journal = write_provisioning_fixture(tmp_path, rows)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    calls = []

    def full_remove(actualroot, **kwargs):
        calls.append((actualroot, kwargs))
        return dict(status="removed", deleted_count=0)

    monkeypatch.setattr(removal, "uninstall", full_remove)
    result = removal.rollback_provisioning(
        root,
        journal_path=journal,
        trusted_public_key_sha256=pin,
        expected_source_sha="a" * 40,
        resources=Resources(),
        final_resources=Resources(),
        lifecycle_lock=fixture_lock,
    )
    assert result["status"] == "rolled_back"
    assert calls[0][0] == root
    assert calls[0][1]["trusted_ledger_sha256"] == ledger_pin
    assert calls[0][1]["confirmations"] == ("DELETE TESTER", "YES")


@windows_only
def test_provisioning_seal_cooperates_with_retained_root_rename_guard(tmp_path, monkeypatch):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    journal = write_provisioning_fixture(tmp_path, rows)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    guard = removal._WindowsObject(root)
    try:
        proof = removal.seal_provisioning_outputs(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=Resources(),
            lifecycle_lock=fixture_lock,
        )
        assert proof["app.txt"] == removal._identity(root / "app.txt")
    finally:
        guard.close()


@windows_only
def test_final_enrollment_refuses_replacement_after_sealed_creation_receipt(tmp_path):
    values = installation(tmp_path)
    root, _, _, pin = values
    marker = json.loads((root / removal.MARKER).read_text())
    paths = tuple(p.relative_to(root).as_posix() for p in root.rglob("*"))
    identities = {name: removal._identity(root / name) for name in paths}
    root_identity = removal._identity(root)
    (root / "app.txt").rename(tmp_path / "old-original")
    (root / "app.txt").write_bytes(b"TESTER")
    with pytest.raises(ReleaseSecurityError):
        removal.write_ownership(
            root,
            marker,
            release=verify(tmp_path / "bundle", pin),
            ledger_path=tmp_path / "final-after-transition.json",
            owned_paths=paths,
            expected_identities=identities,
            expected_root_identity=root_identity,
        )
    assert (root / "app.txt").exists()


@windows_only
@pytest.mark.parametrize("backend", ["python", "powershell"])
def test_empty_directory_lease_really_blocks_rename_without_child_file_handles(tmp_path, backend):
    import subprocess
    from pathlib import Path

    fixture = tmp_path / "empty-owned-directory"
    fixture.mkdir()
    if backend == "python":
        guard = removal._WindowsObject(fixture)
        try:
            with pytest.raises(OSError):
                fixture.rename(tmp_path / "swapped")
        finally:
            guard.close()
    else:
        wrapper = (
            Path(__file__).resolve().parents[2] / "scripts/windows/Uninstall-ReleaseTester.ps1"
        )
        prelude = wrapper.read_text(encoding="utf-8").split("function Assert-PlainPath", 1)[0]
        script = tmp_path / "empty-dir-proof.ps1"
        script.write_text(
            prelude
            + """
Lock-MaintenancePath $env:TESTER_EMPTY_DIR
[Console]::WriteLine('READY')
[Console]::ReadLine() | Out-Null
foreach ($lease in $maintenanceLeases) { $lease.Dispose() }
""",
            encoding="utf-8",
        )
        child = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(script)],
            env={**os.environ, "TESTER_EMPTY_DIR": str(fixture)},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            assert child.stdout.readline().strip() == "READY"
            with pytest.raises(OSError):
                fixture.rename(tmp_path / "swapped")
        finally:
            output, error = child.communicate("\n", timeout=30)
        assert child.returncode == 0, error + output
    fixture.rename(tmp_path / "released")


@pytest.mark.parametrize(
    "row", [dict(event="runtime_exposed"), dict(event="phase", phase="runtime_exposed")]
)
def test_runtime_exposure_forbids_automatic_provisioning_rollback(tmp_path, row):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    rows.append(row)
    with pytest.raises(ReleaseSecurityError, match="exposed|Committed"):
        removal._parse_provisioning_journal(
            b"".join(removal.canonical_json(value) + b"\n" for value in rows),
            root=root,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
        )


@windows_only
def test_exposed_failed_readiness_retains_user_data_without_stopping_resources(
    tmp_path, monkeypatch
):
    root, pin, header, rows, encoded = provisioning_journal(tmp_path)
    result = root / "USER_RESULTS"
    result.write_text("KEEP USER DATA")
    rows.append(dict(event="runtime_exposed"))
    journal = write_provisioning_fixture(tmp_path, rows)
    monkeypatch.setattr(removal, "_protected_provisioning_journal", fixture_protected_provisioning)
    resources = Resources()
    with pytest.raises(ReleaseSecurityError, match="exposed"):
        removal.rollback_provisioning(
            root,
            journal_path=journal,
            trusted_public_key_sha256=pin,
            expected_source_sha="a" * 40,
            resources=resources,
            lifecycle_lock=fixture_lock,
        )
    assert result.read_text() == "KEEP USER DATA" and (root / "app.txt").exists()
    assert resources.events == []
