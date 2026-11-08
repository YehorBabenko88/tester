"""Single platform gate. Run ONLY against a disposable TEST_DATABASE_URL."""

import argparse
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(*args):
    # pwsh 7 prepends incompatible built-ins to the inherited PSModulePath.
    # Windows PowerShell 5 descendants must rebuild their own edition defaults.
    # Isolate only the child environment; never change host/user settings.
    child_env = (
        {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
        if os.name == "nt"
        else None
    )
    subprocess.run([sys.executable, *args], cwd=ROOT, check=True, env=child_env)


def wait_health(port, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Supervisor exited before readiness")
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            try:
                connection.request("GET", "/api/health")
                response = connection.getresponse()
                data = json.loads(response.read())
                if response.status in {200, 503} and all(
                    data["data"].get(k) == "ok" for k in ("database", "worker", "storage")
                ):
                    return
            finally:
                connection.close()
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.25)
    raise RuntimeError("API/DB/worker readiness timeout")


def lifecycle(dsn):
    from src.api.db import migrate

    migrate(dsn)
    with tempfile.TemporaryDirectory(prefix="tester-acceptance-") as directory:
        root = Path(directory)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        env.pop("TESTER_CONFIG_FILE", None)
        env.update(
            TESTER_DATABASE_URL=dsn,
            TESTER_DATA_DIR=str(root),
            TESTER_PORT=str(port),
            TESTER_MIN_FREE_BYTES="0",
        )
        process = subprocess.Popen([sys.executable, "-m", "src.api.supervisor"], cwd=ROOT, env=env)
        try:
            wait_health(port, process)
            (root / "stop.request").touch()
            assert process.wait(timeout=30) == 0, "Supervisor failed graceful shutdown"
            assert not (root / "supervisor.json").exists()
        finally:
            if process.poll() is None:
                (root / "stop.request").touch()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    print(
        "PASS: real API + PostgreSQL + worker operational readiness and graceful shutdown (provider unavailability remains degraded)"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--integration", action="store_true")
    parser.add_argument("--security", action="store_true")
    args = parser.parse_args()
    check("scripts/runtime_preflight.py")
    check("scripts/check_contract_pin.py")
    check("-m", "ruff", "check", "src/api", "tests/platform", "scripts")
    check("-m", "ruff", "format", "--check", "src/api", "tests/platform", "scripts")
    check("scripts/export_contract.py", "--check")
    check("-m", "pytest", "-q", "--timeout=20", "--basetemp=.scratch/pytest-acceptance")
    if args.integration:
        dsn = os.environ.get("TEST_DATABASE_URL")
        if not dsn:
            raise SystemExit("--integration requires disposable TEST_DATABASE_URL")
        lifecycle(dsn)
    if args.security:
        check("-m", "bandit", "-r", "src/api", "scripts", "-ll", "-q")
        check(
            "-m",
            "pip_audit",
            "--skip-editable",
            "--cache-dir",
            str(ROOT / ".scratch/audit-cache"),
            "--timeout",
            "15",
        )
    print("PASS: requested acceptance gates")


if __name__ == "__main__":
    main()
