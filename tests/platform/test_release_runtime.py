"""Safety and truthful evidence boundaries of the disposable runtime runner."""

import pytest

from scripts.release_runtime_acceptance import compare_results, validate_port, validate_runtime_dsn


@pytest.mark.parametrize("port", [5432, 55819, 55828, 55829, 65536, True, "55820"])
def test_runner_refuses_nonreserved_database_ports(port):
    with pytest.raises(ValueError):
        validate_port(port)


def test_runner_accepts_only_reserved_disposable_database_ports():
    assert validate_port(55820) == 55820
    assert validate_port(55827) == 55827


def test_performance_comparison_requires_identical_verified_workloads():
    baseline = {
        "workload_sha256": "a",
        "rows": 100,
        "jobs": 10,
        "metrics": {"load_seconds": 2.0},
        "status": "PASS",
    }
    candidate = {**baseline, "metrics": {"load_seconds": 3.0}}
    assert compare_results(baseline, candidate)["ratios"]["load_seconds"] == 1.5
    with pytest.raises(ValueError):
        compare_results(baseline, {**candidate, "rows": 101})
    with pytest.raises(ValueError):
        compare_results(baseline, {**candidate, "status": "FAIL"})


@pytest.mark.parametrize(
    "dsn",
    [
        "host=127.0.0.1 port=5432 dbname=tester_acceptance user=tester_release",
        "host=foreign port=55820 dbname=tester_acceptance user=tester_release",
        "host=127.0.0.1 hostaddr=192.0.2.1 port=55820 dbname=tester_acceptance user=tester_release",
        "host=127.0.0.1 port=55820 dbname=postgres user=tester_release",
    ],
)
def test_direct_workload_child_refuses_foreign_or_default_database(dsn):
    with pytest.raises(ValueError):
        validate_runtime_dsn(dsn)
