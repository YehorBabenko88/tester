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


def create_shortcuts(root, marker):
    """Real COM links, including exact Unicode names; fake Desktop only in test mode."""
    import win32com.client

    desktop = (
        Path(marker["test_desktop"])
        if marker["mode"] == "test"
        else Path(os.environ["USERPROFILE"]) / "Desktop"
    )
    desktop = plain(desktop)
    desktop.mkdir(exist_ok=True)
    shell = win32com.client.Dispatch("WScript.Shell")
    target = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
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
        script_path = root / "releases" / marker["source_sha"] / "scripts/windows" / script
        if not plain(script_path).is_file():
            raise LabViolation("Shortcut target script is missing")
        if link.exists():
            current = shell.CreateShortcut(str(link))
            if (
                current.TargetPath.casefold() != target.casefold()
                or current.Arguments != arguments
                or current.WorkingDirectory != str(root)
            ):
                raise LabViolation("Foreign shortcut collision")
        else:
            shortcut = shell.CreateShortcut(str(link))
            shortcut.TargetPath, shortcut.Arguments, shortcut.WorkingDirectory = (
                target,
                arguments,
                str(root),
            )
            shortcut.Save()
            # MS-SHLLINK LinkFlags at 0x14; explicit user activation prompts UAC.
            data = bytearray(link.read_bytes())
            if data[:4] != b"\x4c\x00\x00\x00":
                raise LabViolation("Unexpected ShellLink header")
            flags = int.from_bytes(data[20:24], "little") | 0x2000
            data[20:24] = flags.to_bytes(4, "little")
            link.write_bytes(data)
        if not int.from_bytes(link.read_bytes()[20:24], "little") & 0x2000:
            raise LabViolation("Shortcut must request explicit elevated activation")
        actual = shell.CreateShortcut(str(link))
        if (
            actual.TargetPath.casefold() != target.casefold()
            or actual.Arguments != arguments
            or actual.WorkingDirectory != str(root)
        ):
            raise LabViolation("COM shortcut readback mismatch")
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
        marker = load_marker(root)
        if (
            marker["source_sha"] != release.source_sha
            or marker["release_manifest_sha256"] != release.manifest_sha256
        ):
            raise LabViolation("Existing installation differs; explicit update required")
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
    root.mkdir(exist_ok=True)
    with lifecycle_lock(root):
        if any(root.iterdir()):
            raise LabViolation("Fresh root changed before lifecycle lock")
        subprocess.run(
            [
                "icacls.exe",
                str(root),
                "/inheritance:r",
                "/grant:r",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ],
            check=True,
            capture_output=True,
        )
        for name in (
            "state",
            "runtime",
            "dependencies",
            "releases",
            "private",
            "backups",
            "postgres-data",
        ):
            (root / name).mkdir()
        ident = uuid.uuid4()
        maintenance_parent = (
            Path(os.environ["ProgramData"]) if mode == "production" else root.parent
        )
        maintenance = plain(maintenance_parent / ("TesterReleaseMaintenance_" + ident.hex))
        maintenance.mkdir()
        subprocess.run(
            [
                "icacls.exe",
                str(maintenance),
                "/inheritance:r",
                "/grant:r",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
                "/setowner",
                "*S-1-5-32-544",
            ],
            check=True,
            capture_output=True,
        )
        (maintenance / "evidence").mkdir()
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
            trust_config=str(maintenance / "trust.json"),
            test_desktop=str(plain(desktop)) if desktop else None,
            services={
                "app": "TesterReleaseApp_" + ident.hex,
                "postgres": "TesterReleasePG_" + ident.hex,
            },
            ports={"api": api_port, "postgres": pg_port},
        )
        write_json(root / MARKER, marker)
        journal = root / "state/install.jsonl"
        creation_receipt = root / "state/creation-receipts.jsonl"
        creation_receipt.touch()
        created = {}
        sealed = False

        def stage(name, **details):
            with journal.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        dict(
                            installation_id=str(ident),
                            source_sha=release.source_sha,
                            phase=name,
                            **details,
                        )
                    )
                    + "\n"
                )
            if not sealed:
                # Only invoked while the fresh root is builder-owned and services
                # have never started. Never re-enroll a running/preexisting tree.
                rows = []
                for item in root.rglob("*"):
                    item = plain(item)
                    relative = item.relative_to(root).as_posix()
                    info = item.stat()
                    identity = [info.st_dev, info.st_ino]
                    if created.get(relative) != identity:
                        rows.append(dict(path=relative, identity=identity))
                        created[relative] = identity
                with creation_receipt.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(dict(builder_phase=name, created=rows)) + "\n")

        try:
            stage("copying-verified-payload")
            release_root = root / "releases" / release.source_sha
            shutil.copytree(Path(bundle) / "payload", release_root)
            write_json(root / "state/source-runtime.json", {"files": inventory(release_root)})
            shutil.copytree(Path(bundle) / "runtimes/python", root / "dependencies/python")
            shutil.copytree(Path(bundle) / "runtimes/postgres", root / "dependencies/postgres")
            stage("installing-hash-locked-dependencies")
            python = root / "dependencies/python/python.exe"
            subprocess.run(
                [str(python), "-I", "-B", "-m", "venv", str(root / "dependencies/venv")],
                check=True,
                timeout=120,
            )
            venv = root / "dependencies/venv/Scripts/python.exe"
            subprocess.run(
                [
                    str(venv),
                    "-I",
                    "-B",
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-compile",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "--find-links",
                    str(Path(bundle) / "wheelhouse"),
                    "-r",
                    str(release_root / "requirements-release-windows314.lock"),
                ],
                check=True,
                timeout=300,
            )
            subprocess.run(
                [
                    str(venv),
                    "-B",
                    "-c",
                    "from pathlib import Path; from src.api.release_install import protect_external_bundle; import sys; protect_external_bundle(Path(sys.argv[1]))",
                    str(plain(bundle)),
                ],
                cwd=release_root,
                check=True,
                timeout=180,
            )
            # Stage using the same private runtime implementation as the verified lab.
            code = 'from pathlib import Path; from src.api.lab_scm import stage_service_runtime; from src.api.release_install import load_marker; stage_service_runtime(Path(__import__("sys").argv[1]),marker_reader=load_marker,bootstrap="release_service_bootstrap.py")'
            subprocess.run(
                [str(venv), "-B", "-c", code, str(root)], cwd=release_root, check=True, timeout=180
            )
            write_json(
                root / "state/postgres-runtime.json",
                {"files": inventory(root / "dependencies/postgres")},
            )
            stage("initializing-dedicated-postgres")
            import secrets

            password = secrets.token_urlsafe(36)
            password_path = root / "private/initdb-password.txt"
            password_path.write_text(password, encoding="utf-8")
            pgbin = root / "dependencies/postgres/bin"
            subprocess.run(
                [
                    str(pgbin / "initdb.exe"),
                    "-D",
                    str(root / "postgres-data"),
                    "-U",
                    "tester_owner",
                    "--pwfile",
                    str(password_path),
                    "--auth-host=scram-sha-256",
                    "--auth-local=scram-sha-256",
                    "--encoding=UTF8",
                ],
                check=True,
                timeout=120,
            )
            password_path.unlink()
            (root / "postgres-data/postgresql.conf").write_text(
                "listen_addresses = '127.0.0.1'\nport = " + str(pg_port) + "\n", encoding="utf-8"
            )
            subprocess.run(
                [
                    str(pgbin / "pg_ctl.exe"),
                    "-D",
                    str(root / "postgres-data"),
                    "-l",
                    str(root / "runtime/postgres-bootstrap.log"),
                    "-w",
                    "start",
                ],
                check=True,
                timeout=65,
            )
            try:
                stage("migrating")
                subprocess.run(
                    [
                        str(venv),
                        "-B",
                        "-m",
                        "src.api.release_install",
                        "--provision-database",
                        str(root),
                    ],
                    input=password,
                    text=True,
                    cwd=release_root,
                    check=True,
                    timeout=120,
                )
                control = subprocess.run(
                    [str(pgbin / "pg_controldata.exe"), "-D", str(root / "postgres-data")],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=dict(os.environ, LC_MESSAGES="C", LANG="C"),
                )
                match = re.search(r"Database system identifier:\s*(\d+)", control.stdout)
                if not match:
                    raise LabViolation("Cannot prove dedicated cluster identity")
                write_json(
                    root / "state/postgres-identity.json",
                    dict(installation_id=str(ident), system_identifier=match[1]),
                )
            finally:
                subprocess.run(
                    [
                        str(pgbin / "pg_ctl.exe"),
                        "-D",
                        str(root / "postgres-data"),
                        "-w",
                        "-m",
                        "fast",
                        "stop",
                    ],
                    check=True,
                    timeout=65,
                )
            from .release_service import NativeController

            stage("registering-native-services")
            with NativeController(marker) as controller:
                controller.apply("install")
                stage("applying-service-acls")
                apply_acls(root, marker)
                links = create_shortcuts(root, marker)
                write_json(root / "state/shortcuts.json", {"paths": links})
                stage("sealing-ownership")
                import win32security

                from .release_uninstall import write_ownership

                boundaries = []
                for role, relative in (("app", "runtime"), ("postgres", "postgres-data")):
                    sid, _, _ = win32security.LookupAccountName(
                        None, "NT SERVICE\\" + marker["services"][role]
                    )
                    boundaries.append(
                        dict(
                            path=relative,
                            service_name=marker["services"][role],
                            service_sid=win32security.ConvertSidToStringSid(sid),
                        )
                    )
                mapping = {}
                for relative, _ in release.files:
                    for prefix, installed in (
                        ("payload/", "releases/" + release.source_sha + "/"),
                        ("runtimes/python/", "dependencies/python/"),
                        ("runtimes/postgres/", "dependencies/postgres/"),
                    ):
                        if relative.startswith(prefix):
                            mapping[relative] = installed + relative.removeprefix(prefix)
                ledger = maintenance / "ownership.json"
                ledger_digest = write_ownership(
                    root,
                    marker,
                    release=release,
                    ledger_path=ledger,
                    owned_paths=tuple(created),
                    installed_files=mapping,
                    writer_boundaries=tuple(boundaries),
                    external_paths=tuple(links),
                    trust_config_path=maintenance / "trust.json",
                )
                sealed = True
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
                subprocess.run(
                    [
                        "icacls.exe",
                        str(maintenance / "trust.json"),
                        "/inheritance:r",
                        "/grant:r",
                        "*S-1-5-18:F",
                        "*S-1-5-32-544:F",
                        "/setowner",
                        "*S-1-5-32-544",
                    ],
                    check=True,
                    capture_output=True,
                )
                stage("waiting-for-api-worker-storage")
                controller.apply("start")
            stage("ready")
        except BaseException as exc:
            stage("interrupted", error_type=type(exc).__name__)
            raise
    return marker


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
            ["icacls.exe", str(path), "/inheritance:r", "/grant:r", *grants],
            check=True,
            capture_output=True,
        )

    # Normalize only this newly created, exclusively builder-owned tree before
    # exposing it to service writers; PostgreSQL initdb may create explicit ACLs.
    subprocess.run(
        ["icacls.exe", str(root / "*"), "/reset", "/T", "/Q"], check=True, capture_output=True
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
        ["icacls.exe", str(root), "/setowner", "*S-1-5-32-544", "/Q"],
        check=True,
        capture_output=True,
    )
    for name in ("releases", "dependencies"):
        subprocess.run(
            ["icacls.exe", str(root / name), "/setowner", "*S-1-5-32-544", "/T", "/Q"],
            check=True,
            capture_output=True,
        )
    acl(root / "config.json", ["*S-1-5-18:F", "*S-1-5-32-544:F", sids["app"] + ":R"])


def provision_database(root, marker, password):
    """Run only under the verified private venv; secrets arrive on stdin, never argv."""
    import secrets

    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    from src.storage.migrations import migrate as storage_migrate

    from .db import migrate

    connection = dict(
        host="127.0.0.1", port=marker["ports"]["postgres"], user="tester_owner", password=password
    )
    admin = make_conninfo(**connection, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute("CREATE DATABASE tester")
    owner_dsn = make_conninfo(**connection, dbname="tester")
    migrate(owner_dsn)
    runtime_password = secrets.token_urlsafe(36)
    with psycopg.connect(owner_dsn) as conn:
        storage_migrate(conn)
        conn.execute(
            sql.SQL(
                "CREATE ROLE tester_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Literal(runtime_password))
        )
        conn.execute("REVOKE ALL ON DATABASE tester FROM PUBLIC")
        conn.execute("GRANT CONNECT ON DATABASE tester TO tester_app")
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        for schema in ("tester_platform", "tester_storage"):
            conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO tester_app").format(sql.Identifier(schema))
            )
            conn.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO tester_app"
                ).format(sql.Identifier(schema))
            )
            conn.execute(
                sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO tester_app").format(
                    sql.Identifier(schema)
                )
            )
        conn.execute("REVOKE INSERT, UPDATE, DELETE ON tester_platform.migrations FROM tester_app")
        conn.execute(
            "REVOKE INSERT, UPDATE, DELETE ON tester_storage.schema_migrations FROM tester_app"
        )
    dsn = make_conninfo(
        host="127.0.0.1",
        port=marker["ports"]["postgres"],
        user="tester_app",
        password=runtime_password,
        dbname="tester",
    )
    write_json(
        root / "config.json",
        dict(
            database_url=dsn,
            data_dir=str(root / "runtime"),
            host="127.0.0.1",
            port=marker["ports"]["api"],
            frontend_dir=str(root / "releases" / marker["source_sha"] / "frontend"),
        ),
    )
    # Preserve administrative recovery credential only in the protected private tree.
    write_json(root / "private/database-owner.json", {"database_url": owner_dsn})


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
