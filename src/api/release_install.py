"""Canonical release installation contracts; importing never mutates the host."""

import contextlib
import json
import os
import re
import socket
import uuid
from pathlib import Path

from .lab_install import LabViolation, plain

MARKER = ".tester-release.json"
SCHEMA = "tester-release-install/v1"
CANONICAL_ROOT = Path(r"C:\TesterLab")


def system_binary(relative):
    """Resolve OS tools through kernel32, never PATH/SystemRoot environment text."""
    import ctypes
    from ctypes import wintypes
    from pathlib import PureWindowsPath

    name = PureWindowsPath(relative)
    if name.is_absolute() or ".." in name.parts or not name.parts:
        raise LabViolation("A relative system tool name is required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel.GetSystemDirectoryW.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value).joinpath(*name.parts)


def validate_destination(root, *, mode, desktop):
    root = plain(root)
    if mode == "production":
        if root != CANONICAL_ROOT or desktop is not None:
            raise LabViolation("Production requires canonical C:\\TesterLab and real Desktop")
    elif mode == "test":
        if root == CANONICAL_ROOT or not re.fullmatch(r"TesterReleaseTest_[0-9a-f]{32}", root.name):
            raise LabViolation("Test root must be a unique TesterReleaseTest_UUID directory")
        if (
            desktop is None
            or plain(desktop).parent != root.parent
            or not Path(desktop).name.startswith("Desktop_")
        ):
            raise LabViolation("Explicit sibling fake Desktop required in test mode")
    else:
        raise LabViolation("Explicit production or test mode required")
    return root


@contextlib.contextmanager
def lifecycle_lock(root):
    """OS lock survives crashes safely; all install/start/remove writers share it."""
    root = plain(root)
    import hashlib

    lock = plain(
        root.parent
        / (
            ".tester-lifecycle-"
            + hashlib.sha256(str(root).casefold().encode()).hexdigest()
            + ".lock"
        )
    )
    stream = lock.open("a+b")
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise LabViolation("Another lifecycle operation owns the installation") from exc
        else:
            import fcntl

            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise LabViolation("Another lifecycle operation owns the installation") from exc
        yield
    finally:
        stream.close()


def load_marker(root):
    root = plain(root)
    try:
        marker = json.loads(plain(root / MARKER).read_text(encoding="utf-8"))
        ident = uuid.UUID(marker["installation_id"])
        valid = (
            marker["schema"] == SCHEMA
            and marker["root"] == str(root)
            and re.fullmatch("[0-9a-f]{40}", marker["source_sha"])
            and re.fullmatch("[0-9a-f]{64}", marker["release_manifest_sha256"])
            and marker["services"]
            == {"app": "TesterReleaseApp_" + ident.hex, "postgres": "TesterReleasePG_" + ident.hex}
            and set(marker["ports"]) == {"api", "postgres"}
            and marker["ports"]["postgres"] != 5432
            and len(set(marker["ports"].values())) == 2
            and all(
                type(port) is int and 1024 <= port <= 65535 for port in marker["ports"].values()
            )
        )
        if not valid:
            raise LabViolation("Invalid release marker authority")
        validate_destination(root, mode=marker["mode"], desktop=marker.get("test_desktop"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LabViolation("Missing or invalid release ownership marker") from exc
    return marker


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_json(path, value):
    temporary = path.with_suffix(".pending")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
    os.replace(temporary, path)


def inventory(path):
    import hashlib

    return {
        p.relative_to(path).as_posix(): hashlib.sha256(plain(p).read_bytes()).hexdigest()
        for p in path.rglob("*")
        if p.is_file()
    }


def _shell_link():
    import pythoncom
    from win32com.shell import shell

    return pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
    )


def read_shortcut(path):
    """IShellLinkW/IPersistFile keep Cyrillic filenames on every Windows ACP."""
    import pythoncom
    from win32com.shell import shell

    link = _shell_link()
    link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(plain(path)))
    return dict(
        target=link.GetPath(shell.SLGP_RAWPATH)[0],
        arguments=link.GetArguments(),
        working_directory=link.GetWorkingDirectory(),
    )


def create_shortcuts(root, marker, *, transaction=None):
    """Real COM links, including exact Unicode names; fake Desktop only in test mode."""
    import pythoncom

    desktop = (
        Path(marker["desktop"])
        if marker.get("desktop")
        else (
            Path(marker["test_desktop"])
            if marker["mode"] == "test"
            else Path(os.environ["USERPROFILE"]) / "Desktop"
        )
    )
    desktop = plain(desktop)
    desktop.mkdir(exist_ok=True)
    target = str(system_binary("WindowsPowerShell/v1.0/powershell.exe"))
    made = []
    for title, script in (
        ("TESTER — Запуск", "Start-ReleaseTester.ps1"),
        ("TESTER — Удаление", "Uninstall-ReleaseTester.ps1"),
    ):
        link = plain(desktop / (title + ".lnk"))
        arguments = (
            '-NoProfile -File "'
            + str(root / "releases" / marker["source_sha"] / "scripts/windows" / script)
            + '" -Root "'
            + str(root)
            + '"'
        )
        if script == "Uninstall-ReleaseTester.ps1" and marker.get("trust_config"):
            arguments = (
                '-NoProfile -File "'
                + str(root / "releases" / marker["source_sha"] / "scripts/windows" / script)
                + '" -TrustConfig "'
                + marker["trust_config"]
                + '"'
            )
        if marker["mode"] == "production":
            arguments = arguments.replace(
                "-NoProfile -File ", "-NoProfile -ExecutionPolicy AllSigned -File ", 1
            )
        script_path = root / "releases" / marker["source_sha"] / "scripts/windows" / script
        if not plain(script_path).is_file():
            raise LabViolation("Shortcut target script is missing")
        existing = link.exists()
        external_identity = None
        if not existing and transaction is not None:
            external_identity = transaction.prepare_external(link)
        if existing:
            current = read_shortcut(link)
            if (
                current["target"].casefold() != target.casefold()
                or current["arguments"] != arguments
                or current["working_directory"] != str(root)
            ):
                raise LabViolation("Foreign shortcut collision")
        else:
            shortcut = _shell_link()
            shortcut.SetPath(target)
            shortcut.SetArguments(arguments)
            shortcut.SetWorkingDirectory(str(root))
            shortcut.QueryInterface(pythoncom.IID_IPersistFile).Save(str(link), 0)
            # MS-SHLLINK LinkFlags at 0x14; explicit user activation prompts UAC.
            data = bytearray(link.read_bytes())
            if data[:4] != b"\x4c\x00\x00\x00":
                raise LabViolation("Unexpected ShellLink header")
            flags = int.from_bytes(data[20:24], "little") | 0x2000
            data[20:24] = flags.to_bytes(4, "little")
            link.write_bytes(data)
        if not int.from_bytes(link.read_bytes()[20:24], "little") & 0x2000:
            raise LabViolation("Shortcut must request explicit elevated activation")
        actual = read_shortcut(link)
        if (
            actual["target"].casefold() != target.casefold()
            or actual["arguments"] != arguments
            or actual["working_directory"] != str(root)
        ):
            raise LabViolation("COM shortcut readback mismatch")
        if external_identity is not None:
            transaction.finalize_external(link, external_identity)
        made.append(str(link))
    return made


def install(
    bundle, root, *, trusted_public_key_sha256, expected_source_sha, mode="production", desktop=None
):
    """Only a cryptographically verified self-contained release may reach host mutation."""
    import ctypes
    import platform
    import shutil
    import subprocess

    from .release_security import verify_release

    release = verify_release(
        Path(bundle),
        trusted_public_key_sha256=trusted_public_key_sha256,
        expected_source_sha=expected_source_sha,
    )
    root = validate_destination(root, mode=mode, desktop=desktop)
    if os.name != "nt" or int(platform.version().split(".")[2]) < 17763:
        raise LabViolation("Windows 10 1809 / Server 2019 or newer required")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise LabViolation("Open setup as Administrator")
    if shutil.disk_usage(root.parent).free < 5 * 1024**3:
        raise LabViolation("At least 5 GiB free space required")
    if root.exists() and any(root.iterdir()):
        from .release_provision import recover_or_validate_existing

        marker = recover_or_validate_existing(
            root, release, trusted_public_key_sha256=trusted_public_key_sha256, mode=mode
        )
        if marker is None:
            return install(
                bundle,
                root,
                trusted_public_key_sha256=trusted_public_key_sha256,
                expected_source_sha=expected_source_sha,
                mode=mode,
                desktop=desktop,
            )
        with lifecycle_lock(root):
            from .release_service import NativeController

            with NativeController(marker) as controller:
                controller.apply("start")
            create_shortcuts(root, marker)
        return marker
    # A bundle must contain all pinned runtimes, wheelhouse, built frontend and source.
    required = (
        "payload/src/api/supervisor.py",
        "payload/frontend/index.html",
        "payload/requirements-release-windows314.lock",
        "runtimes/python/python.exe",
        "runtimes/postgres/bin/initdb.exe",
        "runtimes/postgres/bin/pg_ctl.exe",
    )
    for relative in required:
        if not plain(Path(bundle) / relative).is_file():
            raise LabViolation("Incomplete offline release: " + relative)
    from contextlib import nullcontext

    from .lab_scm import NativeSCM, stage_service_runtime
    from .release_provision import ProvisioningTransaction, provisioning_plan, run_actor
    from .release_service import NativeController, plans
    from .release_uninstall import rollback_provisioning, seal_provisioning_outputs, write_ownership

    ident = uuid.uuid4()
    maintenance_parent = Path(os.environ["ProgramData"]) if mode == "production" else root.parent
    maintenance = plain(maintenance_parent / ("TesterReleaseMaintenance_" + ident.hex))
    maintenance.mkdir()
    protect_external_bundle(maintenance)
    (maintenance / "evidence").mkdir()
    protect_external_bundle(maintenance)
    if mode == "test":
        desktop_path = plain(desktop)
    else:
        from win32com.shell import shell, shellcon

        desktop_path = plain(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0))
    if not desktop_path.is_dir():
        raise LabViolation("Desktop directory is unavailable")
    api_port, pg_port = free_port(), free_port()
    while pg_port == api_port or pg_port == 5432:
        pg_port = free_port()
    marker = dict(
        schema=SCHEMA,
        root=str(root),
        installation_id=str(ident),
        source_sha=release.source_sha,
        release_manifest_sha256=release.manifest_sha256,
        mode=mode,
        test_desktop=str(desktop_path) if mode == "test" else None,
        desktop=str(desktop_path),
        trust_config=str(maintenance / "trust.json"),
        services={
            "app": "TesterReleaseApp_" + ident.hex,
            "postgres": "TesterReleasePG_" + ident.hex,
        },
        ports={"api": api_port, "postgres": pg_port},
    )
    tx = ProvisioningTransaction(root, marker, release, maintenance)
    exposed = False
    try:
        tx.root_created()
        with lifecycle_lock(root):
            for name in (
                "state",
                "runtime",
                "dependencies",
                "releases",
                "private",
                "backups",
                "postgres-data",
            ):
                tx.mkdir(root / name)
            tx.write_bytes(root / MARKER, json.dumps(marker, indent=2).encode("utf-8"))
            journal = root / "state/install.jsonl"

            def stage(name):
                print("TESTER: " + name.replace("-", " "), flush=True)
                tx.append(dict(event="phase", phase=name))
                if journal.exists():
                    with journal.open("a", encoding="utf-8") as stream:
                        stream.write(
                            json.dumps(
                                dict(
                                    installation_id=str(ident),
                                    source_sha=release.source_sha,
                                    phase=name,
                                )
                            )
                            + "\n"
                        )
                        stream.flush()
                        os.fsync(stream.fileno())
                if (
                    mode == "test"
                    and os.environ.get("TESTER_REQUIRE_FULL_INSTALLER") == "1"
                    and os.environ.get("TESTER_INSTALL_TEST_PAUSE_PHASE") == name
                ):
                    import time

                    write_json(
                        maintenance / "test-pause.json",
                        dict(
                            root=str(root),
                            installation_id=marker["installation_id"],
                            source_sha=release.source_sha,
                            phase=name,
                            pid=os.getpid(),
                        ),
                    )
                    while True:
                        time.sleep(0.2)

            stage("copying-verified-payload")
            release_root = root / "releases" / release.source_sha
            tx.copytree(Path(bundle) / "payload", release_root)
            tx.copytree(Path(bundle) / "runtimes/python", root / "dependencies/python")
            tx.copytree(Path(bundle) / "runtimes/postgres", root / "dependencies/postgres")
            tx.copytree(Path(bundle) / "wheelhouse", root / "dependencies/wheelhouse")
            tx.write_bytes(
                root / "state/source-runtime.json",
                json.dumps({"files": inventory(release_root)}).encode("utf-8"),
            )
            tx.write_bytes(
                root / "state/postgres-runtime.json",
                json.dumps({"files": inventory(root / "dependencies/postgres")}).encode("utf-8"),
            )
            stage("staging-private-service-host")
            stage_service_runtime(
                root,
                marker_reader=load_marker,
                bootstrap="release_service_bootstrap.py",
                site_override=root / "dependencies/python/Lib/site-packages",
                transaction=tx,
                precompile=False,
            )
            immutable = {
                relative: inventory(root / relative)
                for relative in (
                    "releases/" + release.source_sha,
                    "dependencies/python",
                    "dependencies/postgres",
                    "dependencies/service",
                    "dependencies/wheelhouse",
                )
            }
            tx.write_bytes(
                root / "state/provisioning-authority.json",
                json.dumps(dict(marker=marker, immutable=immutable)).encode("utf-8"),
            )
            stage("starting-authenticated-provisioning-actor")
            tx.release_journal()
            run_actor(root, marker, started=lambda: stage("provisioning-actor-running"))
            # The helper proves unique creator SIDs and exact objects before their
            # owners/ACLs change. This never enrolls an arbitrary Admin snapshot.
            created = seal_provisioning_outputs(
                root,
                journal_path=tx.path,
                trusted_public_key_sha256=trusted_public_key_sha256,
                expected_source_sha=release.source_sha,
                lifecycle_lock=lambda _: nullcontext(),
            )
            stage("registering-runtime-services")
            app, pg = plans(marker)
            scm = NativeSCM(mutating=True)
            try:
                with scm.lease([(pg, pg["image"])]):
                    if scm.inspect(pg["name"]) is not None:
                        raise LabViolation("Fresh PostgreSQL service collision")
                    scm.install(pg, pg["image"], None)
                scm.promote(provisioning_plan(marker), app, subprocess.list2cmdline([app["host"]]))
            finally:
                scm.close()
            stage("applying-runtime-acls")
            apply_acls(root, marker)
            links = create_shortcuts(root, marker, transaction=tx)
            tx.write_bytes(
                root / "state/shortcuts.json", json.dumps({"paths": links}).encode("utf-8")
            )
            mapping = {}
            for relative, _ in release.files:
                for prefix, installed in (
                    ("payload/", "releases/" + release.source_sha + "/"),
                    ("runtimes/python/", "dependencies/python/"),
                    ("runtimes/postgres/", "dependencies/postgres/"),
                    ("wheelhouse/", "dependencies/wheelhouse/"),
                ):
                    if relative.startswith(prefix):
                        mapping[relative] = installed + relative.removeprefix(prefix)
            from .release_service import service_sid

            boundaries = [
                dict(
                    path=relative,
                    service_name=marker["services"][role],
                    service_sid=service_sid(marker["services"][role]),
                )
                for role, relative in (("app", "runtime"), ("postgres", "postgres-data"))
            ]
            # All post-actor Admin files are explicit transaction creations.
            owned_paths = set(created) | set(tx.identities)
            expected_identities = {**tx.identities, **created}
            ledger = maintenance / "ownership.json"
            stage("sealing-ownership")
            ledger_digest = write_ownership(
                root,
                marker,
                release=release,
                ledger_path=ledger,
                owned_paths=tuple(owned_paths),
                expected_identities=expected_identities,
                expected_root_identity=tx.root_identity,
                installed_files=mapping,
                writer_boundaries=tuple(boundaries),
                external_paths=tuple(links),
                trust_config_path=maintenance / "trust.json",
            )
            trust = dict(
                schema="tester-maintenance-trust/v1",
                installation_id=str(ident),
                root=str(root),
                ledger=str(ledger),
                trusted_ledger_sha256=ledger_digest,
                trusted_public_key_sha256=trusted_public_key_sha256,
                source_sha=release.source_sha,
                manifest_sha256=release.manifest_sha256,
                bundle=str(plain(bundle)),
                module_source=str(plain(Path(bundle) / "payload")),
                python=str(plain(Path(bundle) / "runtimes/python/python.exe")),
                evidence_directory=str(maintenance / "evidence"),
            )
            write_json(maintenance / "trust.json", trust)
            protect_external_bundle(maintenance)
            tx.append(
                dict(
                    event="final_ledger_sealed",
                    ledger_path=str(ledger),
                    ledger_sha256=ledger_digest,
                    trust_config_path=marker["trust_config"],
                )
            )
            tx.append(dict(event="runtime_exposed"))
            exposed = True
            stage("waiting-for-api-worker-storage")
            with NativeController(marker) as controller:
                controller.apply("start")
            stage("ready")
            tx.append(dict(event="phase", phase="completed"))
        return marker
    except BaseException:
        tx.append(dict(event="phase", phase="interrupted"))
        tx.release_guards()
        if exposed:
            # A user may have submitted data as soon as the API became reachable.
            # Preserve this installed root; reruns authenticate and resume it.
            with NativeController(marker) as controller:
                controller.apply("stop")
            raise
        # Fresh-install transaction authority permits only proven construction
        # resources. Unknown files make rollback refuse before deleting anything.
        rollback_provisioning(
            root,
            journal_path=tx.path,
            trusted_public_key_sha256=trusted_public_key_sha256,
            expected_source_sha=release.source_sha,
        )
        raise
    finally:
        tx.close()


def protect_external_bundle(bundle):
    """Original signed maintenance media: immutable admin-owned code, never user data."""
    import win32security

    admin = win32security.ConvertStringSidToSid("S-1-5-32-544")
    system = win32security.ConvertStringSidToSid("S-1-5-18")
    for item in (bundle, *bundle.rglob("*")):
        item = plain(item)
        acl = win32security.ACL()
        flags = 3 if item.is_dir() else 0
        for sid in (system, admin):
            acl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS, flags, 0x1F01FF, sid)
        win32security.SetNamedSecurityInfo(
            str(item),
            win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION
            | win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            admin,
            None,
            acl,
            None,
        )


def apply_acls(root, marker):
    """Only the unique service SIDs may write their respective mutable trees."""
    import subprocess

    import win32security

    sids = {}
    for role, name in marker["services"].items():
        sid, _, _ = win32security.LookupAccountName(None, "NT SERVICE\\" + name)
        sids[role] = "*" + win32security.ConvertSidToStringSid(sid)
    administrators = ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"]

    def acl(path, grants):
        subprocess.run(
            [str(system_binary("icacls.exe")), str(path), "/inheritance:r", "/grant:r", *grants],
            check=True,
            capture_output=True,
        )

    # Normalize only this newly created, exclusively builder-owned tree before
    # exposing it to service writers; PostgreSQL initdb may create explicit ACLs.
    subprocess.run(
        [str(system_binary("icacls.exe")), str(root / "*"), "/reset", "/T", "/Q"],
        check=True,
        capture_output=True,
    )
    read = administrators + [sid + ":(OI)(CI)RX" for sid in sids.values()]
    acl(root, read)
    for name in ("dependencies", "releases", "state"):
        acl(root / name, read)
    acl(root / "runtime", administrators + [sids["app"] + ":(OI)(CI)M"])
    acl(root / "postgres-data", administrators + [sids["postgres"] + ":(OI)(CI)M"])
    acl(root / "private", administrators)
    acl(root / "backups", administrators)
    subprocess.run(
        [str(system_binary("icacls.exe")), str(root), "/setowner", "*S-1-5-32-544", "/Q"],
        check=True,
        capture_output=True,
    )
    for name in ("releases", "dependencies", "state", "private", "backups"):
        subprocess.run(
            [
                str(system_binary("icacls.exe")),
                str(root / name),
                "/setowner",
                "*S-1-5-32-544",
                "/T",
                "/Q",
            ],
            check=True,
            capture_output=True,
        )
    for path in (root / "config.json", root / MARKER):
        subprocess.run(
            [str(system_binary("icacls.exe")), str(path), "/setowner", "*S-1-5-32-544", "/Q"],
            check=True,
            capture_output=True,
        )
    # The provisioning App account must lose implicit owner WRITE_DAC on every
    # PostgreSQL object before the protected SQL authority starts accepting work.
    subprocess.run(
        [
            str(system_binary("icacls.exe")),
            str(root / "postgres-data"),
            "/setowner",
            sids["postgres"],
            "/T",
            "/Q",
        ],
        check=True,
        capture_output=True,
    )
    acl(root / "config.json", ["*S-1-5-18:F", "*S-1-5-32-544:F", sids["app"] + ":R"])


def provision_database(root, marker, password):
    """Compose existing canonical migrations under the verified private venv."""
    from .release_database import provision_release_databases, runtime_configuration

    runtime, owners = provision_release_databases(marker, password)
    write_json(
        root / "config.json",
        runtime_configuration(
            root,
            marker,
            runtime["tester"],
            runtime["tester_catalog"],
            research_dsn=runtime["tester_research_release"],
        ),
    )
    # Administrative owner credentials never enter the API/worker config.
    write_json(
        root / "private/database-owner.json", dict(database_url=owners["tester"], databases=owners)
    )


def main():
    import argparse
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "--provision-database":
        root = plain(sys.argv[2])
        provision_database(root, load_marker(root), sys.stdin.read())
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--root", default=str(CANONICAL_ROOT))
    parser.add_argument("--trusted-public-key-sha256", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--mode", choices=("production", "test"), default="production")
    parser.add_argument("--desktop")
    args = vars(parser.parse_args())
    print(json.dumps(install(**args)))


if __name__ == "__main__":
    main()
