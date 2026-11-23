"""Disposable Windows lifecycle; canonical deployment remains plan-only."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from src.api.lab_install import LabViolation, plain, verify_tree

NAMES = ("TESTER — Запуск", "TESTER — Удаление")


def canonical_plan():
    return {
        "root": r"C:\TesterLab",
        "status": "preparation_only",
        "guardians_enabled": False,
        "os_services": ["dedicated_postgres", "api_supervisor"],
        "logical_roles": ["postgres", "api", "worker"],
        "shortcuts": list(NAMES),
        "production_acceptance": False,
    }


def _digest(path):
    return hashlib.sha256(plain(path).read_bytes()).hexdigest()


def _json(path, value):
    with plain(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)


def _run(args, *, capture=True, diagnostic=None, **kwargs):
    result = subprocess.run(
        [str(arg) for arg in args],
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        timeout=90,
        **kwargs,
    )
    if diagnostic is not None:
        # Used only for initdb's nonsecret diagnostics, never bootstrap/DSNs.
        plain(diagnostic).write_bytes(result.stdout or b"")
    if result.returncode:
        raise LabViolation("Owned subprocess failed; inspect disposable logs locally")
    return (result.stdout or b"").decode("utf-8", errors="replace")


class UnifiedInstaller:
    def __init__(self, root, desktop, *, disposable_parent, manifest_sha256=""):
        self.parent = plain(disposable_parent).resolve()
        temp = plain(tempfile.gettempdir()).resolve()
        self.root, self.desktop = plain(root).resolve(), plain(desktop).resolve()
        if (
            temp not in self.parent.parents
            and temp != self.parent
            or self.parent == temp
            or self.root.parent != self.parent
            or self.desktop.parent != self.parent
            or self.root == self.desktop
            or self.root.name.casefold() == "testerlab"
        ):
            raise LabViolation(
                "Only separate direct children of an explicit temporary fixture are allowed"
            )
        self.pin = manifest_sha256
        self.lock = self.parent / (self.root.name + ".operation.lock")
        self.receipt = self.parent / (self.root.name + ".removal.json")
        self.process = None

    @contextlib.contextmanager
    def operation(self):
        self.parent.mkdir(parents=True, exist_ok=True)
        with plain(self.lock).open("a+b") as stream:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def stage(self, source, inventory, source_sha):
        with self.operation():
            source = plain(source)
            verify_tree(source, inventory)
            if not re.fullmatch("[0-9a-f]{40}", source_sha):
                raise LabViolation("Exact source SHA required")
            if self.root.exists() or self.desktop.exists() or self.receipt.exists():
                raise LabViolation("Fresh fixture root and desktop required")
            self.root.mkdir()
            self.desktop.mkdir()
            for name in ("config", "data", "postgres-data", "logs", "scripts", "install"):
                (self.root / name).mkdir()
            shutil.copytree(source, self.root / "app")
            verify_tree(self.root / "app", inventory)
            manifest = {
                "schema": "tester-disposable/v1",
                "root": str(self.root),
                "desktop": str(self.desktop),
                "installation_id": uuid.uuid4().hex,
                "source_sha": source_sha,
                "inventory": inventory,
                "guardians_enabled": False,
                "root_identity": self.root.stat().st_ino,
                "cluster_identity": (self.root / "postgres-data").stat().st_ino,
            }
            path = self.root / "install/install.json"
            _json(path, manifest)
            self.pin = _digest(path)
            return self.pin

    def verify(self):
        path = self.root / "install/install.json"
        if not re.fullmatch("[0-9a-f]{64}", self.pin) or _digest(path) != self.pin:
            raise LabViolation("Independent manifest pin mismatch")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (
            manifest["schema"] != "tester-disposable/v1"
            or manifest["root"] != str(self.root)
            or manifest["desktop"] != str(self.desktop)
            or manifest["guardians_enabled"] is not False
        ):
            raise LabViolation("Foreign manifest")
        if (
            self.root.stat().st_ino != manifest["root_identity"]
            or (self.root / "postgres-data").stat().st_ino != manifest["cluster_identity"]
        ):
            raise LabViolation("Directory identity changed")
        for path in self.root.rglob("*"):
            plain(path)
        verify_tree(self.root / "app", manifest["inventory"])
        runtime = self.root / "install/runtime.json"
        if runtime.exists():
            if _digest(runtime) != manifest.get("runtime_sha256"):
                raise LabViolation("Runtime metadata tampered")
            data = json.loads(runtime.read_text())
            for path, digest in data["executables"].items():
                if _digest(path) != digest:
                    raise LabViolation("Runtime executable tampered")
        return manifest

    def _runtime(self):
        self.verify()
        data = json.loads((self.root / "install/runtime.json").read_text())
        if _digest(self.root / "config/config.json") != data["config_sha256"]:
            raise LabViolation("Configuration tampered")
        return data

    def _pg(self, data, action):
        if action == "stop":
            import psutil

            pid = int((self.root / "postgres-data/postmaster.pid").read_text().splitlines()[0])
            process = psutil.Process(pid)
            expected = Path(data["pg_ctl"]).with_name(
                "postgres.exe" if os.name == "nt" else "postgres"
            )
            if plain(process.exe()).resolve() != expected.resolve():
                raise LabViolation("Foreign PostgreSQL process")
            arguments = process.cmdline()
            if (
                "-D" not in arguments
                or plain(arguments[arguments.index("-D") + 1]).resolve()
                != self.root / "postgres-data"
            ):
                raise LabViolation("Foreign PostgreSQL command line")
        return _run(
            [
                data["pg_ctl"],
                "-D",
                self.root / "postgres-data",
                "-w",
                "-t",
                "45",
                action,
                *(["-m", "fast"] if action == "stop" else ["-l", self.root / "logs/postgres.log"]),
            ],
            capture=False,
        )

    def initialize(self, pg_bin, python, *, pg_port, api_port):
        with self.operation():
            self.verify()
            if (
                pg_port == 5432
                or pg_port == api_port
                or not all(1024 <= p <= 65535 for p in (pg_port, api_port))
            ):
                raise LabViolation("Dedicated non-5432 distinct ports required")
            for port in (pg_port, api_port):
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", port))
            pg_bin, python = plain(pg_bin), plain(python)
            suffix = ".exe" if os.name == "nt" else ""
            executables = [pg_bin / (name + suffix) for name in ("initdb", "pg_ctl", "postgres")]
            executables.append(python)
            process_executable = Path(
                _run([python, "-B", "-c", "import sys;print(sys._base_executable)"]).strip()
            )
            executables.append(process_executable)
            data = {
                "pg_ctl": str(executables[1]),
                "python": str(python),
                "process_executable": str(process_executable),
                "pg_port": pg_port,
                "api_port": api_port,
                "executables": {str(p): _digest(p) for p in executables},
            }
            cluster = self.root / "postgres-data"
            _run(
                [
                    executables[0],
                    "-D",
                    cluster,
                    "-U",
                    "tester_fixture_admin",
                    "-A",
                    "trust",
                    "--no-locale",
                    "--encoding=UTF8",
                ],
                diagnostic=self.root / "logs/initdb.log",
            )
            with (cluster / "postgresql.conf").open("a") as stream:
                stream.write(f"\nport={pg_port}\nlisten_addresses='127.0.0.1'\n")
            try:
                self._pg(data, "start")
                env = self._environment()
                env.pop("TESTER_CONFIG_FILE")
                env["TESTER_ADMIN_DSN"] = (
                    f"host=127.0.0.1 port={pg_port} user=tester_fixture_admin dbname=postgres"
                )
                _run(
                    [
                        python,
                        "-B",
                        "-m",
                        "src.api.bootstrap",
                        "--data-dir",
                        self.root / "data",
                        "--database",
                        "tester_fixture",
                        "--role",
                        "tester_fixture",
                        "--port",
                        api_port,
                        "--min-free-bytes",
                        "0",
                    ],
                    cwd=self.root / "app",
                    env=env,
                )
                shutil.move(self.root / "data/config.json", self.root / "config/config.json")
                data["config_sha256"] = _digest(self.root / "config/config.json")
                data["pg_config_sha256"] = _digest(cluster / "postgresql.conf")
            finally:
                if (cluster / "postmaster.pid").exists():
                    self._pg(data, "stop")
            module = _run(
                [
                    python,
                    "-B",
                    "-c",
                    "import src.api.supervisor;print(src.api.supervisor.__file__)",
                ],
                cwd=self.root / "app",
                env=self._environment(),
            ).strip()
            if plain(module).resolve() != self.root / "app/src/api/supervisor.py":
                raise LabViolation("Supervisor resolved outside installed source")
            data["supervisor_module"] = module
            _json(self.root / "install/runtime.json", data)
            manifest_path = self.root / "install/install.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["runtime_sha256"] = _digest(self.root / "install/runtime.json")
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            self.pin = _digest(manifest_path)
            return self.pin

    def _environment(self):
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("TESTER_", "GUARDIANS_", "GIT_CONFIG_"))
        }
        env.update(
            GUARDIANS_ENABLED="false",
            PYTHONDONTWRITEBYTECODE="1",
            TESTER_CONFIG_FILE=str(self.root / "config/config.json"),
            TESTER_SERVICE_INSTANCE=json.loads((self.root / "install/install.json").read_text())[
                "installation_id"
            ],
        )
        return env

    def _identity(self, data):
        import psycopg

        with psycopg.connect(
            host="127.0.0.1",
            port=data["pg_port"],
            user="tester_fixture_admin",
            dbname="postgres",
            connect_timeout=3,
            autocommit=True,
        ) as conn:
            actual = Path(conn.execute("SHOW data_directory").fetchone()[0])
            if os.path.normcase(str(actual)) != os.path.normcase(str(self.root / "postgres-data")):
                raise LabViolation("Foreign PostgreSQL cluster")

    def start(self):
        self._start_acquired = False
        with self.operation(), self._rollback_start():
            data = self._runtime()
            if (self.root / "install/process.json").exists():
                raise LabViolation("Already started; stop before restarting")
            if _digest(self.root / "postgres-data/postgresql.conf") != data["pg_config_sha256"]:
                raise LabViolation("PostgreSQL configuration tampered")
            overrides = self.root / "postgres-data/postgresql.auto.conf"
            if any(
                line.strip() and not line.lstrip().startswith("#")
                for line in overrides.read_text().splitlines()
            ):
                raise LabViolation("PostgreSQL override refused")
            if (self.root / "postgres-data/postmaster.pid").exists():
                raise LabViolation("Existing cluster process requires explicit stop")
            self._start_acquired = True
            self._pg(data, "start")
            self._identity(data)
            (self.root / "data/stop.request").unlink(missing_ok=True)
            log = (self.root / "logs/supervisor.log").open("ab")
            try:
                self.process = subprocess.Popen(
                    [data["python"], "-B", "-m", "src.api.supervisor"],
                    cwd=self.root / "app",
                    env=self._environment(),
                    stdout=log,
                    stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            finally:
                log.close()
            import psutil

            _json(
                self.root / "install/process.json",
                {
                    "pid": self.process.pid,
                    "created": psutil.Process(self.process.pid).create_time(),
                },
            )
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise LabViolation("Supervisor exited before readiness")
                try:
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", data["api_port"], timeout=2
                    )
                    try:
                        connection.request("GET", "/api/health")
                        response = connection.getresponse()
                        health = json.loads(response.read(65537))["data"]
                        manifest = self.verify()
                        descendants = psutil.Process(self.process.pid).children(recursive=True)
                        owns_listener = any(
                            connection.status == "LISTEN"
                            and connection.laddr.ip == "127.0.0.1"
                            and connection.laddr.port == data["api_port"]
                            for child in descendants
                            for connection in child.net_connections(kind="tcp")
                        )
                        if (
                            response.status in (200, 503)
                            and owns_listener
                            and health.get("instance_id") == manifest["installation_id"]
                            and all(
                                health.get(key) == "ok" for key in ("database", "worker", "storage")
                            )
                        ):
                            return {
                                "status": "ready",
                                "backend": "disposable_process",
                                "api_port": data["api_port"],
                                "health": health,
                            }
                    finally:
                        connection.close()
                except (OSError, http.client.HTTPException):
                    time.sleep(0.25)
            raise LabViolation("Readiness timed out; stop explicitly and inspect fixture log")

    @contextlib.contextmanager
    def _rollback_start(self):
        try:
            yield
        except Exception as failure:
            if not self._start_acquired:
                raise
            try:
                if self.process is not None and self.process.poll() is None:
                    (self.root / "data/stop.request").touch()
                    self.process.wait(timeout=35)
                self._stop()
            except Exception as cleanup:
                raise LabViolation(
                    "Startup failed; cleanup incomplete; preserve fixture and inspect owned state"
                ) from cleanup
            raise failure

    def _process(self):
        import psutil

        path = self.root / "install/process.json"
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        try:
            process = psutil.Process(record["pid"])
            if abs(process.create_time() - record["created"]) > 0.01:
                raise LabViolation("PID reused by foreign process")
            if (
                process.cmdline()[1:] != ["-B", "-m", "src.api.supervisor"]
                or plain(process.cwd()) != self.root / "app"
            ):
                raise LabViolation("Foreign supervisor process")
            runtime = json.loads((self.root / "install/runtime.json").read_text())
            if not any(
                os.path.samefile(process.exe(), runtime[name])
                for name in ("python", "process_executable")
            ):
                raise LabViolation("Foreign supervisor executable")
            return process
        except psutil.NoSuchProcess:
            return None

    def stop(self):
        with self.operation():
            return self._stop()

    def _stop(self):
        data = self._runtime()
        process = self._process()
        if process:
            children = process.children(recursive=True)
            (self.root / "data/stop.request").touch()
            try:
                process.wait(timeout=35)
                for child in children:
                    child.wait(timeout=5)
            except Exception as exc:
                raise LabViolation("Owned process exit not proven; no deletion") from exc
        if (self.root / "postgres-data/postmaster.pid").exists():
            # _pg reattests exact executable, PID and cluster command line before stop.
            self._pg(data, "stop")
        (self.root / "install/process.json").unlink(missing_ok=True)
        self._assert_no_owned_processes()
        return {"status": "stopped"}

    def _assert_no_owned_processes(self):
        import psutil

        controllers = {os.getpid()}
        script = str(self.root / "app/scripts/windows/Manage-UnifiedTester.ps1")
        powershell = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        for ancestor in psutil.Process().parents():
            try:
                arguments = ancestor.cmdline()
                if (
                    os.name == "nt"
                    and "-File" in arguments
                    and arguments[arguments.index("-File") + 1] == script
                    and plain(ancestor.exe()).resolve() == powershell.resolve()
                ):
                    controllers.add(ancestor.pid)
                runtime = self.root / "install/runtime.json"
                python = (
                    json.loads(runtime.read_text())["python"]
                    if runtime.exists()
                    else sys.executable
                )
                if (
                    arguments
                    and arguments[0] == python
                    and "-m" in arguments
                    and arguments[arguments.index("-m") + 1] == "src.api.unified_windows_install"
                    and "--root" in arguments
                    and plain(arguments[arguments.index("--root") + 1]).resolve() == self.root
                ):
                    controllers.add(ancestor.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for process in psutil.process_iter(["pid", "cwd", "cmdline"]):
            if process.pid in controllers:
                continue
            try:
                cwd = process.info["cwd"]
                if cwd:
                    # Foreign OS processes may legitimately use symlink paths.
                    # Apply our no-reparse rule only within the owned tree.
                    path = Path(cwd).resolve()
                    if path == self.root or self.root in path.parents:
                        plain(path)
                        raise LabViolation("Live process beneath owned tree blocks deletion")
                for argument in process.info["cmdline"] or []:
                    if str(self.root).casefold() in argument.casefold():
                        raise LabViolation("Live process referencing owned tree blocks deletion")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def uninstall(self, phrase, *, second_confirmation=False):
        if phrase != "DELETE TESTER" or second_confirmation is not True:
            raise LabViolation("Typed DELETE TESTER and second confirmation required")
        with self.operation():
            if not self.root.exists():
                if self.receipt.exists() and json.loads(self.receipt.read_text()) == {
                    "status": "removed",
                    "manifest_sha256": self.pin,
                }:
                    return {"status": "already_absent"}
                raise LabViolation("Absent unowned installation")
            self.verify()
            if (self.root / "install/runtime.json").exists():
                self._stop()
            self._assert_no_owned_processes()
            for path in self.root.rglob("*"):
                plain(path)
            links = self._shortcut_specs()
            for path in self.desktop.iterdir():
                plain(path)
                if path.name in links:
                    self._shortcut(path, links[path.name], create=False)
            try:
                for name in links:
                    path = self.desktop / name
                    if path.exists():
                        path.unlink()
                shutil.rmtree(self.root)
            except OSError as exc:
                raise LabViolation("Incomplete removal; no success receipt") from exc
            _json(self.receipt, {"status": "removed", "manifest_sha256": self.pin})
            return {"status": "removed"}

    def _shortcut_specs(self):
        script = str(self.root / "app/scripts/windows/Manage-UnifiedTester.ps1")
        runtime = self.root / "install/runtime.json"
        python = json.loads(runtime.read_text())["python"] if runtime.exists() else "python"
        common = f'-NoProfile -File "{script}" -Python "{python}" -Root "{self.root}" -Desktop "{self.desktop}" -DisposableParent "{self.parent}" -ManifestSHA256 {self.pin}'
        return {
            NAMES[0] + ".lnk": common + " -Action start",
            NAMES[1] + ".lnk": common + " -Action uninstall",
        }

    def _shortcut(self, path, arguments, *, create):
        plain(path)
        powershell = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        temporary = self.desktop / ("shortcut-" + uuid.uuid4().hex + ".lnk")
        spec = {
            "path": str(path),
            "temporary": str(temporary),
            "target": str(powershell),
            "arguments": arguments,
            "working": str(self.root),
            "create": create,
        }
        code = """$ErrorActionPreference='Stop'; $s=$env:TESTER_LINK_SPEC|ConvertFrom-Json
$w=New-Object -ComObject WScript.Shell
try {
 if($s.create){if(Test-Path -LiteralPath $s.path){throw 'Shortcut collision'}; $l=$w.CreateShortcut($s.temporary);$l.TargetPath=$s.target;$l.Arguments=$s.arguments;$l.WorkingDirectory=$s.working;$l.Save();Move-Item -LiteralPath $s.temporary -Destination $s.path}
 Copy-Item -LiteralPath $s.path -Destination $s.temporary
 $l=$w.CreateShortcut($s.temporary)
 if($l.TargetPath -ine $s.target -or $l.Arguments -cne $s.arguments -or $l.WorkingDirectory -ine $s.working){throw 'Foreign shortcut'}
}finally{if(Test-Path -LiteralPath $s.temporary){Remove-Item -LiteralPath $s.temporary -Force}}
"""
        env = os.environ.copy()
        env["TESTER_LINK_SPEC"] = json.dumps(spec)
        _run(
            [
                powershell,
                "-NoProfile",
                "-EncodedCommand",
                base64.b64encode(code.encode("utf-16le")).decode(),
            ],
            env=env,
        )

    def create_shortcuts(self):
        if os.name != "nt":
            raise LabViolation("Actual Windows COM required")
        with self.operation():
            self.verify()
            for name, arguments in self._shortcut_specs().items():
                self._shortcut(self.desktop / name, arguments, create=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "status", "start", "stop", "uninstall"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--desktop", type=Path)
    parser.add_argument("--disposable-parent", type=Path)
    parser.add_argument("--manifest-sha256", default="")
    args = parser.parse_args()
    if args.action == "plan":
        print(json.dumps(canonical_plan(), ensure_ascii=True))
        return
    manager = UnifiedInstaller(
        args.root,
        args.desktop,
        disposable_parent=args.disposable_parent,
        manifest_sha256=args.manifest_sha256,
    )
    if args.action == "uninstall":
        print("Destroy ALL owned cluster, candles, results, configuration and logs without backup.")
        result = manager.uninstall(
            input("Type DELETE TESTER: "),
            second_confirmation=input("Confirm complete destruction (YES): ") == "YES",
        )
    elif args.action == "status":
        manager.verify()
        result = {"status": "verified", "production_acceptance": False}
    else:
        result = getattr(manager, args.action)()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
