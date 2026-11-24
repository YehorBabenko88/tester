import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location("lab_soak", Path("scripts/lab_soak.py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_soak_arguments_require_explicit_identity_and_bound_admission(tmp_path):
    m = module()
    args = m.arguments(["--expected-sha", "a" * 40, "--out", str(tmp_path / "report.json")])
    assert (args.seconds, args.sample_seconds, args.cycle_seconds) == (1800, 10, 60)
    for extra in [
        ["--cycle-seconds", "1"],
        ["--seconds", "999999"],
        ["--sample-seconds", "0"],
        ["--expected-sha", "bad"],
    ]:
        with pytest.raises(SystemExit):
            m.arguments(
                ["--expected-sha", "a" * 40, "--out", str(tmp_path / "report.json"), *extra]
            )
    with pytest.raises(SystemExit):
        m.arguments(["--out", str(tmp_path / "report.json")])


def test_rotating_windows_are_closed_small_and_within_previous_day():
    m = module()
    now = 2000000000000
    windows = [m.window(now, i) for i in range(300)]
    for start, end in windows:
        assert end - start == 5 * 60000
        assert now - 86400000 <= start < end <= now - 60000
        assert start % 60000 == end % 60000 == 0
    assert len(set(windows)) > 100
    assert len(m.SYMBOLS) == len(set(m.SYMBOLS)) == 10


def test_counter_deltas_never_turn_resets_or_missing_values_into_throughput():
    m = module()
    before = dict(
        stats_reset="old", wal_stats_reset="wal", tup_inserted=100, xact_commit=20, wal_bytes=1000
    )
    after = dict(
        stats_reset="old", wal_stats_reset="wal", tup_inserted=120, xact_commit=24, wal_bytes=1400
    )
    assert m.counter_delta(before, after, 2)["tup_inserted_per_second"] == 10
    assert m.counter_delta(before, after, 2)["wal_bytes_per_second"] == 200
    after["stats_reset"] = "new"
    assert m.counter_delta(before, after, 2)["tup_inserted_delta"] is None
    after["stats_reset"] = "old"
    after["wal_bytes"] = 999
    assert m.counter_delta(before, after, 2)["wal_bytes_delta"] is None
    assert m.counter_delta(None, after, 2)["tup_inserted_delta"] is None
    assert m.counter_delta(before, after, 0)["xact_commit_per_second"] is None
    after["wal_stats_reset"] = None
    assert m.counter_delta(before, after, 2)["wal_bytes_delta"] is None
    assert (
        m.counter_delta(before | {"stats_reset": None}, after | {"stats_reset": None}, 2)[
            "tup_inserted_delta"
        ]
        is None
    )


def test_wal_reset_is_independent_from_database_reset():
    m = module()
    before = dict(
        stats_reset="db", wal_stats_reset="one", tup_inserted=1, xact_commit=1, wal_bytes=10
    )
    after = dict(
        stats_reset="db", wal_stats_reset="two", tup_inserted=2, xact_commit=2, wal_bytes=20
    )
    value = m.counter_delta(before, after, 1)
    assert value["tup_inserted_delta"] == 1
    assert value["wal_bytes_delta"] is None


def test_identity_mismatch_is_not_a_successful_soak():
    m = module()
    expected = dict(
        source_sha="a" * 40,
        tree_sha="b" * 40,
        deploy_manifest_sha256="c" * 64,
        installation_id="installation",
        instance_id="instance",
        services={"app": "owned"},
        api_endpoint="http://127.0.0.1:18766",
        postgres_port=55470,
        provenance="host_reported",
        scm_state="not_independently_verified",
    )
    m.assert_identity({"data": expected}, expected)
    for key in [
        "source_sha",
        "instance_id",
        "installation_id",
        "tree_sha",
        "deploy_manifest_sha256",
    ]:
        with pytest.raises(ValueError):
            m.assert_identity({"data": expected | {key: "different"}}, expected)


def test_atomic_report_refuses_overwrite_initial_and_preserves_valid_json(tmp_path):
    import json

    m = module()
    path = tmp_path / "report.json"
    m.atomic_report(path, dict(status="RUNNING"), initial=True)
    with pytest.raises(FileExistsError):
        m.atomic_report(path, dict(status="bad"), initial=True)
    m.atomic_report(path, dict(status="FAILED", metric=None))
    assert json.loads(path.read_text()) == dict(status="FAILED", metric=None)


@pytest.mark.parametrize("submission_failure", [False, True])
def test_simulated_soak_is_bounded_and_never_contacts_host(
    tmp_path, monkeypatch, submission_failure
):
    import uuid
    from types import SimpleNamespace

    m = module()
    root = tmp_path / "owned"
    root.mkdir()
    expected = dict(
        source_sha="a" * 40,
        tree_sha="b" * 40,
        deploy_manifest_sha256="c" * 64,
        installation_id="id",
        services={"app": "app", "postgres": "pg"},
        instance_id="instance",
        api_endpoint="http://127.0.0.1:18766",
        postgres_port=55470,
        provenance="host_reported",
        scm_state="not_independently_verified",
    )
    monkeypatch.setattr(m, "bind", lambda *a: (expected, "SECRET", 123))
    clock = [0]
    monkeypatch.setattr(m.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(m.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(m.time, "time_ns", lambda: 2000000000000000000)
    calls = []

    def http(path, body=None):
        if body:
            calls.append(body)
            if submission_failure:
                raise TimeoutError("SECRET")
            return {"data": {"id": str(uuid.uuid4())}}
        if path.endswith("identity"):
            return {"data": expected}
        if path.endswith("health"):
            return {"data": {"status": "ok", "instance_id": "instance"}}
        return {
            "data": {
                "state": "completed",
                "checkpoint": {
                    "telemetry": dict(
                        schema_version=1,
                        source="bybit",
                        coverage="UNKNOWN",
                        work_complete=True,
                        records_ingested=5,
                        records_verified=5,
                        record_hashes={str(i): "a" * 64 for i in range(5)},
                        bytes_received=100,
                        elapsed_seconds=1.0,
                        bytes_per_second=100,
                        evidence_refs=["b" * 64],
                        expected_bytes=None,
                        expected_records=None,
                    )
                },
            }
        }

    monkeypatch.setattr(m, "http", http)
    monkeypatch.setattr(
        m,
        "pg_stats",
        lambda dsn: dict(
            stats_reset="db", wal_stats_reset="wal", tup_inserted=10, xact_commit=10, wal_bytes=10
        ),
    )
    monkeypatch.setattr(
        m,
        "process_stats",
        lambda *a: dict(
            private_working_set_bytes=123, cpu_one_core_percent=1, io_bytes_per_second=2
        ),
    )
    monkeypatch.setattr(
        m, "disk_stats", lambda *a: dict(runtime_file_bytes=20, shared_volume_free_bytes=100)
    )
    args = SimpleNamespace(
        root=root,
        out=tmp_path / "report.json",
        expected_sha="a" * 40,
        seconds=185,
        sample_seconds=10,
        cycle_seconds=60,
    )
    result = m.run(args)
    assert len(calls) == 10
    assert "SECRET" not in (tmp_path / "report.json").read_text()
    if submission_failure:
        assert result["status"] == "FAILED"
    else:
        assert result["status"] == "LOW_RATE_SOAK_PASS"
        assert result["completed"] == 10
        assert result["elapsed_seconds"] >= 185
        assert result["job_record_acknowledgments_total"] == 50
        assert result["http_body_bytes_received_total"] == 1000


def test_completed_telemetry_requires_real_acknowledgment_and_keeps_unknown_totals():
    m = module()
    valid = dict(
        schema_version=1,
        source="bybit",
        coverage="UNKNOWN",
        work_complete=True,
        records_ingested=1,
        records_verified=1,
        record_hashes={"key": "a" * 64},
        bytes_received=100,
        elapsed_seconds=2,
        bytes_per_second=50,
        evidence_refs=["b" * 64],
        expected_bytes=None,
        expected_records=None,
    )
    result = m.completed_telemetry({"checkpoint": {"telemetry": valid}})
    assert result["records_verified"] == 1 and result["expected_records"] is None
    for mutation in [
        {"record_hashes": {}},
        {"work_complete": False},
        {"bytes_received": None},
        {"coverage": "COMPLETE"},
    ]:
        with pytest.raises(ValueError):
            m.completed_telemetry({"checkpoint": {"telemetry": valid | mutation}})
