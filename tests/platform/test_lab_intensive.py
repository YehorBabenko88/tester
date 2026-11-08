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


def simulated_run(monkeypatch, tmp_path, *, failed=0):
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
        return dict(queue=0, running=1, failed=failed if calls[0] > 1 else 0)

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
