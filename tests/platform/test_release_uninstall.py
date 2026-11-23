"""Destructive tests operate only on newly created pytest temporary installations."""

import json
import uuid
from contextlib import contextmanager

import pytest

from src.api import release_uninstall as removal
from src.api.release_security import ReleaseSecurityError
from tests.platform.test_release_security import signed_bundle, verify


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


def test_evidence_preserves_previous_durable_events(tmp_path):
    values = installation(tmp_path)
    run(tmp_path, values)
    records = (tmp_path / "removal.json").read_bytes().splitlines()
    assert len(records) >= 3
    assert json.loads(records[0])["status"] == "refused_or_partial"
    assert json.loads(records[-1])["status"] == "removed"
