"""Strict PG16/18 DATA -> API -> Chromium, rollback and concurrency evidence.

No hosted/production DB or live exchange calls. Preserves separate FastAPI
Mock admission and actual independent PostgreSQL backend overlap checks.
"""
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg
from ci_named_junit_gate import (
    CANONICAL_DATA_RESEARCH_NODES,
    assert_ci_source,
    begin_repetition_attempt,
    complete_repetition_manifest,
    initialize_repetition_manifest,
    reset_artifact,
    safe_artifact,
)
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"16", "18"}
MANDATORY = CANONICAL_DATA_RESEARCH_NODES


def main():
    head_sha = assert_ci_source(os.environ.get("TESTER_EXPECT_HEAD_SHA"))
    expected = os.getenv("TESTER_EXPECT_PG_MAJOR")
    if expected not in EXPECTED:
        raise SystemExit("Expected PostgreSQL major must be the explicit 16/18 matrix value")
    admin_dsn = os.getenv("TESTER_AUX_TEST_DSN", "")
    info = conninfo_to_dict(admin_dsn)
    if (info.get("host") != "127.0.0.1"
            or info.get("port") != "55479"
            or info.get("dbname") != "tester_aux_ci"
            or info.get("user") != "tester_aux_ci"):
        raise SystemExit("Refusing non-disposable PostgreSQL service")
    with psycopg.connect(admin_dsn) as conn:
        actual = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
    if actual != int(expected):
        raise SystemExit(f"Actual PostgreSQL major mismatch: {actual} != {expected}")
    env = dict(os.environ)
    env["TESTER_AUX_TEST_DSN"] = admin_dsn.rsplit("/", 1)[0] + "/tester_aux_roundtrip"
    env["TESTER_RESEARCH_DATA_TEST_DSN"] = admin_dsn.rsplit("/", 1)[0] + "/tester_research_data_disposable"
    env["TESTER_RESEARCH_BROWSER_E2E"] = "1"
    evidence = ROOT / ".scratch" / "mandatory-evidence" / f"pg{expected}"
    evidence.mkdir(parents=True, exist_ok=True)
    aggregate = evidence / f"pg{expected}-two-repetitions.json"
    # A failed first attempt must never upload a stale second attempt receipt.
    for ordinal in (1, 2):
        reset_artifact(evidence / f"pg{expected}-mandatory-{ordinal}.xml")
    initialize_repetition_manifest(
        aggregate, head_sha=head_sha, postgres_major=str(actual),
        expected_repetitions=2,
    )
    artifacts = []
    # Broad CI suites already populated these dedicated DBs: repeat EXACTLY
    # the five finite mandatory tests twice against the SAME DB identities.
    # An unrepeatable database test is a release failure, not a false PASS.
    for repetition in (1, 2):
        with tempfile.TemporaryDirectory(prefix="tester-pg-real-junit-") as td:
            raw = Path(td) / "raw.xml"
            command = [
                sys.executable, "-m", "pytest", "-vv", "-rs",
                "--strict-markers", "-o", "xfail_strict=true", "--timeout=60",
                "-p", "no:cacheprovider", f"--junitxml={raw}", *MANDATORY,
            ]
            # Reserve a random per-execution identity before launching the process.
            attempt_id = secrets.token_hex(16)
            begin_repetition_attempt(
                aggregate, head_sha=head_sha, postgres_major=str(actual),
                repeat_ordinal=repetition, attempt_id=attempt_id,
            )
            artifact = evidence / f"pg{expected}-mandatory-{repetition}.xml"
            reset_artifact(artifact)
            timed_out = False
            result = None
            try:
                result = subprocess.run(command, cwd=ROOT, env=env,
                                        capture_output=True, text=True, timeout=500)
            except subprocess.TimeoutExpired:
                timed_out = True
            for line in (result.stdout if result else "").splitlines():
                if ("::test_" in line and any(t in line for t in
                        (" PASSED", " FAILED", " SKIPPED", " XFAIL", " XPASS"))):
                    print(line[:250], flush=True)
            try:
                safe_artifact(
                    raw, artifact,
                    MANDATORY, head_sha=head_sha, postgres_major=str(actual),
                    suite_name=f"data-research-pg{expected}-repeat{repetition}",
                    process_returncode=(result.returncode if result else None),
                    timed_out=timed_out, attempt_id=attempt_id,
                    repeat_ordinal=repetition,
                )
            except ValueError as exc:
                raise SystemExit(f"Mandatory PG{expected} JUnit rejected: {exc}") from None
            if result is None or result.returncode != 0:
                raise SystemExit(f"Mandatory PG{expected} pytest process NOT_VERIFIED")
            artifacts.append(artifact)
            print(f"PASS: PG{actual}, exact SHA {head_sha}, 5/5 named cases "
                  f"(repeat {repetition}/2, PG concurrent backend and HTTP slots independent)",
                  flush=True)
    complete_repetition_manifest(
        aggregate, artifacts, head_sha=head_sha, postgres_major=str(actual),
    )
    print(f"PASS: exactly two PG{expected} five-case repetitions, SHA={head_sha}", flush=True)


if __name__ == "__main__":
    main()
