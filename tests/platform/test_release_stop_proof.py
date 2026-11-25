"""NativeStopProof must not trust a stale postmaster.pid or historical parent PIDs (R3)."""

import os
import sys
import types
import uuid

import pytest

from src.api import release_service as rs


class Handle:
    def __init__(self, pid):
        self.pid = pid
        self.closed = False

    def Close(self):
        self.closed = True

    def __int__(self):
        return self.pid


class Probe:
    """Injected read-only process facts; any mutating call would be an AttributeError."""

    def __init__(self, processes, *, denied=()):
        self.processes = processes  # pid -> dict(image, sid, created)
        self.denied = set(denied)
        self.opened = []
        self.token_queries = []

    def open(self, pid):
        if pid in self.denied:
            exc = OSError("access denied")
            exc.winerror = 5
            raise exc
        if pid not in self.processes:
            return None
        handle = Handle(pid)
        self.opened.append(handle)
        return handle

    def image(self, handle):
        return self.processes[handle.pid]["image"]

    def token_user(self, handle):
        self.token_queries.append(handle.pid)
        return self.processes[handle.pid]["sid"]

    def created(self, handle):
        return self.processes[handle.pid]["created"]

    def owned_console(self, handle, image, parent, postgres_image, snapshot_started):
        return False


@pytest.fixture
def fake_win32job(monkeypatch):
    module = types.ModuleType("win32job")

    def open_job(access, inherit, name):
        if module.pg_job_exists and "PostgresJob" in name:
            return Handle(0)
        exc = OSError("no job")
        exc.winerror = 2
        raise exc

    module.OpenJobObject = open_job
    module.pg_job_exists = False
    monkeypatch.setitem(sys.modules, "win32job", module)
    monkeypatch.setattr(rs, "_validate_job_owner", lambda handle, marker, role="app": None)
    return module


def install(tmp_path, *, pg_pid):
    ident = uuid.uuid4()
    marker = dict(
        root=str(tmp_path),
        installation_id=str(ident),
        source_sha="a" * 40,
        services=dict(app="TesterReleaseApp_" + ident.hex, postgres="TesterReleasePG_" + ident.hex),
        ports=dict(api=18767, postgres=55471),
    )
    data = tmp_path / "postgres-data"
    data.mkdir()
    pidfile = data / "postmaster.pid"
    pidfile.write_text(f"{pg_pid}\n{data}\n1700000000\n55471\n")
    os.utime(pidfile, (1000.0, 1000.0))  # pidfile last written at t=1000
    return marker, rs.service_sid(marker["services"]["postgres"])


def postgres(tmp_path):
    return str(tmp_path / "dependencies/postgres/bin/postgres.exe")


def stopped(marker):
    return [None, {"state": "stopped", "pid": 0}]


def proof(marker, probe, parents, validator=stopped):
    return rs.NativeStopProof(
        marker, validator=validator, probe=probe, process_parents=lambda: dict(parents)
    )


def test_stopped_service_with_vanished_pidfile_pid_is_stale(tmp_path, fake_win32job):
    marker, _ = install(tmp_path, pg_pid=4242)
    probe = Probe({})
    result = proof(marker, probe, {4: 0})
    assert result.handles == [] and result.processes == {}


def test_stopped_service_with_reused_foreign_pid_is_stale_and_untouched(tmp_path, fake_win32job):
    marker, _ = install(tmp_path, pg_pid=4242)
    # Before the fix this raised "Foreign executable/token" and blocked uninstall forever.
    for created in (2000.0, 500.0):  # newer than the pidfile, and (foreign image) older
        probe = Probe(
            {4242: dict(image=r"C:\Windows\explorer.exe", sid="S-1-5-21-9", created=created)}
        )
        result = proof(marker, probe, {4242: 4})
        assert result.handles == [] and result.processes == {}
        assert all(handle.closed for handle in probe.opened)


def test_foreign_child_of_stale_pid_is_ignored_but_owned_orphan_is_captured(
    tmp_path, fake_win32job
):
    marker, sid = install(tmp_path, pg_pid=4242)
    probe = Probe(
        {
            # Historical PPID 4242 also names this unrelated process.
            7000: dict(image=r"C:\Tools\foreign.exe", sid="S-1-5-21-9", created=3000.0),
            # Genuine orphaned backend of the crashed postmaster: still owned, still waited for.
            7001: dict(image=postgres(tmp_path), sid=sid, created=900.0),
        }
    )
    result = proof(marker, probe, {7000: 4242, 7001: 4242})
    assert set(result.processes) == {7001}
    assert [h.pid for h in result.handles] == [7001]
    assert next(h for h in probe.opened if h.pid == 7000).closed


def test_stopped_service_with_live_owned_postmaster_still_blocks(tmp_path, fake_win32job):
    marker, sid = install(tmp_path, pg_pid=4242)
    probe = Probe({4242: dict(image=postgres(tmp_path).upper(), sid=sid, created=900.0)})
    result = proof(marker, probe, {4242: 4})
    assert set(result.processes) == {4242}  # prove_exit() will wait on it as before
    assert not result.handles[0].closed


def test_owned_image_with_foreign_token_is_stale_only_when_newer_than_pidfile(
    tmp_path, fake_win32job
):
    marker, _ = install(tmp_path, pg_pid=4242)
    newer = Probe({4242: dict(image=postgres(tmp_path), sid="S-1-5-21-9", created=2000.0)})
    assert proof(marker, newer, {4242: 4}).processes == {}
    older = Probe({4242: dict(image=postgres(tmp_path), sid="S-1-5-21-9", created=900.0)})
    with pytest.raises(ValueError, match="Foreign executable/token"):
        proof(marker, older, {4242: 4})


def test_access_denied_only_tolerated_for_stale_hints(tmp_path, fake_win32job):
    marker, sid = install(tmp_path, pg_pid=4242)
    assert proof(marker, Probe({}, denied={4242}), {4242: 4}).processes == {}

    def running(marker):
        fake_win32job.pg_job_exists = True  # a running PG service owns its job
        return [None, {"state": "running", "pid": 100}]

    host = str(tmp_path / "dependencies/service/pythonservice.exe")
    probe = Probe({100: dict(image=host, sid=sid, created=10.0)}, denied={4242})
    with pytest.raises(OSError):
        proof(marker, probe, {100: 4, 4242: 100}, validator=running)


def test_running_service_keeps_strict_pidfile_and_rejects_new_foreign_child(
    tmp_path, fake_win32job
):
    marker, sid = install(tmp_path, pg_pid=4242)
    host = str(tmp_path / "dependencies/service/pythonservice.exe")

    def running(marker):
        fake_win32job.pg_job_exists = True  # a running PG service owns its job
        return [None, {"state": "running", "pid": 100}]

    probe = Probe(
        {
            100: dict(image=host, sid=sid, created=10.0),
            4242: dict(image=r"C:\Tools\foreign.exe", sid="S-1-5-21-9", created=2000.0),
        }
    )
    with pytest.raises(ValueError, match="Foreign executable/token"):
        proof(marker, probe, {100: 4, 4242: 4}, validator=running)
    assert all(handle.closed for handle in probe.opened)

    probe = Probe(
        {
            100: dict(image=host, sid=sid, created=10.0),
            4242: dict(image=postgres(tmp_path), sid=sid, created=20.0),
            # Created after its authenticated parent: a real foreign child stays fail-closed.
            5000: dict(image=r"C:\Tools\foreign.exe", sid="S-1-5-21-9", created=30.0),
        }
    )
    with pytest.raises(ValueError, match="stop-proof rejected"):
        proof(marker, probe, {100: 4, 4242: 100, 5000: 4242}, validator=running)


def test_process_older_than_authenticated_parent_is_historical_ppid(tmp_path, fake_win32job):
    marker, sid = install(tmp_path, pg_pid=4242)
    host = str(tmp_path / "dependencies/service/pythonservice.exe")

    def running(marker):
        fake_win32job.pg_job_exists = True  # a running PG service owns its job
        return [None, {"state": "running", "pid": 100}]

    probe = Probe(
        {
            100: dict(image=host, sid=sid, created=10.0),
            4242: dict(image=postgres(tmp_path), sid=sid, created=20.0),
            # Predates postmaster 4242: its PPID refers to an older, exited process.
            5000: dict(image=r"C:\Tools\foreign.exe", sid="S-1-5-21-9", created=5.0),
            5001: dict(image=r"C:\Tools\foreign-child.exe", sid="S-1-5-21-9", created=6.0),
        }
    )
    result = proof(marker, probe, {100: 4, 4242: 100, 5000: 4242, 5001: 5000}, validator=running)
    assert set(result.processes) == {100, 4242}


def test_windows_stale_pidfile_with_live_foreign_processes_is_not_touched(tmp_path):
    """Native process probes: a reused PID and its historical child are left running."""
    if os.name != "nt":
        pytest.skip("Native process probes require Windows")
    pytest.importorskip("win32job")
    import subprocess
    import time

    import psutil

    marker, _ = install(tmp_path, pg_pid=0)
    pidfile = tmp_path / "postgres-data/postmaster.pid"
    time.sleep(0.05)
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    child_pid = None
    parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        child_pid = int(parent.stdout.readline())
        # Pidfile written before the foreign process was created: crash leftover.
        pidfile.write_text(f"{parent.pid}\n{pidfile.parent}\n1700000000\n55471\n")
        os.utime(pidfile, (time.time() - 3600, time.time() - 3600))
        result = rs.NativeStopProof(marker, validator=stopped)
        try:
            assert result.handles == [] and result.processes == {}
            result.prove_exit()
        finally:
            result.close()
        assert parent.poll() is None
        assert psutil.Process(child_pid).is_running()  # never os.kill: it terminates on Windows
    finally:
        parent.kill()
        parent.wait()
        if child_pid is not None:
            try:
                psutil.Process(child_pid).kill()
            except psutil.NoSuchProcess:
                pass
