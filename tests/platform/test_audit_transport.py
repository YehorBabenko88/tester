"""Audit retry policy only: no network calls or vulnerability suppression."""

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

TRACE = 'Traceback (most recent call last):\n  File "/site-packages/pip_audit/__main__.py", line 8, in <module>\n    audit()\nrequests.exceptions.ConnectionError: connection reset\n'
ARGS = ["--skip-editable", "--cache-dir", "/owned/audit-cache", "--timeout", "15"]


def module():
    path = Path("scripts/audit_transport.py")
    assert path.is_file(), "bounded audit helper missing"
    spec = importlib.util.spec_from_file_location("audit_transport", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def exercise(monkeypatch, results):
    m = module()
    calls, delays = [], []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(returncode=result[0], stdout=result[1], stderr=result[2])

    monkeypatch.setattr(m.subprocess, "run", run)
    monkeypatch.setattr(m.time, "sleep", delays.append)
    code = m.run_audit(ARGS)
    return code, calls, delays


def test_transport_failure_then_actual_success_preserves_command(monkeypatch, capsys):
    code, calls, delays = exercise(
        monkeypatch, [(1, "", TRACE), (0, "", "No known vulnerabilities found\n")]
    )
    assert code == 0 and len(calls) == 2 and delays == [1]
    assert all(call[0][1:] == ["-m", "pip_audit", *ARGS] for call in calls)
    assert all(0 < call[1]["timeout"] <= 180 for call in calls)
    assert "connection reset" in capsys.readouterr().err


def test_transport_exhaustion_remains_nonzero_and_never_third_attempt(monkeypatch):
    code, calls, delays = exercise(monkeypatch, [(1, "", TRACE), (9, "", TRACE)])
    assert code == 9 and len(calls) == 2 and delays == [1]


@pytest.mark.parametrize(
    "result",
    [
        (1, "Found 1 known vulnerability: CVE-2026-1234\n", ""),
        (1, "Found 1 known vulnerability: CVE-2026-1234\n", TRACE),
        (2, "", "usage: pip-audit: invalid option\n"),
        (3, "", TRACE.replace("requests.exceptions.ConnectionError", "ValueError")),
        (4, "", "requests.exceptions.ConnectionError: no terminal traceback\n"),
        (5, "", TRACE + "other terminal failure\n"),
    ],
)
def test_vulnerability_and_nontransport_failures_are_never_retried(monkeypatch, result):
    code, calls, delays = exercise(monkeypatch, [result])
    assert code == result[0] and len(calls) == 1 and delays == []


def test_second_attempt_vulnerability_is_not_suppressed(monkeypatch, capsys):
    code, calls, _ = exercise(
        monkeypatch, [(1, "", TRACE), (1, "CVE-2026-1234 vulnerable package\n", "")]
    )
    assert code == 1 and len(calls) == 2
    assert "CVE-2026-1234" in capsys.readouterr().out


def test_process_timeout_fails_without_retry(monkeypatch):
    code, calls, delays = exercise(monkeypatch, [subprocess.TimeoutExpired("pip_audit", 180)])
    assert code != 0 and len(calls) == 1 and delays == []


def test_output_redacts_url_credentials_without_hiding_vulnerability(monkeypatch, capsys):
    exercise(
        monkeypatch, [(1, "CVE-2026-1234 https://alice:SECRET@example.org/a?token=PRIVATE\n", "")]
    )
    output = capsys.readouterr().out
    assert "SECRET" not in output and "PRIVATE" not in output
    assert "CVE-2026-1234" in output and "example.org" in output


def test_stderr_vulnerability_evidence_prevents_transport_retry(monkeypatch):
    result = (1, "", "Found 1 known vulnerability in 1 package\n" + TRACE)
    code, calls, delays = exercise(monkeypatch, [result])
    assert code == 1 and len(calls) == 1 and delays == []


@pytest.mark.parametrize("kind", ["Timeout", "ConnectTimeout", "ReadTimeout"])
def test_explicit_requests_terminal_timeout_is_retryable(monkeypatch, kind):
    trace = TRACE.replace("ConnectionError", kind)
    code, calls, delays = exercise(monkeypatch, [(1, "", trace), (0, "", "")])
    assert code == 0 and len(calls) == 2 and delays == [1]


@pytest.mark.parametrize("code", [-9, 7])
def test_unknown_or_signal_exit_is_not_transport_retry(monkeypatch, code):
    result, calls, delays = exercise(monkeypatch, [(code, "", TRACE)])
    assert result == code and len(calls) == 1 and delays == []
