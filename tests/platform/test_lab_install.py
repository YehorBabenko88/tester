import hashlib
import importlib
import json
import os
import uuid

import pytest


def api():
    try:
        return importlib.import_module("src.api.lab_install")
    except ImportError:
        pytest.fail("fresh lab lifecycle module is missing")


def fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "hello.txt").write_text("published")
    inventory = {"hello.txt": hashlib.sha256(b"published").hexdigest()}
    return source, inventory


def test_prepare_exact_release_and_random_ownership(tmp_path):
    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    marker = m.prepare(root, "owner/tester", "a" * 40, source, inventory)
    assert uuid.UUID(marker["installation_id"])
    assert marker["ports"] == {"api": 18766, "postgres": 55470}
    assert marker["services"]["app"].endswith(uuid.UUID(marker["installation_id"]).hex)
    assert (root / "releases" / ("a" * 40) / "hello.txt").read_text() == "published"
    assert m.check(root)["active_sha"] == "a" * 40


def test_unknown_root_and_tampered_release_refused(tmp_path):
    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    root.mkdir()
    (root / "foreign").write_text("keep")
    with pytest.raises(m.LabViolation):
        m.prepare(root, "owner/tester", "a" * 40, source, inventory)
    assert (root / "foreign").read_text() == "keep"
    fresh = tmp_path / "fresh"
    m.prepare(fresh, "owner/tester", "a" * 40, source, inventory)
    (fresh / "releases" / ("a" * 40) / "hello.txt").write_text("tampered")
    with pytest.raises(m.LabViolation):
        m.check(fresh)


def test_inventory_cannot_escape_and_must_be_complete(tmp_path):
    m = api()
    source, inventory = fixture(tmp_path)
    for bad in ({"../escape": "0" * 64}, {}):
        with pytest.raises(m.LabViolation):
            m.prepare(tmp_path / str(uuid.uuid4()), "owner/tester", "a" * 40, source, bad)


def test_remove_rejects_foreign_and_retains_data(tmp_path):
    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    marker = m.prepare(root, "owner/tester", "a" * 40, source, inventory)
    backup = root / "backups" / "precious"
    backup.write_text("keep")
    with pytest.raises(m.LabViolation):
        m.remove(root, lambda _: {"installation_id": "foreign", "state": "stopped"}, lambda _: None)
    removed = []
    import subprocess

    from src.api.lab_scm import ACCOUNT, pg_plan, service_plan

    app, pg = service_plan(root), pg_plan(root)

    def query(name):
        return dict(
            installation_id=marker["installation_id"],
            root=str(root),
            state="stopped",
            source_sha=marker["active_sha"],
            account=ACCOUNT,
            image=pg["image"] if name == pg["name"] else subprocess.list2cmdline([app["host"]]),
        )

    m.remove(root, query, removed.append)
    assert set(removed) == set(marker["services"].values())
    assert backup.read_text() == "keep"
    assert not (root / ".tester-lab.json").exists()
    assert list((root / "state").glob("removed-*.json"))


def test_service_plan_is_real_private_pywin32_host(tmp_path):
    try:
        from src.api.lab_scm import service_plan
    except ImportError:
        pytest.fail("fresh SCM plan module missing")
    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    marker = m.prepare(root, "owner/tester", "a" * 40, source, inventory)
    plan = service_plan(root)
    assert plan["name"] == marker["services"]["app"]
    assert plan["account"] == "NT AUTHORITY\\LocalService"
    assert plan["host"].endswith("pythonservice.exe")
    assert str(root / "dependencies") in plan["host"]
    assert plan["dependency"] == marker["services"]["postgres"]
    assert (
        plan["python_class"]
        == str(root / "dependencies/service/lab_service_bootstrap") + ".LabService"
    )


def test_reparse_root_refused(tmp_path):
    m = api()
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_text("retain target contents", encoding="utf-8")
    link = tmp_path / "junction"
    import subprocess

    windows = os.name == "nt"
    if windows:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        for path in (link, link / sentinel.name):
            with pytest.raises(m.LabViolation, match="Reparse|symlink"):
                m.plain(path)
    finally:
        if windows:
            link.rmdir()  # Remove only the junction, never recurse into its target.
        else:
            link.unlink()  # POSIX directory symlinks must be unlinked, not rmdir'd.
        assert target.is_dir()
        assert sentinel.read_text(encoding="utf-8") == "retain target contents"
        assert set(target.iterdir()) == {sentinel}
    assert not link.exists()


def test_scm_rejects_foreign_image_before_any_mutation(tmp_path):
    try:
        from src.api.lab_scm import apply
    except ImportError:
        pytest.fail("fresh SCM controller missing")
    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    marker = m.prepare(root, "owner/tester", "a" * 40, source, inventory)

    class Foreign:
        def inspect(self, name):
            return {
                "image": "C:\\foreign.exe",
                "installation_id": marker["installation_id"],
                "root": str(root),
                "state": "stopped",
            }

        def stop(self, name):
            pytest.fail("must never mutate foreign service")

    with pytest.raises(m.LabViolation):
        apply("stop", root, Foreign())


def test_service_child_uses_private_python_and_explicit_config(tmp_path):
    try:
        from src.api.lab_service import launch_contract
    except ImportError:
        pytest.fail("fresh ServiceFramework host missing")
    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    m.prepare(root, "owner/tester", "a" * 40, source, inventory)
    contract = launch_contract(root)
    assert contract["argv"] == [
        str(root / "dependencies/venv/Scripts/python.exe"),
        "-m",
        "src.api.supervisor",
    ]
    assert contract["cwd"] == str(root / "releases" / ("a" * 40))
    assert contract["env"]["TESTER_CONFIG_FILE"] == str(root / "config.json")
    assert not any(k.startswith("TESTER_") and k != "TESTER_CONFIG_FILE" for k in contract["env"])


def test_service_runtime_stage_requires_private_python_and_inventory(tmp_path):
    try:
        from src.api.lab_scm import stage_service_runtime
    except ImportError:
        pytest.fail("service runtime staging missing")
    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    m.prepare(root, "owner/tester", "a" * 40, source, inventory)
    base = root / "dependencies/python"
    base.mkdir()
    (base / "python312.dll").write_bytes(b"python")
    (base / "python3.dll").write_bytes(b"abi")
    (base / "Lib").mkdir()
    (base / "Lib/os.py").write_bytes(b"stdlib")
    site = root / "dependencies/venv/Lib/site-packages"
    (site / "win32").mkdir(parents=True)
    (site / "pywin32_system32").mkdir()
    (site / "win32/pythonservice.exe").write_bytes(b"host")
    (site / "win32/servicemanager.pyd").write_bytes(b"manager-fixture")
    (site / "pywin32_system32/pywintypes312.dll").write_bytes(b"types")
    manifest = stage_service_runtime(root)
    m.verify_tree(root / "dependencies/service", manifest["files"])
    assert "python312.dll" in manifest["files"]
    assert "pywintypes312.dll" in manifest["files"]
    assert any(
        name.startswith("Lib/__pycache__/os.") and name.endswith(".pyc")
        for name in manifest["files"]
    )
    with pytest.raises(m.LabViolation):
        stage_service_runtime(root)


def test_add_release_and_rollback_require_deregistered_services(tmp_path):
    m = api()
    assert hasattr(m, "add_release"), "verified retained release staging is missing"
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    m.prepare(root, "owner/tester", "a" * 40, source, inventory)
    m.add_release(root, "b" * 40, source, inventory, lambda _: None)
    assert m.rollback(root, "b" * 40, lambda _: None)["active_sha"] == "b" * 40
    with pytest.raises(m.LabViolation):
        m.rollback(root, "a" * 40, lambda _: {"state": "stopped"})


def test_lifecycle_remove_refuses_copied_uuid_on_foreign_image(tmp_path):
    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    marker = m.prepare(root, "owner/tester", "a" * 40, source, inventory)
    with pytest.raises(m.LabViolation):
        m.remove(
            root,
            lambda _: dict(
                installation_id=marker["installation_id"],
                root=str(root),
                state="stopped",
                account="NT AUTHORITY\\LocalService",
                image="foreign.exe",
            ),
            lambda _: pytest.fail("must not deregister foreign image"),
        )


def test_scm_rejects_stale_registered_release(tmp_path):
    import subprocess

    from src.api.lab_scm import ACCOUNT, owned, service_plan

    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    m.prepare(root, "owner/tester", "a" * 40, source, inventory)
    plan = service_plan(root)
    with pytest.raises(m.LabViolation):
        owned(
            dict(
                image=subprocess.list2cmdline([plan["host"]]),
                installation_id=plan["installation_id"],
                root=str(root),
                account=ACCOUNT,
                source_sha="b" * 40,
            ),
            plan,
            subprocess.list2cmdline([plan["host"]]),
        )


def test_scm_status_includes_runtime_sha_evidence(tmp_path):
    from src.api.lab_scm import apply

    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    marker = m.prepare(root, "owner/tester", "a" * 40, source, inventory)
    evidence = dict(
        source_sha="a" * 40, installation_id=marker["installation_id"], supervisor_pid=123
    )
    (root / "runtime/service.json").write_text(json.dumps(evidence))

    class Absent:
        def inspect(self, name):
            return None

    assert apply("status", root, Absent())["runtime_evidence"] == evidence


def test_service_host_regressions():
    import inspect

    from src.api import lab_service

    host = inspect.getsource(lab_service)
    assert 'CreateJobObject(None, "")' in host
    assert "def SvcRun(self)" in host
    assert "PYTHONDONTWRITEBYTECODE" in host
    assert "Unexpected supervisor exit" in host
    assert "Graceful shutdown timed out" in host


def test_pg_configuration_parser_rejects_overrides_and_includes():
    from src.api import lab_scm

    assert hasattr(lab_scm, "validate_pg_config"), "PG config verifier missing"
    lab_scm.validate_pg_config("listen_addresses='127.0.0.1'\nport=55470\n")
    lab_scm.validate_pg_config("listen_addresses = '127.0.0.1'\nport = 55470\n")
    for bad in (
        "listen_addresses='127.0.0.1'\nport=55470\nport=5432\n",
        "include='foreign'\n",
        "data_directory='C:/foreign'\n",
    ):
        with pytest.raises(api().LabViolation):
            lab_scm.validate_pg_config(bad)


def test_native_python_startup_disables_source_bytecode(tmp_path):
    from pathlib import Path

    # The published startup hook must run before importing a release package.
    hook = Path("src/api/lab_sitecustomize.py")
    assert hook.is_file(), "native CPython sitecustomize hook missing"
    import sys

    namespace = {}
    before = sys.dont_write_bytecode
    try:
        exec(hook.read_text(), namespace)
        assert sys.dont_write_bytecode is True
    finally:
        sys.dont_write_bytecode = before


def test_service_instance_matches_health_contract():
    from src.api import lab_service

    assert hasattr(lab_service, "instance_token"), "service instance generator missing"
    import re

    first, second = lab_service.instance_token(), lab_service.instance_token()
    assert re.fullmatch("[0-9a-f]{32}", first)
    assert first != second


def test_native_bootstrap_disables_bytecode_before_release_package_import(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    bootstrap = Path("src/api/lab_service_bootstrap.py")
    assert bootstrap.is_file(), "pre-package native bootstrap missing"
    (tmp_path / "lab_service_bootstrap.py").write_bytes(bootstrap.read_bytes())
    package = tmp_path / "src/api"
    package.mkdir(parents=True)
    (tmp_path / "src/__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "lab_service.py").write_text("class LabService: pass\n")
    import py_compile

    cache = py_compile.compile(str(tmp_path / "lab_service_bootstrap.py"), doraise=True)
    cache_before = Path(cache).read_bytes()
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.path.insert(0,sys.argv[1]); import lab_service_bootstrap",
            str(tmp_path),
        ],
        check=True,
    )
    assert not list((tmp_path / "src").rglob("*.pyc"))
    assert Path(cache).read_bytes() == cache_before


def test_pg_identity_mismatch_prevents_scm_registration(tmp_path):
    from src.api import lab_scm

    assert hasattr(lab_scm, "validate_pg_identity"), "native PostgreSQL identity verifier missing"
    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    marker = m.prepare(root, "owner/tester", "a" * 40, source, inventory)
    (root / "state/postgres-identity.json").write_text(
        json.dumps(dict(installation_id=marker["installation_id"], system_identifier="123456789"))
    )

    class Absent:
        def inspect(self, name):
            return None

        def install(self, *args):
            pytest.fail("identity mismatch must forbid any registration")

    def ready(path):
        lab_scm.validate_pg_identity(path, lambda _: "Database system identifier: 999999999\n")

    with pytest.raises(m.LabViolation):
        lab_scm.apply("install", root, Absent(), ready=ready)
    lab_scm.validate_pg_identity(root, lambda _: "Database system identifier: 123456789\n")


def test_partial_install_can_remove_exact_owned_stopped_pg_only(tmp_path):
    from src.api.lab_scm import ACCOUNT, apply

    m = api()
    source, inventory = fixture(tmp_path)
    root = tmp_path / "lab"
    m.prepare(root, "owner/tester", "a" * 40, source, inventory)

    class Partial:
        state = {}

        def inspect(self, name):
            return self.state.get(name)

        def install(self, plan, image, dependency):
            if dependency:
                raise RuntimeError("simulated app registration failure")
            self.state[plan["name"]] = dict(
                image=image,
                account=ACCOUNT,
                state="stopped",
                root=str(root),
                installation_id=plan["installation_id"],
            )

        def remove(self, name):
            del self.state[name]

    scm = Partial()
    with pytest.raises(RuntimeError):
        apply("install", root, scm, ready=lambda _: None)
    assert (root / "state/scm-install.json").is_file()
    apply("remove", root, scm)
    assert scm.state == {}


def test_native_remove_uses_standard_delete_access_without_scm_side_effects():
    from src.api.lab_scm import NativeSCM

    calls = []

    class API:
        def OpenService(self, manager, name, access):
            calls.append(access)
            return "handle"

        def DeleteService(self, handle):
            pass

        def CloseServiceHandle(self, handle):
            pass

    scm = object.__new__(NativeSCM)
    scm.ws, scm.manager = API(), "manager"
    scm.remove("owned")
    assert calls == [0x00010000]
