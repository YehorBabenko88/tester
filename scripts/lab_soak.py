"""Explicit low-rate lab soak. Never installs, elevates, or contacts PG5432.

Run only a published copy after installing the expected source SHA. Database
statistics and Windows counters are read-only; HTTP creates bounded public jobs.
Unavailable metrics remain null and prevent a fully observed PASS.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path

SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "LTCUSDT",
)
IDENTITY_KEYS = (
    "source_sha",
    "tree_sha",
    "deploy_manifest_sha256",
    "installation_id",
    "services",
    "instance_id",
    "api_endpoint",
    "postgres_port",
)


def plain(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Reparse path refused")
    return path


def read_json(path, limit=1024 * 1024):
    with plain(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Metadata size limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Metadata object required")
    return value, raw


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("C:/TesterLab"))
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--sample-seconds", type=int, default=10)
    parser.add_argument("--cycle-seconds", type=int, default=60)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if (
        not re.fullmatch("[0-9a-f]{40}", args.expected_sha)
        or not 1 <= args.seconds <= 7200
        or not 1 <= args.sample_seconds <= 60
        or not 60 <= args.cycle_seconds <= 3600
        or not args.out.is_absolute()
    ):
        parser.error(
            "Require exact SHA, absolute output, duration 1..7200s, sample 1..60s, cycle 60..3600s"
        )
    return args


def window(now_ms, cycle):
    # Never include the current or most recently closed minute; rotate 5-minute
    # history within one day without repeatedly fetching a full day.
    latest = now_ms // 60000 * 60000 - 60000
    end = latest - (cycle % 286) * 5 * 60000
    return end - 5 * 60000, end


def counter_delta(before, after, elapsed):
    result = {}
    for name in ("tup_inserted", "xact_commit", "wal_bytes"):
        reset = "wal_stats_reset" if name == "wal_bytes" else "stats_reset"
        valid = (
            isinstance(before, dict)
            and isinstance(after, dict)
            and elapsed > 0
            and before.get(reset) is not None
            and before.get(reset) == after.get(reset)
            and type(before.get(name)) in (int, float)
            and type(after.get(name)) in (int, float)
            and after[name] >= before[name]
        )
        delta = after[name] - before[name] if valid else None
        result[name + "_delta"] = delta
        result[name + "_per_second"] = delta / elapsed if delta is not None else None
    return result


def atomic_report(path, value, initial=False):
    path = plain(path)
    descriptor, name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".pending", dir=plain(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        plain(path)
        if initial:
            os.link(name, path)  # Atomic create-if-absent; never replace a prior report.
        else:
            os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def http(path, body=None):
    # HTTPConnection deliberately bypasses proxy environment variables.
    connection = HTTPConnection("127.0.0.1", 18766, timeout=5)
    try:
        connection.request(
            "GET" if body is None else "POST",
            path,
            body=None if body is None else json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read(1024 * 1024 + 1)
        if response.status != 200 or len(raw) > 1024 * 1024:
            if response.status != 202 or body is None or len(raw) > 1024 * 1024:
                raise ValueError("Local HTTP request failed")
        return json.loads(raw)
    finally:
        connection.close()


def assert_identity(response, expected):
    data = response["data"]
    if (
        any(data.get(key) != expected.get(key) for key in IDENTITY_KEYS)
        or data.get("provenance") != "host_reported"
        or data.get("scm_state") != "not_independently_verified"
    ):
        raise ValueError("Installed source or service instance changed")
    return data


def bind(root, sha):
    root = plain(root)
    marker, _ = read_json(root / ".tester-lab.json")
    payload, raw = read_json(root / "state/deploy-payload.json")
    report, _ = read_json(root / "runtime/service.json", 65536)
    ident = str(uuid.UUID(marker["installation_id"]))
    services = dict(
        app="TesterLabApp_" + uuid.UUID(ident).hex, postgres="TesterLabPG_" + uuid.UUID(ident).hex
    )
    if (
        marker["schema"] != "tester-lab/v1"
        or marker["root"] != str(root)
        or marker["repository"] != "YehorBabenko88/tester"
        or marker["active_sha"] != sha
        or marker["installation_id"] != ident
        or marker["services"] != services
        or marker["ports"] != dict(api=18766, postgres=55470)
        or payload["schema_version"] != "tester-lab-payload/v1"
        or payload["source_sha"] != sha
        or payload["files"] != marker["releases"][sha]
        or not re.fullmatch("[0-9a-f]{40}", payload["tree_sha"])
        or report["source_sha"] != sha
        or report["installation_id"] != ident
        or report["service"] != services["app"]
        or not re.fullmatch("[0-9a-f]{32}", report["instance_id"])
    ):
        raise ValueError("Installed metadata binding failed")
    expected = dict(
        source_sha=sha,
        tree_sha=payload["tree_sha"],
        deploy_manifest_sha256=hashlib.sha256(raw).hexdigest(),
        installation_id=ident,
        services=services,
        instance_id=report["instance_id"],
        api_endpoint="http://127.0.0.1:18766",
        postgres_port=55470,
    )
    assert_identity(http("/api/lab/identity"), expected)
    config, _ = read_json(root / "config.json", 65536)
    from psycopg.conninfo import conninfo_to_dict

    dsn = config["database_url"]
    pg = conninfo_to_dict(dsn)
    if (
        pg.get("host") != "127.0.0.1"
        or pg.get("port") != "55470"
        or pg.get("dbname") != "tester_lab"
        or pg.get("hostaddr")
        or pg.get("service")
        or config.get("host") != "127.0.0.1"
        or config.get("port") != 18766
        or plain(config["data_dir"]) != root / "runtime"
    ):
        raise ValueError("Dedicated endpoint configuration required")
    pid = report["supervisor_pid"]
    if type(pid) is not int or pid <= 0:
        raise ValueError("Missing supervisor PID")
    return expected, dsn, pid


def pg_stats(dsn):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(
        dsn, connect_timeout=3, options="-c statement_timeout=2000", row_factory=dict_row
    ) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        row = connection.execute("""SELECT current_database() AS database, inet_server_port() AS port,
            coalesce(d.stats_reset,pg_postmaster_start_time())::text AS stats_reset,
            CASE WHEN d.stats_reset IS NULL THEN 'postmaster_start' ELSE 'database_stats_reset' END AS stats_epoch_origin,
            d.tup_inserted, d.xact_commit,
            coalesce(w.stats_reset,pg_postmaster_start_time())::text AS wal_stats_reset,
            CASE WHEN w.stats_reset IS NULL THEN 'postmaster_start' ELSE 'wal_stats_reset' END AS wal_epoch_origin,
            w.wal_bytes::text
            FROM pg_stat_database d CROSS JOIN pg_stat_wal w WHERE d.datname=current_database()""").fetchone()
        if not row or row.pop("database") != "tester_lab" or row.pop("port") != 55470:
            raise ValueError("Unexpected statistics endpoint")
        row["wal_bytes"] = int(row["wal_bytes"]) if row["wal_bytes"] is not None else None
        return row


def completed_telemetry(job):
    value = job["checkpoint"]["telemetry"]
    records = value["record_hashes"]
    if (
        value["schema_version"] != 1
        or value["source"] != "bybit"
        or value["coverage"] != "UNKNOWN"
        or value["work_complete"] is not True
        or type(value["bytes_received"]) is not int
        or value["bytes_received"] <= 0
        or type(value["records_verified"]) is not int
        or value["records_verified"] <= 0
        or value["records_ingested"] != value["records_verified"]
        or not isinstance(records, dict)
        or len(records) != value["records_verified"]
        or any(
            not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)
            for digest in records.values()
        )
        or not isinstance(value["elapsed_seconds"], (int, float))
        or value["elapsed_seconds"] <= 0
        or not value["evidence_refs"]
        or any(not re.fullmatch("[0-9a-f]{64}", item) for item in value["evidence_refs"])
        or value["expected_bytes"] is not None
        or value["expected_records"] is not None
    ):
        raise ValueError("Completed job lacks measured acknowledgment")
    return {
        key: value[key]
        for key in (
            "records_ingested",
            "records_verified",
            "bytes_received",
            "elapsed_seconds",
            "bytes_per_second",
            "evidence_refs",
            "expected_bytes",
            "expected_records",
            "coverage",
        )
    }


def process_stats(supervisor_pid, services, root):
    if os.name != "nt":
        raise ValueError("Windows process counters unavailable")
    pg_service = services["postgres"]
    if not re.fullmatch("TesterLabPG_[0-9a-f]{32}", pg_service):
        raise ValueError("Unexpected service name")
    image = subprocess.list2cmdline(
        [
            "C:\\Program Files\\PostgreSQL\\18\\bin\\pg_ctl.exe",
            "runservice",
            "-N",
            pg_service,
            "-D",
            str(root / "postgres-data"),
            "-w",
        ]
    )
    # Query only the API seed and owned PG service descendants, never unrelated counters.
    script = (
        r"""
$ErrorActionPreference='Stop'
$Pg=Get-CimInstance Win32_Service -Filter "Name='PGSERVICE'"
if ($Pg.State -ne 'Running' -or $Pg.ProcessId -le 0 -or $Pg.StartName -ne 'NT AUTHORITY\LocalService' -or -not [string]::Equals($Pg.PathName,'PGIMAGE',[StringComparison]::OrdinalIgnoreCase)) {throw 'Owned PG service mismatch'}
$Queue=[Collections.Generic.Queue[int]]::new(); $Queue.Enqueue(SEED); $Queue.Enqueue([int]$Pg.ProcessId)
$Ids=[Collections.Generic.HashSet[int]]::new()
while ($Queue.Count -gt 0) {
 $Id=$Queue.Dequeue()
 if (-not $Ids.Add($Id)) {continue}
 if ($Ids.Count -gt 64) {throw 'Process tree limit'}
 foreach ($Child in @(Get-CimInstance Win32_Process -Filter ('ParentProcessId='+$Id))) {$Queue.Enqueue([int]$Child.ProcessId)}
}
$Filter=(@($Ids | ForEach-Object {'IDProcess='+$_}) -join ' OR ')
$Rows=@(Get-CimInstance Win32_PerfFormattedData_PerfProc_Process -Filter $Filter | Select-Object IDProcess,WorkingSetPrivate,PercentProcessorTime,IODataBytesPersec)
if (-not @($Rows | Where-Object {$_.IDProcess -eq SEED}).Count) {throw 'Supervisor counters missing'}
$Rows | ConvertTo-Json -Compress
""".replace("SEED", str(supervisor_pid))
        .replace("PGSERVICE", pg_service)
        .replace("PGIMAGE", image.replace("'", "''"))
    )
    value = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        timeout=10,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if len(value.stdout) > 65536:
        raise ValueError("Counter response limit")
    rows = json.loads(value.stdout)
    rows = rows if isinstance(rows, list) else [rows]
    return dict(
        processes=len(rows),
        private_working_set_bytes=sum(int(row["WorkingSetPrivate"]) for row in rows),
        cpu_one_core_percent=sum(float(row["PercentProcessorTime"]) for row in rows),
        io_bytes_per_second=sum(float(row["IODataBytesPersec"]) for row in rows),
    )


def disk_stats(root):
    start, size, count = time.monotonic(), 0, 0
    for path in plain(root / "runtime").rglob("*"):
        plain(path)
        count += 1
        if count > 100000 or time.monotonic() - start > 2:
            raise ValueError("Runtime inventory budget")
        if path.is_file():
            size += path.stat().st_size
    return dict(runtime_file_bytes=size, shared_volume_free_bytes=shutil.disk_usage(root).free)


def measured(call):
    try:
        return call(), None
    except Exception as exc:
        # Exception text may contain a DSN, SQL details or local machine paths.
        return None, type(exc).__name__


def run(args):
    root, out = plain(args.root), plain(args.out)
    if out == root or root in out.parents or not out.parent.is_dir():
        raise ValueError("Output must be outside the lab in an existing directory")
    expected, dsn, supervisor_pid = bind(root, args.expected_sha)
    report = dict(
        kind="LOW_RATE_SOAK",
        status="RUNNING",
        identity=expected,
        requested_seconds=args.seconds,
        sample_seconds=args.sample_seconds,
        cycle_seconds=args.cycle_seconds,
        records_per_symbol_window=5,
        load_scope="ten public linear symbols, at most ten jobs per cycle",
        metrics_scope="own supervisor and UUID PG service descendants; configured lab DB; dedicated cluster WAL; shared-volume free space",
        started_at_ms=time.time_ns() // 1000000,
        samples=[],
        jobs=[],
        errors=[],
        peaks=dict(
            private_working_set_bytes=None,
            cpu_one_core_percent=None,
            io_bytes_per_second=None,
            runtime_file_bytes=None,
        ),
        telemetry_scope="job-unique verified acknowledgments, not newly inserted raw rows; measured HTTP body bytes including metadata and retries",
        job_record_acknowledgments_total=0,
        http_body_bytes_received_total=0,
        submitted=0,
        completed=0,
        failed=0,
    )
    atomic_report(out, report, initial=True)
    started = time.monotonic()
    deadline, next_cycle, cycle = started + args.seconds, started, 0
    pending, previous, previous_at = {}, None, None
    try:
        while time.monotonic() < deadline:
            sample_at = time.monotonic()
            assert_identity(http("/api/lab/identity"), expected)
            health = http("/api/health")["data"]
            if health.get("status") != "ok" or health.get("instance_id") != expected["instance_id"]:
                raise ValueError("Health or service instance changed")
            for ident, job in list(pending.items()):
                actual = http("/api/downloads/" + ident)["data"]
                state = actual["state"]
                if state in ("completed", "failed"):
                    telemetry = completed_telemetry(actual) if state == "completed" else None
                    report[state] += 1
                    report["jobs"].append(
                        dict(
                            id=ident,
                            symbol=job["symbol"],
                            state=state,
                            telemetry=telemetry,
                            elapsed_seconds=time.monotonic() - job["at"],
                        )
                    )
                    if telemetry is not None:
                        report["job_record_acknowledgments_total"] += telemetry["records_verified"]
                        report["http_body_bytes_received_total"] += telemetry["bytes_received"]
                    del pending[ident]
                    if state == "failed":
                        raise ValueError("A bounded acquisition job failed")
                elif time.monotonic() - job["at"] > 180:
                    raise ValueError("Bounded job deadline exceeded")
            if (
                not pending
                and sample_at >= next_cycle
                and deadline - sample_at >= min(180, args.seconds)
            ):
                a, b = window(time.time_ns() // 1000000, cycle)
                bodies = [
                    dict(
                        category="linear",
                        symbol=symbol,
                        source="bybit",
                        kind="candles",
                        interval="1",
                        start_ms=a,
                        end_ms=b,
                    )
                    for symbol in SYMBOLS
                ]
                with ThreadPoolExecutor(max_workers=10) as pool:
                    outcomes = list(
                        pool.map(
                            lambda body: measured(lambda: http("/api/downloads", body)), bodies
                        )
                    )
                for body, (value, error) in zip(bodies, outcomes):
                    if error:
                        report["errors"].append(
                            dict(kind="submission", symbol=body["symbol"], error_type=error)
                        )
                        continue
                    ident = str(uuid.UUID(value["data"]["id"]))
                    if ident in pending:
                        raise ValueError("Duplicate admission identity")
                    pending[ident] = dict(symbol=body["symbol"], at=time.monotonic())
                    report["submitted"] += 1
                if report["errors"]:
                    raise ValueError("Ambiguous admission; no further jobs submitted")
                cycle += 1
                next_cycle = time.monotonic() + args.cycle_seconds
            pg, pg_error = measured(lambda: pg_stats(dsn))
            resources, process_error = measured(
                lambda: process_stats(supervisor_pid, expected["services"], root)
            )
            disk, disk_error = measured(lambda: disk_stats(root))
            captured_at = time.monotonic()
            sample = dict(
                elapsed_seconds=captured_at - started,
                postgres=pg,
                processes=resources,
                collection_seconds=captured_at - sample_at,
                disk=disk,
                errors=dict(postgres=pg_error, processes=process_error, disk=disk_error),
                database_activity=counter_delta(
                    previous, pg, captured_at - previous_at if previous_at is not None else 0
                ),
                pending_jobs=len(pending),
            )
            report["samples"].append(sample)
            previous, previous_at = pg, captured_at
            for key in report["peaks"]:
                value = (resources or {}).get(key, (disk or {}).get(key))
                if value is not None:
                    report["peaks"][key] = max(report["peaks"][key] or 0, value)
            report["elapsed_seconds"] = captured_at - started
            atomic_report(out, report)
            time.sleep(
                max(
                    0,
                    min(
                        args.sample_seconds - (time.monotonic() - sample_at),
                        deadline - time.monotonic(),
                    ),
                )
            )
        # No late admission and no hidden extension of the requested duration.
        assert_identity(http("/api/lab/identity"), expected)
        report["pending_jobs"] = len(pending)
        unavailable = any(any(sample["errors"].values()) for sample in report["samples"])
        unavailable |= any(
            any(value is None for value in sample["database_activity"].values())
            for sample in report["samples"][1:]
        )
        report["completion_checks"] = dict(
            duration_reached=time.monotonic() >= deadline,
            no_pending_jobs=not pending,
            metrics_complete=not unavailable,
            completed_any_jobs=report["completed"] > 0,
            no_failed_jobs=not report["failed"],
        )
        report["status"] = (
            "LOW_RATE_SOAK_PASS"
            if report["completed"] > 0 and not pending and not unavailable and not report["failed"]
            else "INCOMPLETE"
        )
    except BaseException as exc:
        report["status"] = "FAILED"
        report["errors"].append(dict(kind="run", error_type=type(exc).__name__))
        report["pending_jobs"] = len(pending)
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        report["finished_at_ms"] = time.time_ns() // 1000000
        report["completed_jobs_per_second"] = (
            report["completed"] / report["elapsed_seconds"] if report["elapsed_seconds"] else None
        )
        report["job_record_acknowledgments_per_second"] = (
            report["job_record_acknowledgments_total"] / report["elapsed_seconds"]
            if report["elapsed_seconds"]
            else None
        )
        report["http_body_bytes_per_second_observed"] = (
            report["http_body_bytes_received_total"] / report["elapsed_seconds"]
            if report["elapsed_seconds"]
            else None
        )
        atomic_report(out, report)
    return report


def main():
    args = arguments()
    try:
        report = run(args)
    except Exception as exc:
        print(
            json.dumps(
                dict(kind="LOW_RATE_SOAK", status="PREFLIGHT_FAILED", error_type=type(exc).__name__)
            )
        )
        return 1
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("kind", "status", "submitted", "completed", "failed", "elapsed_seconds")
            }
        )
    )
    return 0 if report["status"] == "LOW_RATE_SOAK_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
