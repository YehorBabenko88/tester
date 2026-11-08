import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path("scripts/lab_intensive.py")
    assert path.is_file(), "published intensive harness missing"
    spec = importlib.util.spec_from_file_location("lab_intensive", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_latency_distribution_uses_real_observations_and_unknowns():
    m = module()
    values = m.latency_summary(list(range(1, 21)))
    assert values == dict(
        count=20, mean_seconds=10.5, p50_seconds=10, p95_seconds=19, max_seconds=20
    )
    assert m.latency_summary([])["p95_seconds"] is None
    with pytest.raises(ValueError):
        m.latency_summary([float("nan")])


def test_rate_slots_never_catch_up_or_exceed_ten_by_forty():
    m = module()
    gate = m.SlotGate(600)
    admitted = []
    for at in [0, 0.1, 1, 15, 15.1, 80, 80.1, *range(90, 600, 15), 600, 601]:
        slot = gate.take(at)
        if slot is not None:
            admitted.append(slot)
    assert len(admitted) == len(set(admitted))
    assert len(admitted) * 10 <= 400
    assert 2 not in admitted and 3 not in admitted and 4 not in admitted
    assert m.can_admit(19, 41, False)
    assert not m.can_admit(20, 41, False)
    assert not m.can_admit(None, 41, False)
    assert not m.can_admit(0, 29, False)


def test_stop_limits_require_sustained_samples_but_fail_queue_immediately():
    m = module()
    gate = m.StopPolicy()
    assert gate.observe(queue=10, failed=0, memory=2**31 + 1, p95=16) is None
    assert gate.observe(queue=10, failed=0, memory=None, p95=None) is None
    assert gate.observe(queue=10, failed=0, memory=2**31 + 1, p95=16) is None
    assert gate.observe(queue=10, failed=0, memory=2**31 + 1, p95=16) in (
        "MEMORY_SUSTAINED",
        "READ_LATENCY_SUSTAINED",
    )
    assert m.StopPolicy().observe(queue=41, failed=0, memory=0, p95=0) == "QUEUE_LIMIT"
    assert m.StopPolicy().observe(queue=0, failed=1, memory=0, p95=0) == "FAILED_JOB"


def test_unknown_metrics_and_pending_jobs_cannot_be_a_pass():
    m = module()
    good = dict(postgres=True, processes=True, disk=True, latency=True, worker_count=True)
    assert m.classify(None, 0, 10, good) == "INTENSIVE_OBSERVED_PASS"
    assert m.classify(None, 0, 10, {**good, "processes": False}) == "INCOMPLETE"
    assert m.classify(None, 1, 10, good) == "INCOMPLETE"
    assert m.classify("QUEUE_LIMIT", 0, 10, good) == "STOPPED_BY_LIMIT"


def test_preflight_errors_do_not_print_secrets(monkeypatch, capsys):
    m = module()
    monkeypatch.setattr(m, "arguments", lambda: None)

    def fail(_):
        raise ValueError("postgresql://user:SECRET@127.0.0.1/private")

    monkeypatch.setattr(m, "run", fail)
    assert m.main() == 1
    output = capsys.readouterr().out
    assert "PREFLIGHT_FAILED" in output
    assert "SECRET" not in output and "postgresql" not in output


def test_invalid_service_roles_are_rejected_before_cim(monkeypatch):
    m = module()
    monkeypatch.setattr(m.os, "name", "nt")
    with pytest.raises(ValueError, match="service"):
        m.process_sample(
            Path("C:/sandbox"),
            {
                "services": {
                    "app": "TesterLabPG_" + "a" * 32,
                    "postgres": "TesterLabApp_" + "a" * 32,
                }
            },
            10,
        )


def simulated_run(
    monkeypatch, tmp_path, *, failed=0, historical=0, replacement=False, failure_call=2
):
    # All network, DB and CIM boundaries are replaced; report persistence stays real.
    import types
    import uuid

    m = module()
    now = [0.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(m.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    expected = {"source_sha": "a" * 40, "instance_id": "b" * 32}
    monkeypatch.setattr(m.base, "bind", lambda *_: (expected, "SECRET_DSN", 10))
    monkeypatch.setattr(m.base, "assert_identity", lambda response, identity: None)
    monkeypatch.setattr(
        m.base, "http", lambda path: {"data": {"status": "ok", "instance_id": "b" * 32}}
    )
    calls = [0]

    def database(_):
        calls[0] += 1
        ids = [str(uuid.UUID(int=i + 1)) for i in range(historical)]
        if calls[0] >= failure_call:
            if replacement:
                ids = []
            ids += [str(uuid.UUID(int=100 + i)) for i in range(failed)]
        return dict(queue=0, running=1, failed=len(ids), failed_ids=ids)

    monkeypatch.setattr(m, "database_sample", database)
    monkeypatch.setattr(
        m.base,
        "pg_stats",
        lambda _: dict(
            tup_inserted=int(now[0]),
            xact_commit=int(now[0]),
            wal_bytes=int(now[0]),
            stats_reset="epoch",
            wal_stats_reset="epoch",
        ),
    )
    monkeypatch.setattr(
        m, "process_sample", lambda *_: dict(private_bytes=100, worker_process_count=1)
    )
    monkeypatch.setattr(m.base, "disk_stats", lambda _: dict(shared_volume_free_bytes=2**32))
    monkeypatch.setattr(m.base, "completed_telemetry", lambda _: {"records_verified": 5})

    def request(path, body=None):
        if body is not None:
            value = {"id": str(uuid.uuid4())}
        elif path.startswith("/api/downloads/"):
            value = {"state": "completed"}
        else:
            value = [{"is_closed": True, "provenance": "bybit", "kind": "official_trade"}]
        return dict(value={"data": value}, seconds=0.01, error_type=None)

    monkeypatch.setattr(m, "request", request)
    report = m.run(
        types.SimpleNamespace(
            root=tmp_path / "lab", out=tmp_path / "report.json", seconds=60, expected_sha="a" * 40
        )
    )
    return report


def test_full_synthetic_run_reports_fairness_without_claiming_parallel_workers(
    monkeypatch, tmp_path
):
    import json

    report = simulated_run(monkeypatch, tmp_path)
    assert report["status"] == "INTENSIVE_OBSERVED_PASS"
    assert report["submitted"] == report["completed"] == 30
    assert report["pending_jobs"] == 0
    assert all(x["completed"] == 3 for x in report["fairness"].values())
    assert report["peaks"]["worker_processes"] == 1
    assert report["peaks"]["active_jobs"] == 1
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["status"] == report["status"]
    assert "SECRET_DSN" not in json.dumps(saved)


def test_stop_limit_ends_collection_immediately(monkeypatch, tmp_path):
    report = simulated_run(monkeypatch, tmp_path, failed=1)
    assert report["status"] == "STOPPED_BY_LIMIT"
    assert report["submitted"] == 0
    assert len(report["samples"]) == 1


def test_missing_cim_rows_cannot_look_like_zero_resource_usage(monkeypatch):
    import types

    m = module()
    monkeypatch.setattr(
        m.subprocess,
        "run",
        lambda *args, **kwargs: types.SimpleNamespace(stdout=b'{"rows":[],"bindings":[]}'),
    )
    with pytest.raises(ValueError, match="process"):
        m.process_sample(
            Path("C:/sandbox"),
            {
                "services": {
                    "app": "TesterLabApp_" + "a" * 32,
                    "postgres": "TesterLabPG_" + "a" * 32,
                }
            },
            10,
        )


def test_historical_failed_jobs_are_preserved_and_do_not_block_probe(monkeypatch, tmp_path):
    report = simulated_run(monkeypatch, tmp_path, historical=1)
    assert report["status"] == "INTENSIVE_OBSERVED_PASS"
    assert report["failure_baseline"]["count"] == 1
    assert report["failure_baseline"]["ids"] == ["00000000-0000-0000-0000-000000000001"]
    assert report["failed"] == 0


@pytest.mark.parametrize("replacement", [False, True])
def test_new_external_failure_stops_even_when_total_failed_count_is_unchanged(
    monkeypatch, tmp_path, replacement
):
    report = simulated_run(monkeypatch, tmp_path, historical=1, failed=1, replacement=replacement)
    assert report["status"] == "STOPPED_BY_LIMIT"
    assert report["stop_reason"] == "FAILED_JOB"
    assert report["submitted"] == 0
    assert report["new_failed_ids"] == ["00000000-0000-0000-0000-000000000064"]


def test_final_snapshot_detects_late_external_failure(monkeypatch, tmp_path):
    report = simulated_run(monkeypatch, tmp_path, failed=1, failure_call=18)
    assert report["submitted"] == report["completed"] == 30
    assert report["status"] == "STOPPED_BY_LIMIT"
    assert report["stop_reason"] == "FAILED_JOB"
    assert report["new_failed_ids"] == ["00000000-0000-0000-0000-000000000064"]


@pytest.mark.parametrize(
    "scenario",
    ["valid", "missing_child_counter", "missing_creation", "wrong_image", "missing_supervisor"],
)
def test_batched_cim_template_scopes_and_fails_closed(monkeypatch, scenario):
    import json
    import subprocess

    m = module()
    names = {"app": "TesterLabApp_" + "a" * 32, "postgres": "TesterLabPG_" + "a" * 32}
    root = Path("C:/synthetic probe")
    real_run = subprocess.run
    prefix = r"""
$global:ProcessQueries=0
function Get-CimInstance {
 param([string]$ClassName, [string]$Filter, [string[]]$Property)
 switch ($ClassName) {
  'Win32_Service' {
   if ($Filter -like '*TesterLabApp_*') {
    $Image='APP_PATH';if ('SCENARIO' -eq 'wrong_image'){$Image='wrong.exe'}
    [pscustomobject]@{State='Running';StartName='NT AUTHORITY\LocalService';PathName=$Image;ProcessId=10}
   } else {
    [pscustomobject]@{State='Running';StartName='NT AUTHORITY\LocalService';PathName='PG_PATH';ProcessId=20}
   }
  }
  'Win32_Process' {
   $global:ProcessQueries++
   if ($Filter -or $global:ProcessQueries -gt 1) {throw 'Must use one batch process inventory'}
   foreach ($Pair in @(@(10,0),@(11,10),@(12,11),@(20,0),@(21,20),@(99,0))) {
    if ('SCENARIO' -eq 'missing_supervisor' -and $Pair[0] -eq 11){continue}
    $Created=[datetime]'2026-01-01T00:00:00Z'
    if ('SCENARIO' -eq 'missing_creation' -and $Pair[0] -eq 12){$Created=$null}
    $Command='SECRET unrelated';if($Pair[0] -eq 12){$Command='python -m src.api.worker'}
    [pscustomobject]@{ProcessId=$Pair[0];ParentProcessId=$Pair[1];CreationDate=$Created;ExecutablePath='synthetic.exe';CommandLine=$Command}
   }
  }
  'Win32_PerfFormattedData_PerfProc_Process' {
   if (-not $Filter -or $Filter -match 'IDProcess=99') {throw 'Unscoped counters'}
   foreach ($Id in @(10,11,12,20,21)) {
    if ($Filter -match ('(?:^| OR )IDProcess='+$Id+'(?: OR |$)')) {
     if ('SCENARIO' -eq 'missing_child_counter' -and $Id -eq 12){continue}
     [pscustomobject]@{IDProcess=$Id;WorkingSetPrivate=100;PercentProcessorTime=2;IOWriteBytesPersec=3}
    }
   }
  }
  default {throw 'Unexpected class'}
 }
}
"""
    image = subprocess.list2cmdline(
        [
            r"C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe",
            "runservice",
            "-N",
            names["postgres"],
            "-D",
            str(root / "postgres-data"),
            "-w",
        ]
    )
    prefix = (
        prefix.replace("APP_PATH", str(root / "dependencies/service/pythonservice.exe"))
        .replace("PG_PATH", image)
        .replace("SCENARIO", scenario)
    )

    def synthetic_subprocess(command, **kwargs):
        return real_run([*command[:-1], prefix + "\n" + command[-1]], **kwargs)

    monkeypatch.setattr(m.subprocess, "run", synthetic_subprocess)
    if scenario != "valid":
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            m.process_sample(root, {"services": names}, 11)
    else:
        result = m.process_sample(root, {"services": names}, 11)
        assert result["private_bytes"] == 500
        assert result["worker_process_count"] == 1
        assert result["groups"]["app"]["process_count"] == 3
        assert {x["pid"] for x in result["bindings"]} == {10, 11, 12, 20, 21}
        assert "SECRET" not in json.dumps(result)


def test_resources_only_cli_never_starts_load_or_database_probe(monkeypatch, tmp_path, capsys):
    import json
    import types

    m = module()
    args = types.SimpleNamespace(
        resources_only=True,
        root=tmp_path / "lab",
        out=tmp_path / "resources.json",
        expected_sha="a" * 40,
    )
    monkeypatch.setattr(m, "arguments", lambda: args)
    monkeypatch.setattr(m.base, "bind", lambda *_: ({"source_sha": "a" * 40}, "SECRET", 10))
    monkeypatch.setattr(m.base, "assert_identity", lambda *_: None)
    monkeypatch.setattr(m.base, "http", lambda _: {"data": {}})

    def forbidden(*_):
        raise AssertionError("load or DB probe forbidden")

    monkeypatch.setattr(m, "run", forbidden)
    monkeypatch.setattr(m, "database_sample", forbidden)
    monkeypatch.setattr(m, "request", forbidden)
    monkeypatch.setattr(
        m, "process_sample", lambda *_: {"private_bytes": 123, "worker_process_count": 1}
    )
    assert m.main() == 0
    report = json.loads(args.out.read_text())
    assert report["kind"] == "RESOURCE_OBSERVATION_ONLY"
    assert report["processes"]["private_bytes"] == 123
    assert "SECRET" not in json.dumps(report) + capsys.readouterr().out
