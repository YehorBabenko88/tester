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
        assert marker["schema"] == SCHEMA and marker["root"] == str(root)
        assert re.fullmatch("[0-9a-f]{40}", marker["source_sha"])
        assert re.fullmatch("[0-9a-f]{64}", marker["release_manifest_sha256"])
        assert marker["services"] == {
            "app": "TesterReleaseApp_" + ident.hex,
            "postgres": "TesterReleasePG_" + ident.hex,
        }
        assert marker["ports"]["postgres"] != 5432
        assert len(set(marker["ports"].values())) == 2
        assert all(isinstance(p, int) and 1024 <= p <= 65535 for p in marker["ports"].values())
        validate_destination(root, mode=marker["mode"], desktop=marker.get("test_desktop"))
    except (OSError, ValueError, KeyError, AssertionError, TypeError) as exc:
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
        "payload/requirements-windows-lab.lock",
        "runtimes/python/python.exe",
        "runtimes/postgres/bin/initdb.exe",
        "runtimes/postgres/bin/pg_ctl.exe",
    )
    for relative in required:
        if not plain(Path(bundle) / relative).is_file():
            raise LabViolation("Incomplete offline release: " + relative)
    root.mkdir()
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
            (root / name).mkdir()
        ident = uuid.uuid4()
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
            test_desktop=str(plain(desktop)) if desktop else None,
            services={
                "app": "TesterReleaseApp_" + ident.hex,
                "postgres": "TesterReleasePG_" + ident.hex,
            },
            ports={"api": api_port, "postgres": pg_port},
        )
        write_json(root / MARKER, marker)
        journal = root / "state/install.json"

        def stage(name):
            write_json(
                journal, dict(installation_id=str(ident), source_sha=release.source_sha, phase=name)
            )

        try:
            stage("copying-verified-payload")
            release_root = root / "releases" / release.source_sha
            shutil.copytree(Path(bundle) / "payload", release_root)
            shutil.copytree(Path(bundle) / "runtimes/python", root / "dependencies/python")
            shutil.copytree(Path(bundle) / "runtimes/postgres", root / "dependencies/postgres")
            stage("installing-hash-locked-dependencies")
            python = root / "dependencies/python/python.exe"
            subprocess.run(
                [str(python), "-I", "-m", "venv", str(root / "dependencies/venv")],
                check=True,
                timeout=120,
            )
            venv = root / "dependencies/venv/Scripts/python.exe"
            subprocess.run(
                [
                    str(venv),
                    "-I",
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "--find-links",
                    str(Path(bundle) / "wheelhouse"),
                    "-r",
                    str(release_root / "requirements-windows-lab.lock"),
                ],
                check=True,
                timeout=300,
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
                import psycopg
                from psycopg.conninfo import make_conninfo

                admin = make_conninfo(
                    host="127.0.0.1",
                    port=pg_port,
                    user="tester_owner",
                    password=password,
                    dbname="postgres",
                )
                with psycopg.connect(admin, autocommit=True) as conn:
                    conn.execute("CREATE DATABASE tester")
                dsn = make_conninfo(
                    host="127.0.0.1",
                    port=pg_port,
                    user="tester_owner",
                    password=password,
                    dbname="tester",
                )
                # Use existing storage and platform migration engines, not a second schema implementation.
                stage("migrating")
                code = "import sys; from src.api.db import migrate; from src.storage.migrations import migrate as sm; import psycopg; dsn=sys.stdin.read(); migrate(dsn);\nwith psycopg.connect(dsn) as c: sm(c)"
                subprocess.run(
                    [str(venv), "-B", "-c", code],
                    input=dsn,
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
                config = dict(
                    database_url=dsn,
                    data_dir=str(root / "runtime"),
                    host="127.0.0.1",
                    port=api_port,
                    frontend_dir=str(release_root / "frontend"),
                )
                write_json(root / "config.json", config)
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
            stage("applying-service-acls")
            apply_acls(root)
            from .release_service import NativeController

            stage("registering-native-services")
            with NativeController(marker) as controller:
                controller.apply("install")
                stage("waiting-for-api-worker-storage")
                controller.apply("start")
            write_json(root / "state/shortcuts.json", {"paths": create_shortcuts(root, marker)})
            stage("ready")
        except BaseException as exc:
            write_json(
                journal,
                dict(
                    installation_id=str(ident),
                    source_sha=release.source_sha,
                    phase="interrupted",
                    error_type=type(exc).__name__,
                ),
            )
            raise
    return marker


def apply_acls(root):
    """Administrators own immutable code; LocalService gets only runtime/cluster write."""
    import subprocess

    def acl(path, grants):
        subprocess.run(
            ["icacls.exe", str(path), "/inheritance:r", "/grant:r", *grants],
            check=True,
            capture_output=True,
        )

    read = ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-19:(OI)(CI)RX"]
    acl(root, read)
    for name in ("dependencies", "releases", "state"):
        acl(root / name, read)
    for name in ("runtime", "postgres-data"):
        acl(root / name, ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-19:(OI)(CI)M"])
    acl(root / "private", ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"])
    acl(root / "config.json", ["*S-1-5-18:F", "*S-1-5-32-544:F", "*S-1-5-19:R"])


def main():
    import argparse

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
