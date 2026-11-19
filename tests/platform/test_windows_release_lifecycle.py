import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.api.guardians.windows_release_lifecycle import (
    lifecycle_plan,
    verified_lifecycle_plan,
)

ROOT = "c:\\testerlab"


def fixture():
    services = {
        "TesterPG": ROOT + "\\postgres-data\\postgres.exe",
        "TesterAPI": ROOT + "\\app\\api.exe",
        "TesterWorker": ROOT + "\\app\\worker.exe",
    }
    manifest = {
        "schema": "tester-install/v1",
        "root": ROOT,
        "guardians_enabled": False,
        "source_sha": "a" * 40,
        "package_sha256": "b" * 64,
        "pg_data": ROOT + "\\postgres-data\\data",
        "pg_port": 55432,
        "services": services,
        "service_roles": {
            "postgres": "TesterPG",
            "api": "TesterAPI",
            "worker": "TesterWorker",
        },
        "delete_targets": [ROOT],
    }
    attested = {
        "root": ROOT,
        "source_sha": "a" * 40,
        "pg_data": ROOT + "\\postgres-data\\data",
        "pg_port": 55432,
        "services": services,
        "system_pg_5432_unchanged": True,
        "reparse_points": [],
        "runtime_identity_verified": True,
        "lock_exclusive": True,
        "existing_install": False,
        "owned_services_stopped_or_running": True,
        "all_owned_processes_exited": True,
        "all_owned_services_stopped": True,
        "owner_paths_rechecked": True,
    }
    return manifest, attested


def test_install_plan_is_bounded_and_has_two_shortcuts():
    m, a = fixture()
    result = lifecycle_plan("install", m, a, signed_bundle_verified=True)
    assert result.allowed
    assert "create_exact_two_shortcuts" in result.steps
    assert "install_owned_postgresql_non5432" in result.steps
    assert not any("trade" in step for step in result.steps)


def test_reinstall_never_adopts_unknown_existing_install():
    m, a = fixture()
    a["existing_install"] = True
    assert lifecycle_plan("install", m, a, signed_bundle_verified=True).reason == (
        "existing_install_requires_separate_migration"
    )


def test_start_and_stop_order_preserves_dedicated_postgres():
    m, a = fixture()
    start = lifecycle_plan("start", m, a, signed_bundle_verified=True)
    stop = lifecycle_plan("stop", m, a, signed_bundle_verified=True)
    assert start.allowed and stop.allowed
    assert start.steps.index("start_owned_postgresql") < start.steps.index("start_owned_api")
    assert stop.steps.index("stop_owned_worker") < stop.steps.index("stop_owned_postgresql")


def test_uninstall_requires_both_confirmation_and_proven_stopped_services():
    m, a = fixture()
    assert not lifecycle_plan("uninstall", m, a, signed_bundle_verified=True).allowed
    a["all_owned_processes_exited"] = False
    assert lifecycle_plan(
        "uninstall",
        m,
        a,
        signed_bundle_verified=True,
        typed_confirmation="DELETE TESTER",
        second_confirmation=True,
    ).reason == "live_processes_block_deletion"
    a["all_owned_processes_exited"] = True
    result = lifecycle_plan(
        "uninstall",
        m,
        a,
        signed_bundle_verified=True,
        typed_confirmation="DELETE TESTER",
        second_confirmation=True,
    )
    assert result.allowed
    assert "delete_all_attested_tester_owned_data_and_history" in result.steps


def test_system_postgres_and_foreign_scopes_block_all_actions():
    m, a = fixture()
    m["pg_port"] = a["pg_port"] = 5432
    assert not lifecycle_plan("install", m, a, signed_bundle_verified=True).allowed
    m, a = fixture()
    m["services"]["postgresql-x64-18"] = "c:\\program files\\postgresql\\18\\postgres.exe"
    assert not lifecycle_plan("stop", m, a, signed_bundle_verified=True).allowed


def test_lock_signature_identity_and_roles_are_mandatory():
    m, a = fixture()
    assert lifecycle_plan("install", m, a).reason == "unsigned_release"
    a["lock_exclusive"] = False
    assert lifecycle_plan("install", m, a, signed_bundle_verified=True).reason == (
        "exclusive_lock_missing"
    )
    m, a = fixture()
    m["service_roles"].pop("worker")
    assert lifecycle_plan("install", m, a, signed_bundle_verified=True).reason == (
        "missing_service_roles"
    )


def test_canonical_windows_case_and_separators_are_consistent():
    m, a = fixture()
    m["root"] = "C:\\TesterLab"
    m["delete_targets"] = ["C:\\TesterLab"]
    assert lifecycle_plan("install", m, a, signed_bundle_verified=True).allowed
    assert lifecycle_plan(
        "uninstall",
        m,
        a,
        signed_bundle_verified=True,
        typed_confirmation="DELETE TESTER",
        second_confirmation=True,
    ).allowed


def test_signed_lifecycle_rejects_tampering_and_untrusted_signer():
    m, a = fixture()
    private = Ed25519PrivateKey.generate()
    key = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = json.dumps(
        m, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    signature = private.sign(b"TESTER-INSTALL-MANIFEST-v1\x00" + payload).hex()
    proof = {
        "signature_hex": signature,
        "public_key_hex": key.hex(),
        "trusted_public_key_sha256": hashlib.sha256(key).hexdigest(),
    }
    assert verified_lifecycle_plan("install", m, a, **proof).allowed
    assert verified_lifecycle_plan(
        "install", m, a, **{**proof, "trusted_public_key_sha256": "0" * 64}
    ).reason == "invalid_signed_release"
    m["pg_port"] = 5432
    assert verified_lifecycle_plan("install", m, a, **proof).reason == "invalid_signed_release"
