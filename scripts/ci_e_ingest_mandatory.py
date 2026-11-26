"""Six mandatory E REST negative streaming/deadline tests on each PG CI matrix job."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ci_named_junit_gate import assert_ci_source, safe_artifact, reset_artifact

ROOT = Path(__file__).resolve().parents[1]
FILE = "tests/ingest/test_pipeline.py::PipelineTests::"
TEST_NAMES = (
    "test_canonical_rest_slow_trickle_stops_on_deadline_and_closes",
    "test_canonical_rest_rejects_compressed_body_before_iteration",
    "test_canonical_rest_expired_deadline_refuses_before_network",
    "test_canonical_rest_socket_timeouts_follow_remaining_total_budget",
    "test_canonical_rest_deadline_interrupts_durable_policy_wait",
    "test_canonical_rest_deadline_during_stream_closes_response",
)


def main():
    head_sha = assert_ci_source(os.environ.get("TESTER_EXPECT_HEAD_SHA"))
    major = os.getenv("TESTER_EXPECT_PG_MAJOR")
    if major not in ("16", "18"):
        raise SystemExit("Expected PG matrix major 16 or 18 not set")
    admin_dsn = os.getenv("TESTER_AUX_TEST_DSN", "")
    parts = conninfo_to_dict(admin_dsn)
    if (parts.get("host") != "127.0.0.1" or parts.get("port") != "55479"
            or parts.get("dbname") != "tester_aux_ci"):
        raise SystemExit("Refusing non-disposable CI PostgreSQL service")
    with psycopg.connect(admin_dsn) as conn:
        actual = str(int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000)
    if actual != major:
        raise SystemExit("Actual PostgreSQL major differs from E acceptance job")
    evidence = ROOT / ".scratch" / "mandatory-evidence" / f"pg{major}"
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tester-e-six-junit-") as td:
        raw = Path(td) / "raw.xml"
        cmd = [
            sys.executable, "-m", "pytest", "-vv", "-rs",
            "--strict-markers", "-o", "xfail_strict=true", "--timeout=40",
            "-p", "no:cacheprovider", f"--junitxml={raw}",
            *(FILE + name for name in TEST_NAMES),
        ]
        artifact = evidence / f"pg{major}-e-rest-six.xml"
        reset_artifact(artifact)
        proc = None
        timed_out = False
        try:
            proc = subprocess.run(cmd, cwd=ROOT, env=dict(os.environ), text=True,
                                  capture_output=True, timeout=340)
        except subprocess.TimeoutExpired:
            timed_out = True
        for line in (proc.stdout if proc else "").splitlines():
            if "::test_" in line and any(t in line for t in
                    (" PASSED", " FAILED", " SKIPPED", " XPASS", " XFAIL")):
                print(line[:250], flush=True)
        try:
            safe_artifact(
                raw, artifact,
                tuple(FILE + name for name in TEST_NAMES),
                head_sha=head_sha, postgres_major=major,
                suite_name=f"e-rest-six-pg{major}",
                process_returncode=(proc.returncode if proc else None),
                timed_out=timed_out,
            )
        except ValueError as exc:
            raise SystemExit(f"Six mandatory E JUnit cases rejected: {exc}") from None
        if proc is None or proc.returncode != 0:
            raise SystemExit("Mandatory E pytest process NOT_VERIFIED")
        print(f"PASS: six E offline REST regression cases on PG{major} "
              f"matrix job, exact checkout {head_sha}", flush=True)


if __name__ == "__main__":
    main()
