from src.api.guardians.windows_install_gate import (
    Gate,
    canonical,
    manifest_fingerprint,
    uninstall_authorized,
    validate_manifest,
)


def fixture():
    root = "c:\\testerlab"
    services = {"TesterApp": root + "\\bin\\api.exe", "TesterPG": root + "\\pg\\postgres.exe"}
    manifest = {
        "schema": "tester-install/v1",
        "guardians_enabled": False,
        "root": root,
        "pg_data": root + "\\pg\\data",
        "pg_port": 55432,
        "package_sha256": "a" * 64,
        "source_sha": "b" * 40,
        "services": services,
        "delete_targets": [root + "\\history", root + "\\results", root],
    }
    attested = {
        "root": root,
        "pg_data": root + "\\pg\\data",
        "pg_port": 55432,
        "source_sha": "b" * 40,
        "services": dict(services),
        "reparse_points": [],
        "system_pg_5432_unchanged": True,
    }
    return manifest, attested


def test_pure_gate_requires_two_confirmations():
    m, a = fixture()
    gate = validate_manifest(m, attested=a)
    assert gate.allowed
    assert not uninstall_authorized("DELETE TESTER", False, gate)
    assert not uninstall_authorized("delete tester", True, gate)
    assert uninstall_authorized("DELETE TESTER", True, gate)
    assert not uninstall_authorized("DELETE TESTER", True, Gate(False, "denied"))
    assert len(manifest_fingerprint(m)) == 64


def test_foreign_system_paths_and_pg5432_fail_closed():
    m, a = fixture()
    m["pg_port"] = 5432
    a["pg_port"] = 5432
    assert not validate_manifest(m, attested=a).allowed
    m, a = fixture()
    m["delete_targets"].append("c:\\program files\\postgresql\\18\\data")
    assert validate_manifest(m, attested=a).reason == "foreign_delete_target"
    m, a = fixture()
    m["services"]["postgresql-x64-18"] = "c:\\program files\\postgresql\\18\\bin\\postgres.exe"
    assert not validate_manifest(m, attested=a).allowed


def test_reparse_and_forged_service_attestation_rejected():
    m, a = fixture()
    a["reparse_points"] = ["c:\\testerlab\\history"]
    assert validate_manifest(m, attested=a).reason == "reparse_or_unknown"
    m, a = fixture()
    a["services"]["TesterPG"] = "c:\\other\\postgres.exe"
    assert validate_manifest(m, attested=a).reason == "service_identity_mismatch"


def test_missing_manifest_or_identity_fails():
    m, a = fixture()
    assert not validate_manifest(m).allowed
    assert not validate_manifest({}, attested=a).allowed
    m["guardians_enabled"] = True
    assert not validate_manifest(m, attested=a).allowed


def test_commit_sha_pin_and_attestation_required():
    m, a = fixture()
    m.pop("source_sha")
    assert validate_manifest(m, attested=a).reason == "unpinned_commit"
    m, a = fixture()
    m["source_sha"] = "c" * 40
    assert validate_manifest(m, attested=a).reason == "commit_attestation_mismatch"


def test_windows_ambiguous_names_ads_and_device_paths_fail_closed():
    bad = (
        "c:\\testerlab\\history:alternate",
        "c:\\testerlab\\AUX.txt",
        "c:\\testerlab\\COM1",
        "c:\\testerlab\\history.",
        "c:\\testerlab\\history ",
        "c:\\testerlab\\\\history",
        "c:\\testerlab\\.\\history",
        "c:\\testerlab\\..\\windows",
        "c:\\testerlab\\history?",
        "c:\\testerlab\\history\\NUL.txt",
        "c:\\testerlab\\history" + chr(0),
    )
    for path in bad:
        assert canonical(path) is None, path
    assert canonical("C:\\TesterLab\\history") == "c:\\testerlab\\history"
