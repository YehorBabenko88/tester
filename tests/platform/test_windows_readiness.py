"""Real loopback HTTP readiness checks through Windows PowerShell and pwsh."""

import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[2] / "scripts/windows/Tester-Readiness.ps1"


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher's actual PowerShell boundary")
@pytest.mark.parametrize("engine", ["powershell", "pwsh"])
@pytest.mark.parametrize(
    "status,updates,expected",
    [
        (200, {}, True),
        (503, {"status": "degraded", "provider": "unavailable"}, True),
        (503, {"status": "degraded", "provider": "unavailable", "database": "unavailable"}, False),
        (503, {"status": "degraded", "provider": "unavailable", "worker": "unavailable"}, False),
        (503, {"status": "degraded", "provider": "unavailable", "storage": "low"}, False),
        (503, {"status": "degraded"}, False),
        (500, {}, False),
        (200, {"database": ["ok"]}, False),
        (503, {"status": "degraded", "provider": "unknown"}, False),
        (200, {"_body": b""}, False),
        (200, {"_body": b"<html>not health</html>"}, False),
        (200, {"_body": b"x" * 65537}, False),
        (200, {"_body": b'{"schema_version":"2.0","data":{}}'}, False),
        (302, {}, False),
        (200, {"_trickle": True}, False),
        (200, {"_stall": True}, False),
        (200, {"_array": True}, False),
    ],
)
def test_actual_http_readiness(engine, status, updates, expected):
    executable = shutil.which(engine)
    assert executable, f"Required Windows test shell missing: {engine}"
    data = dict(status="ok", database="ok", worker="ok", storage="ok", provider="configured")
    data.update({key: value for key, value in updates.items() if not key.startswith("_")})
    document = dict(schema_version="1.0", data=data)
    if updates.get("_array"):
        document = [document]
    payload = updates.get("_body", json.dumps(document).encode())

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/api/health"
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/must-not-follow")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                if updates.get("_stall"):
                    self.wfile.flush()
                    time.sleep(6)
                if updates.get("_trickle"):
                    for byte in payload:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.1)
                else:
                    self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        command = (
            "$ErrorActionPreference='Stop'; . '"
            + str(HELPER).replace("'", "''")
            + "'; Get-TesterOperationalReadiness -Url 'http://127.0.0.1:"
            + str(server.server_port)
            + "/api/health' | ConvertTo-Json -Compress"
        )
        started = time.monotonic()
        result = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        if updates.get("_stall"):
            assert time.monotonic() - started < 5, "HTTP body read must have a finite deadline"
        receipt = json.loads(result.stdout)
        assert receipt["operational"] is expected
        assert receipt["http_status"] == status
        if expected:
            assert receipt["health"] == data["status"]
            assert receipt["provider"] == data["provider"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
