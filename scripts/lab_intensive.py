"""Published bounded ten-symbol load probe; no installation, elevation, cancellation or kills.

Only normal public Bybit candle admissions mutate the app. PG reads are read-only
and explicit localhost55470. Unknown metrics prevent an observed PASS.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab_soak as base  # noqa: E402


def latency_summary(values):
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 for x in values):
        raise ValueError("Invalid latency observation")
    ordered = sorted(values)

    def percentile(p):
        return ordered[max(0, math.ceil(len(ordered) * p) - 1)] if ordered else None

    return dict(
        count=len(values),
        mean_seconds=sum(values) / len(values) if values else None,
        p50_seconds=percentile(0.5),
        p95_seconds=percentile(0.95),
        max_seconds=max(values) if values else None,
    )


class SlotGate:
    def __init__(self, seconds):
        self.limit = min(40, math.ceil(seconds / 15))
        self.last = -1

    def take(self, elapsed):
        slot = int(elapsed // 15)
        if slot <= self.last or slot >= self.limit:
            return None
        self.last = slot
        return slot  # Missed slots are never replayed.


def can_admit(queue, remaining, stopped):
    return type(queue) is int and 0 <= queue < 20 and remaining >= 30 and not stopped


class StopPolicy:
    def __init__(self):
        self.memory_bad = self.latency_bad = 0

    def observe(self, *, queue, failed, memory, p95):
        if failed:
            return "FAILED_JOB"
        if queue is not None and queue > 40:
            return "QUEUE_LIMIT"
        if memory is not None:
            self.memory_bad = self.memory_bad + 1 if memory > 2**31 else 0
        if p95 is not None:
            self.latency_bad = self.latency_bad + 1 if p95 > 15 else 0
        if self.memory_bad >= 3:
            return "MEMORY_SUSTAINED"
        if self.latency_bad >= 3:
            return "READ_LATENCY_SUSTAINED"
        return None


def classify(reason, pending, completed, complete):
    if reason:
        return "STOPPED_BY_LIMIT"
    if pending or not completed or not complete or not all(complete.values()):
        return "INCOMPLETE"
    return "INTENSIVE_OBSERVED_PASS"


def request(path, body=None):
    started = time.perf_counter()
    connection = HTTPConnection("127.0.0.1", 18766, timeout=20)
    try:
        connection.request(
            "GET" if body is None else "POST",
            path,
            body=None if body is None else json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read(1024 * 1024 + 1)
        elapsed = time.perf_counter() - started
        if response.status != (200 if body is None else 202) or len(raw) > 1024 * 1024:
            raise ValueError("Unexpected local API response")
        return dict(value=json.loads(raw), seconds=elapsed, error_type=None)
    except Exception as exc:
        return dict(
            value=None, seconds=time.perf_counter() - started, error_type=type(exc).__name__
        )
    finally:
        connection.close()


def database_sample(dsn):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(
        dsn, connect_timeout=3, options="-c statement_timeout=2000", row_factory=dict_row
    ) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        scope = conn.execute(
            "SELECT current_database() AS db,inet_server_port() AS port"
        ).fetchone()
        if scope != dict(db="tester_lab", port=55470):
            raise ValueError("Unexpected PG endpoint")
        queue = conn.execute(
            "SELECT state,count(*) AS n FROM tester_platform.jobs GROUP BY state"
        ).fetchall()
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()
        counts = {row["state"]: row["n"] for row in queue}
        failed_rows = conn.execute(
            "SELECT id FROM tester_platform.jobs WHERE state='failed' ORDER BY id LIMIT 10001"
        ).fetchall()
        failed_ids = [str(uuid.UUID(str(row["id"]))) for row in failed_rows]
        if len(failed_ids) > 10000 or len(failed_ids) != counts.get("failed", 0):
            raise ValueError("Failed job inventory budget or consistency")
        return dict(
            queue=sum(counts.get(x, 0) for x in ("queued", "running", "paused")),
            running=counts.get("running", 0),
            failed=counts.get("failed", 0),
            failed_ids=failed_ids,
            database_bytes=next(iter(size.values())),
            states=counts,
        )


def process_sample(root, expected, supervisor):
    if os.name != "nt":
        raise ValueError("Windows counters unavailable")
    if type(supervisor) is not int or supervisor <= 0:
        raise ValueError("Supervisor PID required")
    names = expected["services"]
    if set(names) != {"app", "postgres"} or any(
        not re.fullmatch(prefix + "[0-9a-f]{32}", names[role])
        for role, prefix in (("app", "TesterLabApp_"), ("postgres", "TesterLabPG_"))
    ):
        raise ValueError("Unexpected service names")
    pg_image = subprocess.list2cmdline(
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
    ps = (
        r"""
$ErrorActionPreference='Stop'
$App=Get-CimInstance Win32_Service -Filter "Name='APP'"
$PG=Get-CimInstance Win32_Service -Filter "Name='PGNAME'"
if ($App.State -ne 'Running' -or $PG.State -ne 'Running' -or $App.StartName -ine 'NT AUTHORITY\LocalService' -or $PG.StartName -ine 'NT AUTHORITY\LocalService' -or $App.PathName -ine 'APPIMAGE' -or $PG.PathName -ine 'PGIMAGE') {throw 'Owned services mismatch'}
if ($App.ProcessId -le 0 -or $PG.ProcessId -le 0 -or $App.ProcessId -eq $PG.ProcessId) {throw 'Invalid service PID'}
# One inventory avoids two CIM round trips per descendant. Never emit raw images/commands.
$Inventory=@(Get-CimInstance Win32_Process -Property ProcessId,ParentProcessId,CreationDate,ExecutablePath,CommandLine)
if ($Inventory.Count -gt 10000) {throw 'Inventory budget'}
$ById=@{};$Children=@{}
foreach ($P in $Inventory) {
 $Key=[int]$P.ProcessId
 if ($ById.ContainsKey($Key)) {throw 'Duplicate inventory PID'}
 $ById[$Key]=$P
 $Parent=[int]$P.ParentProcessId
 if (-not $Children.ContainsKey($Parent)) {$Children[$Parent]=[Collections.Generic.List[int]]::new()}
 $Children[$Parent].Add($Key)
}
$Rows=@();$Bindings=@()
foreach ($Group in @(@('app',[int]$App.ProcessId),@('postgres',[int]$PG.ProcessId))) {
 $Queue=[Collections.Generic.Queue[int]]::new();$Queue.Enqueue($Group[1]);$Ids=[Collections.Generic.HashSet[int]]::new()
 while ($Queue.Count -gt 0) {
  $Id=$Queue.Dequeue();if(-not $Ids.Add($Id)){continue};if($Ids.Count -gt 64){throw 'Process budget'}
  $P=$ById[$Id]
  if ($null -eq $P -or $null -eq $P.CreationDate) {throw 'PID binding unavailable'}
  $Bindings+=[pscustomobject]@{group=$Group[0];pid=$Id;created=$P.CreationDate.ToUniversalTime().ToString('o');image_available=($null -ne $P.ExecutablePath);worker=($P.CommandLine -match '(?:^|\s)-m\s+src\.api\.worker(?:\s|$)');command_available=($null -ne $P.CommandLine)}
  if ($Children.ContainsKey($Id)) {
   foreach ($ChildId in $Children[$Id]) {
    $Child=$ById[$ChildId]
    if ($null -eq $Child.CreationDate -or $Child.CreationDate -lt $P.CreationDate) {throw 'Child PID creation mismatch'}
    $Queue.Enqueue($ChildId)
   }
  }
 }
 $Filter=(@($Ids|ForEach-Object {'IDProcess='+$_}) -join ' OR ')
 foreach($R in @(Get-CimInstance Win32_PerfFormattedData_PerfProc_Process -Filter $Filter)) {
  $Rows+=[pscustomobject]@{group=$Group[0];pid=$R.IDProcess;memory=$R.WorkingSetPrivate;cpu=$R.PercentProcessorTime;write_bytes_per_second=$R.IOWriteBytesPersec}
 }
 if (@($Rows|Where-Object {$_.group -eq $Group[0]}).Count -ne $Ids.Count) {throw 'Partial process counters'}
}
if (-not @($Bindings|Where-Object {$_.group -eq 'app' -and $_.pid -eq SEED}).Count){throw 'Supervisor not in app tree'}
[pscustomobject]@{rows=$Rows;bindings=$Bindings}|ConvertTo-Json -Depth 5 -Compress
""".replace("APPIMAGE", str(root / "dependencies/service/pythonservice.exe").replace("'", "''"))
        .replace("PGIMAGE", pg_image.replace("'", "''"))
        .replace("PGNAME", names["postgres"])
        .replace("Name='APP'", "Name='" + names["app"] + "'")
        .replace("SEED", str(supervisor))
    )
    raw = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
        timeout=10,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if len(raw.stdout) > 131072:
        raise ValueError("Counter response limit")
    data = json.loads(raw.stdout)
    groups = {}
    for group in ("app", "postgres"):
        rows = [x for x in data["rows"] if x["group"] == group]
        bindings = [x for x in data["bindings"] if x["group"] == group]
        if not rows or {x["pid"] for x in rows} != {x["pid"] for x in bindings}:
            raise ValueError("Incomplete process counters")
        if any(
            x.get(key) is None for x in rows for key in ("memory", "cpu", "write_bytes_per_second")
        ):
            raise ValueError("Unknown process metric")
        groups[group] = dict(
            process_count=len(rows),
            private_bytes=sum(int(x["memory"]) for x in rows),
            cpu_one_core_percent=sum(float(x["cpu"]) for x in rows),
            io_write_bytes_per_second=sum(float(x["write_bytes_per_second"]) for x in rows),
        )
    bindings = data["bindings"]
    app = [x for x in bindings if x["group"] == "app"]
    return dict(
        groups=groups,
        private_bytes=sum(x["private_bytes"] for x in groups.values()),
        worker_process_count=sum(x["worker"] for x in app)
        if all(x["command_available"] for x in app)
        else None,
        bindings=bindings,
        binding_mode="CIM configured service image, PID creation and descendant ancestry; image availability reported separately",
    )


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resources-only",
        action="store_true",
        help="One read-only process snapshot; no load or DB probes",
    )
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--root", type=Path, default=Path("C:/TesterLab"))
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if (
        not re.fullmatch("[0-9a-f]{40}", args.expected_sha)
        or not 60 <= args.seconds <= 600
        or not args.out.is_absolute()
    ):
        parser.error("Require SHA40, duration60..600 and absolute output")
    return args


def run(args):
    root, out = base.plain(args.root), base.plain(args.out)
    if out == root or root in out.parents or not out.parent.is_dir():
        raise ValueError("Output must be outside lab")
    expected, dsn, supervisor = base.bind(root, args.expected_sha)
    initial = database_sample(dsn)
    if initial["queue"]:
        raise ValueError("Require empty queue before load")
    baseline_failed = set(initial["failed_ids"])
    report = dict(
        kind="TEN_SYMBOL_INTENSIVE",
        status="RUNNING",
        identity=expected,
        policy=dict(
            seconds=args.seconds,
            slot_seconds=15,
            batch=10,
            max_slots=40,
            max_jobs=400,
            admission_pause_at=20,
            queue_stop_above=40,
            last_seconds_no_admission=30,
            read_timeout_seconds=20,
            read_p95_stop_above_seconds=15,
            private_memory_stop_above_bytes=2**31,
            sustained_samples=3,
            min_free_disk_bytes=2**30,
            kill_or_cancel=False,
            retries=False,
            deadline_semantics="No new cycle after deadline; in-flight bounded socket/CIM/SQL operations finish before return. Not a hard wall-clock deadline.",
        ),
        samples=[],
        jobs=[],
        errors=[],
        stop_reason=None,
        submitted=0,
        completed=0,
        failed=0,
        failure_baseline=dict(count=initial["failed"], ids=sorted(baseline_failed)),
        new_failed_ids=[],
        fairness={
            s: dict(
                submitted=0,
                completed=0,
                failed=0,
                queue_wait_upper_bounds_seconds=[],
                nonempty_reads=0,
                read_rows=0,
            )
            for s in base.SYMBOLS
        },
        observations="Ten concurrent admissions/probes; worker/running counts observed independently, never inferred from client concurrency.",
        queue_wait_semantics="First-running observation minus submission is an upper bound; null when running state was missed.",
        io_semantics="CIM process IOWriteBytesPersec is not physical disk bandwidth; WAL is dedicated cluster-wide, not per-job.",
        acceptance_limits="Observation probe only: does not certify parallel processing, saturation, retry, cancellation or enforcement of server CPU/WAL quotas.",
        logical_cpu_count=os.cpu_count(),
        read_latencies=[],
        post_latencies=[],
        slot_events=[],
        metrics_complete=dict(
            postgres=True, processes=True, disk=True, latency=True, worker_count=True
        ),
        peaks=dict(active_jobs=0, worker_processes=None, private_bytes=None),
        started_at_ms=time.time_ns() // 1000000,
    )
    base.atomic_report(out, report, initial=True)
    start = time.monotonic()
    deadline = start + args.seconds
    gate, policy = SlotGate(args.seconds), StopPolicy()
    pending = {}
    read_windows = {}
    previous_pg = None
    previous_at = None
    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            while time.monotonic() < deadline:
                at = time.monotonic()
                base.assert_identity(base.http("/api/lab/identity"), expected)
                health = base.http("/api/health")["data"]
                if (
                    health.get("status") != "ok"
                    or health.get("instance_id") != expected["instance_id"]
                ):
                    raise ValueError("Health/source instance changed")
                for ident, result in zip(
                    list(pending), pool.map(lambda i: request("/api/downloads/" + i), list(pending))
                ):
                    if result["error_type"]:
                        raise ValueError("Job observation unavailable")
                    actual = result["value"]["data"]
                    job = pending[ident]
                    if actual["state"] == "running" and job["first_running_upper_bound"] is None:
                        job["first_running_upper_bound"] = time.monotonic() - job["at"]
                    if actual["state"] in ("completed", "failed"):
                        state = actual["state"]
                        report[state] += 1
                        report["fairness"][job["symbol"]][state] += 1
                        evidence = (
                            base.completed_telemetry(actual) if state == "completed" else None
                        )
                        report["jobs"].append(
                            dict(
                                id=ident,
                                symbol=job["symbol"],
                                state=state,
                                observed_elapsed_seconds=time.monotonic() - job["at"],
                                queue_wait_upper_bound_seconds=job["first_running_upper_bound"],
                                telemetry=evidence,
                            )
                        )
                        if job["first_running_upper_bound"] is not None:
                            report["fairness"][job["symbol"]][
                                "queue_wait_upper_bounds_seconds"
                            ].append(job["first_running_upper_bound"])
                        if state == "completed":
                            read_windows[job["symbol"]] = job["window"]
                        del pending[ident]
                db, db_error = base.measured(lambda: database_sample(dsn))
                pg, pg_error = base.measured(lambda: base.pg_stats(dsn))
                processes, process_error = base.measured(
                    lambda: process_sample(root, expected, supervisor)
                )
                disk, disk_error = base.measured(lambda: base.disk_stats(root))
                slot = gate.take(time.monotonic() - start)
                read_summary = None
                if slot is not None and time.monotonic() < deadline:
                    paths = []
                    for symbol in base.SYMBOLS:
                        a, b = read_windows.get(
                            symbol, base.window(time.time_ns() // 1000000, slot)
                        )
                        paths.append(
                            "/api/candles?"
                            + urlencode(
                                dict(
                                    category="linear",
                                    symbol=symbol,
                                    interval="1",
                                    start_ms=a,
                                    end_ms=b,
                                    limit=5,
                                )
                            )
                        )
                    reads = list(pool.map(request, paths))
                    for symbol, result in zip(base.SYMBOLS, reads):
                        report["read_latencies"].append(result["seconds"])
                        if result["error_type"]:
                            report["metrics_complete"]["latency"] = False
                            report["stop_reason"] = report["stop_reason"] or "READ_REQUEST_FAILED"
                        else:
                            rows = result["value"]["data"]
                            if not isinstance(rows, list) or len(rows) > 5:
                                raise ValueError("Unbounded candle response")
                            if any(
                                not row.get("is_closed")
                                or not row.get("provenance")
                                or row.get("kind") != "official_trade"
                                for row in rows
                            ):
                                raise ValueError("Unverified candle response")
                            report["fairness"][symbol]["nonempty_reads"] += bool(rows)
                            report["fairness"][symbol]["read_rows"] += len(rows)
                    read_summary = latency_summary([x["seconds"] for x in reads])
                queue = db["queue"] if db else None
                if db:
                    report["new_failed_ids"] = sorted(
                        set(report["new_failed_ids"]) | (set(db["failed_ids"]) - baseline_failed)
                    )
                reason = policy.observe(
                    queue=queue,
                    failed=bool(report["new_failed_ids"]) or report["failed"],
                    memory=processes["private_bytes"] if processes else None,
                    p95=read_summary["p95_seconds"] if read_summary else None,
                )
                report["stop_reason"] = report["stop_reason"] or reason
                if disk and disk["shared_volume_free_bytes"] < 2**30:
                    report["stop_reason"] = report["stop_reason"] or "DISK_FREE_LIMIT"
                if db_error or pg_error:
                    report["metrics_complete"]["postgres"] = False
                if process_error:
                    report["metrics_complete"]["processes"] = False
                if disk_error:
                    report["metrics_complete"]["disk"] = False
                if not processes or processes["worker_process_count"] is None:
                    report["metrics_complete"]["worker_count"] = False
                if slot is not None:
                    # Metrics/read probes can be slow. Re-read admission pressure rather
                    # than admitting ten jobs using an old queue observation.
                    admission_db, admission_error = base.measured(lambda: database_sample(dsn))
                    if admission_error:
                        report["metrics_complete"]["postgres"] = False
                        queue = None
                    else:
                        queue = admission_db["queue"]
                        if queue > 40:
                            report["stop_reason"] = report["stop_reason"] or "QUEUE_LIMIT"
                        report["new_failed_ids"] = sorted(
                            set(report["new_failed_ids"])
                            | (set(admission_db["failed_ids"]) - baseline_failed)
                        )
                        if report["new_failed_ids"] or report["failed"]:
                            report["stop_reason"] = report["stop_reason"] or "FAILED_JOB"
                    admitted = (
                        can_admit(queue, deadline - time.monotonic(), bool(report["stop_reason"]))
                        and db is not None
                    )
                    report["slot_events"].append(
                        dict(
                            slot=slot,
                            elapsed_seconds=time.monotonic() - start,
                            admitted=admitted,
                            queue=queue,
                            error_type=admission_error,
                        )
                    )
                    if admitted:
                        a, b = base.window(time.time_ns() // 1000000, slot)
                        bodies = [
                            dict(
                                category="linear",
                                symbol=s,
                                source="bybit",
                                kind="candles",
                                interval="1",
                                start_ms=a,
                                end_ms=b,
                            )
                            for s in base.SYMBOLS
                        ]
                        posted = list(
                            pool.map(lambda body: request("/api/downloads", body), bodies)
                        )
                        for body, result in zip(bodies, posted):
                            report["post_latencies"].append(result["seconds"])
                            if result["error_type"]:
                                report["stop_reason"] = (
                                    report["stop_reason"] or "AMBIGUOUS_ADMISSION"
                                )
                                report["errors"].append(
                                    dict(
                                        kind="admission",
                                        symbol=body["symbol"],
                                        error_type=result["error_type"],
                                    )
                                )
                                continue
                            ident = str(uuid.UUID(result["value"]["data"]["id"]))
                            if ident in pending:
                                raise ValueError("Duplicate admission identity")
                            pending[ident] = dict(
                                symbol=body["symbol"],
                                at=time.monotonic(),
                                window=(a, b),
                                first_running_upper_bound=None,
                            )
                            report["submitted"] += 1
                            report["fairness"][body["symbol"]]["submitted"] += 1
                now = time.monotonic()
                delta = base.counter_delta(previous_pg, pg, now - previous_at if previous_at else 0)
                if previous_at and any(x is None for x in delta.values()):
                    report["metrics_complete"]["postgres"] = False
                report["samples"].append(
                    dict(
                        elapsed_seconds=now - start,
                        collection_seconds=now - at,
                        queue=db,
                        postgres=pg,
                        processes=processes,
                        disk=disk,
                        read_latency=read_summary,
                        database_activity=delta,
                        errors=dict(
                            database=db_error,
                            postgres=pg_error,
                            processes=process_error,
                            disk=disk_error,
                        ),
                    )
                )
                previous_pg, previous_at = pg, now
                report["peaks"]["active_jobs"] = max(
                    report["peaks"]["active_jobs"], db["running"] if db else 0
                )
                if processes:
                    for key, value in [
                        ("private_bytes", processes["private_bytes"]),
                        ("worker_processes", processes["worker_process_count"]),
                    ]:
                        if value is not None:
                            report["peaks"][key] = max(report["peaks"][key] or 0, value)
                report["pending_jobs"] = len(pending)
                report["elapsed_seconds"] = now - start
                report["read_latency"] = latency_summary(report["read_latencies"])
                report["post_latency"] = latency_summary(report["post_latencies"])
                base.atomic_report(out, report)
                if report["stop_reason"]:
                    break
                time.sleep(max(0, min(5 - (time.monotonic() - at), deadline - time.monotonic())))
        base.assert_identity(base.http("/api/lab/identity"), expected)
        final_db, final_error = base.measured(lambda: database_sample(dsn))
        if final_error:
            report["metrics_complete"]["postgres"] = False
        else:
            report["new_failed_ids"] = sorted(
                set(report["new_failed_ids"]) | (set(final_db["failed_ids"]) - baseline_failed)
            )
            if report["new_failed_ids"] or report["failed"]:
                report["stop_reason"] = report["stop_reason"] or "FAILED_JOB"
        report["metrics_complete"]["latency"] &= all(
            x["nonempty_reads"] > 0 for x in report["fairness"].values()
        )
        report["status"] = classify(
            report["stop_reason"], len(pending), report["completed"], report["metrics_complete"]
        )
    except BaseException as exc:
        report["status"] = "FAILED"
        report["stop_reason"] = report["stop_reason"] or "OBSERVATION_FAILED"
        report["errors"].append(dict(kind="run", error_type=type(exc).__name__))
    finally:
        report["elapsed_seconds"] = time.monotonic() - start
        report["pending_jobs"] = len(pending)
        report["finished_at_ms"] = time.time_ns() // 1000000
        report["read_latency"] = latency_summary(report["read_latencies"])
        report["post_latency"] = latency_summary(report["post_latencies"])
        report["pending_ids"] = list(pending)
        base.atomic_report(out, report)
    return report


def resource_observation(args):
    root, out = base.plain(args.root), base.plain(args.out)
    if out == root or root in out.parents or not out.parent.is_dir() or out.exists():
        raise ValueError("Require absent output outside lab")
    expected, _dsn, supervisor = base.bind(root, args.expected_sha)
    started = time.monotonic()
    processes, error = base.measured(lambda: process_sample(root, expected, supervisor))
    base.assert_identity(base.http("/api/lab/identity"), expected)
    report = dict(
        kind="RESOURCE_OBSERVATION_ONLY",
        status="RESOURCE_OBSERVED"
        if processes and processes["worker_process_count"] is not None
        else "INCOMPLETE",
        identity=expected,
        processes=processes,
        error_type=error,
        collection_seconds=time.monotonic() - started,
        observed_at_ms=time.time_ns() // 1000000,
        scope="One read-only CIM snapshot; no admissions, DB probes, retries, cancellation or kills. Does not repair metrics of an earlier load report.",
    )
    base.atomic_report(out, report, initial=True)
    return report


def main():
    try:
        args = arguments()
        if getattr(args, "resources_only", False):
            result = resource_observation(args)
            print(json.dumps(dict(kind=result["kind"], status=result["status"])))
            return 0 if result["status"] == "RESOURCE_OBSERVED" else 1
        result = run(args)
    except Exception as exc:
        print(json.dumps(dict(status="PREFLIGHT_FAILED", error_type=type(exc).__name__)))
        return 1
    print(
        json.dumps(
            dict(
                status=result["status"],
                submitted=result["submitted"],
                completed=result["completed"],
                failed=result["failed"],
                stop_reason=result["stop_reason"],
            )
        )
    )
    return 0 if result["status"] == "INTENSIVE_OBSERVED_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
