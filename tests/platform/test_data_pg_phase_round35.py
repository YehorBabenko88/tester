"""Offline phase/required-PG phase regressions for the real DATA concurrency test.

These tests execute the actual pytest node in an isolated subprocess.  No DB
connection is made in negative configurations.  The real PostgreSQL positive
case remains mandatory on disposable 127.0.0.1:55479 under hosted CI.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
NODE = (
    "tests/platform/test_analytics_readback_integration_round3.py::"
    "test_round13_two_real_postgresql_readers_overlap_and_release"
)


def _invoke(*, required, dsn=None):
    env = dict(os.environ)
    env.pop("TESTER_RESEARCH_DATA_TEST_DSN", None)
    env.pop("TESTER_REQUIRE_RESEARCH_PG", None)
    if required:
        env["TESTER_REQUIRE_RESEARCH_PG"] = "1"
    if dsn is not None:
        env["TESTER_RESEARCH_DATA_TEST_DSN"] = dsn
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", NODE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


def test_offline_without_dsn_is_explicit_skip_not_success():
    result = _invoke(required=False)
    output = result.stdout + result.stderr
    assert result.returncode == 5
    assert "1 skipped" in output
    assert "PostgreSQL concurrency requires a disposable DSN in the PG phase" in output


def test_required_pg_without_dsn_must_fail():
    result = _invoke(required=True)
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "Mandatory real PostgreSQL concurrency DSN is absent" in output
    assert "1 failed" in output


@pytest.mark.parametrize(
    "dsn",
    [
        "host=127.0.0.1 port=5432 dbname=tester_research_data_disposable",
        "host=localhost port=55479 dbname=tester_research_data_disposable",
        "host=127.0.0.1 port=55479 dbname=production",
    ],
)
def test_required_pg_rejects_foreign_endpoint_without_connection(dsn):
    result = _invoke(required=True, dsn=dsn)
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "Refusing non-disposable PostgreSQL concurrency target" in output


@pytest.mark.integration
def test_required_pg_disposable_dsn_executes_real_backend_overlap():
    """Only the dedicated PG step can supply the actual disposable service."""
    dsn = os.getenv("TESTER_RESEARCH_DATA_TEST_DSN")
    if os.getenv("TESTER_REQUIRE_RESEARCH_PG") != "1" or not dsn:
        pytest.skip("genuine disposable PostgreSQL CI environment required")
    result = _invoke(required=True, dsn=dsn)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "1 passed" in output
    assert "skipped" not in output
