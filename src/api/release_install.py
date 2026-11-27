"""Canonical release installation contracts; importing never mutates the host."""

import contextlib
import json
import os
import re
import socket
import sys
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


def lifecycle_lock_path(root):
    import hashlib

    root = plain(root)
    return plain(
        root.parent
        / (
            ".tester-lifecycle-"
            + hashlib.sha256(str(root).casefold().encode()).hexdigest()
            + ".lock"
        )
    )


def remove_lifecycle_lock(root):
    """Delete this root's empty lock file after removal, only if nobody has it open.

    The native handle refuses reparse points/hardlinks and requests DELETE with
    read-only sharing, so it cannot be acquired while any lifecycle operation
    holds the lock file. POSIX hosts keep the file (unlink would break flock).
    """
    if os.name != "nt":
        return False
    path = lifecycle_lock_path(root)
    if not path.exists():
        return False
    from .release_uninstall import _WindowsObject

    try:
        handle = _WindowsObject(path, delete=True)
    except PermissionError:
        return False  # Held by a concurrent lifecycle operation.
    try:
        if handle.is_dir or handle.size > 1:
            raise LabViolation("Lifecycle lock path is not an empty lock file")
        handle.delete()
    finally:
        handle.close()
    return True


@contextlib.contextmanager
def lifecycle_lock(root):
    """OS lock survives crashes safely; all install/start/remove writers share it."""
    lock = lifecycle_lock_path(root)
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
    # Never the installation root: a process whose current directory is inside
    # the root holds a handle that makes the final root deletion fail.
    working_directory = str(Path(target).parent)
    accepted_directories = {working_directory.casefold(), str(root).casefold()}  # legacy links
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
                or current["working_directory"].casefold() not in accepted_directories
            ):
                raise LabViolation("Foreign shortcut collision")
        else:
            shortcut = _shell_link()
            shortcut.SetPath(target)
            shortcut.SetArguments(arguments)
            shortcut.SetWorkingDirectory(working_directory)
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
            or actual["working_directory"].casefold()
            != (current["working_directory"] if existing else working_directory).casefold()
        ):
            raise LabViolation("COM shortcut readback mismatch")
        if external_identity is not None:
            transaction.finalize_external(link, external_identity)
        made.append(str(link))
    return made


def install(
    bundle, root, *, trusted_public_key_sha256, expected_source_sha, mode="production", desktop=None
):
    """Only a cryptographically verified self-contained release may reach host mutation.

    Recovery, rollback, fresh provisioning and metadata cleanup all run under the
    single per-root lifecycle lock, so no other lifecycle writer interleaves.
    """
    import ctypes
    import platform
    import shutil

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
    try:
        with lifecycle_lock(root):
            return _install_locked(
                bundle,
                root,
                release,
                trusted_public_key_sha256=trusted_public_key_sha256,
                mode=mode,
                desktop=desktop,
            )
    finally:
        if not root.exists():
            with contextlib.suppress(OSError, ValueError):
                remove_lifecycle_lock(root)


def _held_lock(_root):
    return contextlib.nullcontext()


def maintenance_parent(root, mode):
    return Path(os.environ["ProgramData"]) if mode == "production" else plain(root).parent


# Time SCM grants each service to drain on PRESHUTDOWN (agent A: clean stop record).
PRESHUTDOWN_TIMEOUT_MS = {"app": 60000, "postgres": 90000}


def _with_preshutdown(app, pg):
    return (
        dict(app, preshutdown_timeout_ms=PRESHUTDOWN_TIMEOUT_MS["app"]),
        dict(pg, preshutdown_timeout_ms=PRESHUTDOWN_TIMEOUT_MS["postgres"]),
    )


def enable_automatic_start(marker):
    """Idempotent post-commit switch of both exact owned services to AUTO_START."""
    import subprocess

    from .lab_scm import NativeSCM
    from .release_service import plans

    scm = NativeSCM(mutating=True)
    try:
        scm.set_start_type(
            [
                (plan, plan.get("image", subprocess.list2cmdline([plan.get("host", "")])))
                for plan in _with_preshutdown(*plans(marker))
            ],
            automatic=True,
        )
    finally:
        scm.close()


def _services_absent(marker):
    from .lab_scm import NativeSCM

    scm = NativeSCM()
    try:
        return all(scm.inspect(name) is None for name in marker["services"].values())
    finally:
        scm.close()


def _cleanup_metadata(directory, installation_id):
    """Best effort: a refused cleanup is reported, never turned into data loss."""
    from .release_uninstall import remove_maintenance_directory

    try:
        remove_maintenance_directory(directory, installation_id=installation_id)
    except (OSError, ValueError) as exc:
        print(
            "TESTER: retained maintenance metadata "
            + str(directory)
            + " ("
            + type(exc).__name__
            + ")",
            flush=True,
        )


def _finish_owned(root, marker, *, trusted_public_key_sha256, mode):
    """Shortcuts, their pinned identities and stale metadata of earlier attempts."""
    from .release_uninstall import reenroll_external_shortcuts

    create_shortcuts(root, marker)
    reenroll_external_shortcuts(
        root,
        marker,
        trusted_public_key_sha256=trusted_public_key_sha256,
        expected_source_sha=marker["source_sha"],
        protect=_protect_admin_object,
    )
    _sweep_metadata(root, marker, mode)


def _sweep_metadata(root, marker, mode):
    from .release_uninstall import sweep_orphan_maintenance

    try:
        swept = sweep_orphan_maintenance(
            root,
            maintenance_parent(root, mode),
            keep_installation_id=marker["installation_id"],
            services_absent=_services_absent,
        )
    except (OSError, ValueError) as exc:
        print("TESTER: maintenance metadata sweep skipped (" + type(exc).__name__ + ")")
        return
    for directory in swept["kept"]:
        print("TESTER: retained unproven maintenance metadata " + directory, flush=True)


def _install_locked(bundle, root, release, *, trusted_public_key_sha256, mode, desktop):
    replace_empty_root = False
    if root.exists():
        from .release_provision import recover_or_validate_existing

        marker = recover_or_validate_existing(
            root,
            release,
            trusted_public_key_sha256=trusted_public_key_sha256,
            mode=mode,
            lifecycle_lock=_held_lock,
        )
        if marker is not None:
            from .release_service import NativeController

            with NativeController(marker) as controller:
                controller.apply("start")
            # Crash-safe: a commit interrupted before this switch is completed here.
            enable_automatic_start(marker)
            _finish_owned(
                root, marker, trusted_public_key_sha256=trusted_public_key_sha256, mode=mode
            )
            return marker
        # Either an authenticated interrupted attempt was rolled back (root gone)
        # or an empty unclaimed root was proven replaceable: the fresh transaction
        # deletes it by exclusive handle and creates a new object (never adopts it).
        replace_empty_root = root.exists()
    import subprocess

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
    if mode == "test":
        desktop_path = plain(desktop)
    else:
        from win32com.shell import shell, shellcon

        desktop_path = plain(shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, None, 0))
    if not desktop_path.is_dir():
        raise LabViolation("Desktop directory is unavailable")
    maintenance = plain(maintenance_parent(root, mode) / ("TesterReleaseMaintenance_" + ident.hex))
    maintenance.mkdir()
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
    try:
        protect_external_bundle(maintenance)
        (maintenance / "evidence").mkdir()
        protect_external_bundle(maintenance)
        tx = ProvisioningTransaction(root, marker, release, maintenance)
    except BaseException:
        # Nothing but this attempt's own empty directories exists yet.
        if not (maintenance / "provisioning.jsonl").exists():
            with contextlib.suppress(OSError):
                (maintenance / "evidence").rmdir()
            with contextlib.suppress(OSError):
                maintenance.rmdir()
        raise
    exposed = False
    try:
        tx.root_created(replace_empty=replace_empty_root)
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
        app, pg = _with_preshutdown(*plans(marker))
        scm = NativeSCM(mutating=True)
        try:
            # DEMAND_START until the commit point: an interrupted installation
            # must never be started by SCM at boot (it would create service-
            # owned objects inside a root that only rollback may remove).
            with scm.lease([(pg, pg["image"])]):
                if scm.inspect(pg["name"]) is not None:
                    raise LabViolation("Fresh PostgreSQL service collision")
                scm.install(dict(pg, auto_start=False), pg["image"], None)
            scm.promote(
                provisioning_plan(marker),
                dict(app, auto_start=False),
                subprocess.list2cmdline([app["host"]]),
            )
        finally:
            scm.close()
        stage("applying-runtime-acls")
        apply_acls(root, marker)
        links = create_shortcuts(root, marker, transaction=tx)
        tx.write_bytes(root / "state/shortcuts.json", json.dumps({"paths": links}).encode("utf-8"))
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
        enable_automatic_start(marker)
        stage("waiting-for-api-worker-storage")
        with NativeController(marker) as controller:
            controller.apply("start")
        stage("ready")
        tx.append(dict(event="phase", phase="completed"))
        _sweep_metadata(root, marker, mode)  # Never raises: the install is committed.
        return marker
    except BaseException as original:
        # Failure handling is best-effort evidence collection, never a replacement
        # for the original provisioning/SCM exception. An interrupted journal or
        # guard release must not prevent a fail-closed authenticated rollback.
        def record_secondary(stage, exc, primary=original):
            primary.add_note(
                "TESTER: "
                + stage
                + " failed ("
                + type(exc).__name__
                + "); maintenance metadata retained in "
                + str(maintenance)
            )

        journal_valid = True
        try:
            tx.append(dict(event="phase", phase="interrupted"))
        except Exception as journal_error:
            journal_valid = False
            record_secondary("interrupted journal append", journal_error)
        guards_released = True
        try:
            tx.release_guards()
        except Exception as guard_error:
            guards_released = False
            journal_valid = False
            record_secondary("provisioning guard release", guard_error)
        if exposed:
            # Once exposed, user data may exist; never attempt fresh rollback.
            # A failed emergency stop remains an explicit secondary failure.
            try:
                with NativeController(marker) as controller:
                    controller.apply("stop")
            except Exception as stop_error:
                record_secondary("exposed service stop", stop_error)
            raise
        # Never delete while a protection handle may still be live or unsafe
        # to release. Retain authority records for an operator-reviewed retry.
        if not guards_released:
            raise original
        # Missing journal evidence cannot authorize deletion of an owned root.
        if not journal_valid:
            raise original
        # Authenticated provisioning rollback must verify journal and FileIDs.
        # Any failure retains evidence and never permits deleting a foreign root.
        try:
            rollback_provisioning(
                root,
                journal_path=tx.path,
                trusted_public_key_sha256=trusted_public_key_sha256,
                expected_source_sha=release.source_sha,
                lifecycle_lock=_held_lock,
            )
        except Exception as rollback_error:
            record_secondary("authenticated rollback", rollback_error)
            raise original from rollback_error
        # Metadata is deleted only after a verified authenticated rollback.
        _cleanup_metadata(maintenance, marker["installation_id"])
        raise
    finally:
        # A secondary native handle/token close may fail while a provisioning
        # exception is already propagating. Keep the primary error authoritative
        # and attach a bounded diagnostic; a successful install still fails if
        # cleanup cannot complete. Never authorize deletion in this handler.
        active_error = sys.exc_info()[1]
        try:
            tx.close()
        except BaseException as close_error:
            if active_error is None:
                raise
            active_error.add_note(
                "TESTER: transaction close failed (" + type(close_error).__name__
                + "); maintenance metadata retained in " + str(maintenance)
            )


def protect_external_bundle(bundle):
    """Original signed maintenance media: immutable admin-owned code, never user data."""
    for item in (bundle, *bundle.rglob("*")):
        _protect_admin_object(item)


def _protect_admin_object(item):
    """Protected SYSTEM/Administrators-only descriptor on exactly one object."""
    import win32security

    admin = win32security.ConvertStringSidToSid("S-1-5-32-544")
    system = win32security.ConvertStringSidToSid("S-1-5-18")
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
    acl(root / "config.json", ["*S-1-5-18:F", "*S-1-5-32-544:F", sids["app"] + ":R"])
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
