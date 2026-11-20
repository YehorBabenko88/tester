from src.api.guardians.windows_install_plan import (
    DIRECTORIES,
    REMOVE_TITLE,
    ROOT,
    START_TITLE,
    plan_install,
)


def fixture():
    services = {
        "TesterApp": ROOT + "\\app\\api.exe",
        "TesterPG": ROOT + "\\postgres-data\\postgres.exe",
    }
    m = {
        "schema": "tester-install/v1",
        "guardians_enabled": False,
        "root": ROOT,
        "pg_data": ROOT + "\\postgres-data\\data",
        "pg_port": 55432,
        "package_sha256": "a" * 64,
        "source_sha": "b" * 40,
        "services": services,
        "delete_targets": [ROOT],
    }
    a = {
        "root": ROOT,
        "pg_data": m["pg_data"],
        "pg_port": 55432,
        "source_sha": "b" * 40,
        "services": dict(services),
        "reparse_points": [],
        "system_pg_5432_unchanged": True,
    }
    return m, a


def test_plan_exact_two_shortcuts_and_owned_directories():
    m, a = fixture()
    plan = plan_install(m, a, signed_bundle_verified=True)
    assert plan.allowed
    assert len(plan.shortcuts) == 2
    assert {s.title for s in plan.shortcuts} == {START_TITLE, REMOVE_TITLE}
    assert len(plan.directories) == len(DIRECTORIES)
    assert all(path.startswith(ROOT + "\\") for path in plan.directories)
    assert all("AllSigned" in s.arguments for s in plan.shortcuts)


def test_unsigned_and_foreign_root_rejected():
    m, a = fixture()
    assert plan_install(m, a).reason == "bundle_signature_unverified"
    m["root"] = "c:\\foreign"
    a["root"] = "c:\\foreign"
    assert not plan_install(m, a, signed_bundle_verified=True).allowed


def test_system_pg_port_and_guardian_enable_rejected():
    m, a = fixture()
    m["pg_port"] = a["pg_port"] = 5432
    assert not plan_install(m, a, signed_bundle_verified=True).allowed
    m, a = fixture()
    m["guardians_enabled"] = True
    assert not plan_install(m, a, signed_bundle_verified=True).allowed


def test_mismatched_source_commit_fails_closed():
    m, a = fixture()
    a["source_sha"] = "c" * 40
    assert not plan_install(m, a, signed_bundle_verified=True).allowed
