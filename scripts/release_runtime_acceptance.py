"""Disposable real PostgreSQL/runtime comparison and bounded public read-only probes.

Uses existing Store, JobQueue, process_one and application factory. No production
services, default PostgreSQL, or Guardian effectors are touched.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import logging
import os
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

WORKLOAD = {
    "version": 1,
    "rows": 300,
    "jobs": 40,
    "workers": 4,
    "http_requests": 30,
    "fixture_kind": "deterministic_synthetic_candles_not_exchange_history",
}


def validate_port(port):
    if type(port) is not int or not 55820 <= port <= 55827:
        raise ValueError("Only disposable runtime PostgreSQL ports 55820..55827 are permitted")
    return port


def validate_runtime_dsn(dsn):
    from psycopg.conninfo import conninfo_to_dict

    values = conninfo_to_dict(dsn)
    try:
        validate_port(int(values.get("port", "0")))
    except (ValueError, TypeError) as exc:
        raise ValueError("Reserved disposable database required") from exc
    if (
        values.get("host") != "127.0.0.1"
        or values.get("hostaddr", "127.0.0.1") != "127.0.0.1"
        or values.get("dbname") != "tester_acceptance"
        or values.get("user") != "tester_release"
    ):
        raise ValueError("Explicit disposable runtime identity required")
    return dsn


def compare_results(baseline, candidate):
    for key in ("workload_sha256", "rows", "jobs"):
        if baseline[key] != candidate[key]:
            raise ValueError("Workloads differ: " + key)
    if baseline["status"] != "PASS" or candidate["status"] != "PASS":
        raise ValueError("Cannot compare unverified runs")
    ratios = {}
    for key, value in baseline["metrics"].items():
        other = candidate["metrics"].get(key)
        if type(value) in (float, int) and type(other) in (float, int) and value > 0:
            ratios[key] = other / value
    return {
        "ratios": ratios,
        "interpretation": "candidate/baseline; one short run, descriptive only; no sustained-load claim",
    }


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_command(argv, *, cwd=None, env=None, timeout=180, log=None):
    options = dict(
        cwd=cwd,
        env=env,
        timeout=timeout,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if log:
        # pg_ctl's server inherits pipe handles on Windows; real files avoid
        # communicate() waiting for a healthy long-running server to close them.
        with Path(log).open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                [str(x) for x in argv], stdout=stream, stderr=subprocess.STDOUT, **options
            )
        output = Path(log).read_text(encoding="utf-8")
    else:
        result = subprocess.run([str(x) for x in argv], capture_output=True, **options)
        output = result.stdout
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}); log={log}")
    return output


def snapshot(repo, revision, destination):
    # Committed objects cannot change when another agent edits its working tree.
    sha = run_command(["git", "-C", repo, "rev-parse", revision]).strip()
    data = subprocess.check_output(["git", "-C", str(repo), "archive", "--format=zip", sha])
    destination.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            target = (destination / name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError("Unsafe archive path")
        archive.extractall(destination)
    write_json(
        destination.parent / (destination.name + "-source.json"),
        {"sha": sha, "archive_sha256": hashlib.sha256(data).hexdigest()},
    )
    return sha


def fixture():
    base = 1704067200000
    return [
        dict(
            schema_version="1.0",
            venue="bybit",
            category="linear",
            symbol="BTCUSDT",
            dataset="candles",
            timestamp_ms=base + i * 60000,
            interval="1",
            payload=dict(
                open="100.000000000000000001",
                high="102",
                low="99",
                close="101",
                volume="1.25",
                turnover="126.25",
            ),
            source=dict(
                source_kind="rest",
                url="https://api.bybit.com/v5/market/kline",
                fetched_at="2026-11-23T00:00:00Z",
                sha256="a" * 64,
            ),
        )
        for i in range(WORKLOAD["rows"])
    ]


def evidence(count=None):
    return dict(
        venue="bybit",
        category="linear",
        symbol="BTCUSDT",
        dataset="candles",
        interval="1",
        start_ms=1704067200000,
        end_ms=1704067200000 + WORKLOAD["rows"] * 60000,
        status="EXHAUSTED",
        available_range=None,
        pages=[
            dict(
                params={"synthetic_fixture": True},
                cursor=None,
                next_cursor=None,
                count=WORKLOAD["rows"] if count is None else count,
            )
        ],
    )


def child_workload(args):
    sys.path.insert(0, str(args.source.resolve()))
    import psycopg
    import requests
    import uvicorn

    from src.api.config import Settings
    from src.api.db import migrate as platform_migrate
    from src.api.jobs import JobQueue, LeaseLost
    from src.api.main import create_app
    from src.api.models import DownloadRequest
    from src.api.worker import process_one
    from src.integration.storage_reader import StorageTradeReader
    from src.storage.migrations import migrate
    from src.storage.models import digest
    from src.storage.store import DataConflict, Store

    dsn = validate_runtime_dsn(os.environ["TEST_DATABASE_URL"])
    store = Store(dsn)
    queue = JobQueue(dsn, lease_seconds=60)
    state_path = args.result.parent / "durable-state.json"
    if args.child == "verify":
        queue.lease_seconds = 60
        state = json.loads(state_path.read_text())
        start = time.perf_counter()
        rows = store.read(
            "candles",
            "bybit",
            "linear",
            "BTCUSDT",
            evidence()["start_ms"],
            evidence()["end_ms"],
            interval="1",
            limit=1000,
        )
        assert len(rows) == WORKLOAD["rows"]
        assert digest(rows) == state["rows_sha256"]
        assert store.checkpoint("release-load") == {"offset": WORKLOAD["rows"]}
        crashed = queue.get(uuid.UUID(state["crashed_job"]))
        assert crashed.checkpoint == {"offset": 7} and crashed.state == "running"
        time.sleep(1.1)
        new_lease = queue.claim()
        assert str(new_lease.id) == state["crashed_job"]
        assert str(new_lease.lease_token) != state["crashed_token"]
        try:
            queue.finish(new_lease.id, uuid.UUID(state["crashed_token"]))
        except LeaseLost:
            pass
        else:
            raise AssertionError("Stale lease accepted after crash")
        assert new_lease.checkpoint == {"offset": 7}
        queue.finish(new_lease.id, new_lease.lease_token)
        with psycopg.connect(dsn) as conn:
            completed = conn.execute(
                "SELECT count(*) FROM tester_platform.jobs WHERE state='completed'"
            ).fetchone()[0]
            assert completed == state["completed_jobs_before_crash"] + 1
        result = {
            "status": "PASS",
            "durable_rows": len(rows),
            "row_digest_preserved": True,
            "checkpoint_preserved": True,
            "stale_lease_fenced": True,
            "completed_jobs": state["completed_jobs_before_crash"] + 1,
            "recovery_verify_seconds": time.perf_counter() - start,
        }
        write_json(args.result, result)
        return

    platform_migrate(dsn)
    platform_migrate(dsn)
    with psycopg.connect(dsn) as conn:
        migrate(conn)
        migrate(conn)
    queue.configure_limits(256, WORKLOAD["workers"])
    rows = fixture()
    start = time.perf_counter()
    store.ingest(rows, evidence(), job_key="release-load", checkpoint={"offset": len(rows)})
    load_seconds = time.perf_counter() - start
    replay = copy.deepcopy(rows)
    for record in replay:
        record["source"]["fetched_at"] = "2026-11-23T01:00:00Z"
    store.ingest(replay, evidence(), job_key="release-load", checkpoint={"offset": len(rows)})
    readback = store.read(
        "candles",
        "bybit",
        "linear",
        "BTCUSDT",
        evidence()["start_ms"],
        evidence()["end_ms"],
        interval="1",
        limit=1000,
    )
    assert len(readback) == len(rows)
    assert readback[0]["payload"]["open"] == "100.000000000000000001"
    with psycopg.connect(dsn) as conn:
        provenance = conn.execute("SELECT count(*) FROM tester_storage.record_sources").fetchone()[
            0
        ]
        assert provenance == len(rows) * 2
        raw_count = conn.execute("SELECT count(*) FROM tester_storage.raw_records").fetchone()[0]
        assert raw_count == len(rows)
        conn.execute("UPDATE tester_storage.schema_migrations SET checksum='bad' WHERE version=1")
        try:
            migrate(conn)
        except ValueError:
            pass
        else:
            raise AssertionError("Corrupted migration checksum accepted")
        conn.rollback()
    changed = copy.deepcopy(rows)
    changed[0]["payload"]["close"] = "100.5"
    try:
        store.ingest(changed, evidence(), job_key="release-load")
    except DataConflict:
        pass
    else:
        raise AssertionError("Conflicting candle accepted")

    def interrupted():
        yield rows[0]
        raise RuntimeError("intentional interrupted batch")

    try:
        store.ingest(
            interrupted(), evidence(), job_key="release-load", checkpoint={"offset": len(rows) + 1}
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("Interrupted batch accepted")
    assert store.checkpoint("release-load") == {"offset": len(rows)}

    def concurrent_read_replay(index):
        store.ingest(rows, evidence(), job_key="release-load")
        result = store.read(
            "candles",
            "bybit",
            "linear",
            "BTCUSDT",
            evidence()["start_ms"],
            evidence()["end_ms"],
            interval="1",
            limit=1000,
        )
        assert len(result) == len(rows)
        return len(result)

    concurrent_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(concurrent_read_replay, range(4))) == len(rows) * 4
    concurrent_seconds = time.perf_counter() - concurrent_start

    request = DownloadRequest(
        category="linear",
        symbol="BTCUSDT",
        source="bybit",
        kind="candles",
        interval="1",
        start_ms=0,
        end_ms=60000,
    )
    for _ in range(WORKLOAD["jobs"]):
        queue.create(request)
    assert queue.depth() == WORKLOAD["jobs"]

    class FixtureHandler:
        def run(self, request, checkpoint, report, stop, commit):
            report({"offset": 1, "fixture": "no_network"}, 0.5)
            commit(lambda conn: conn.execute("SELECT 1"), {"offset": 2}, 1)

    worker_start = time.perf_counter()

    def worker(index):
        completed = 0
        while process_one(queue, FixtureHandler(), threading.Event(), logging.getLogger("release")):
            completed += 1
        return completed

    with ThreadPoolExecutor(max_workers=WORKLOAD["workers"]) as executor:
        processed = sum(executor.map(worker, range(WORKLOAD["workers"])))
    worker_seconds = time.perf_counter() - worker_start
    assert processed == WORKLOAD["jobs"] and queue.depth() == 0
    worker_identity = uuid.uuid4()
    queue.heartbeat(worker_identity)
    settings = Settings(
        database_url=dsn,
        data_dir=args.result.parent / "app-data",
        frontend_dir=args.source / "frontend",
        port=args.api_port,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, provider=StorageTradeReader(store)),
            host="127.0.0.1",
            port=args.api_port,
            log_level="error",
            lifespan="on",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    session = requests.Session()
    session.trust_env = False
    api_latencies = []
    soak = {
        "requested_seconds": args.soak_seconds,
        "completed_jobs": 0,
        "http_requests": 0,
        "peak_queue_depth": 0,
        "samples": [],
    }
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("API start timed out")
            time.sleep(0.05)
        for _ in range(WORKLOAD["http_requests"]):
            before = time.perf_counter()
            reply = session.get(f"http://127.0.0.1:{args.api_port}/api/health", timeout=5)
            body = reply.json()
            assert reply.status_code == 200 and body["data"]["database"] == "ok", body
            api_latencies.append((time.perf_counter() - before) * 1000)
        soak_start = time.perf_counter()
        soak_latencies = []
        next_sample = 0
        with ThreadPoolExecutor(max_workers=WORKLOAD["workers"]) as executor:
            while time.perf_counter() - soak_start < args.soak_seconds:
                queue.heartbeat(worker_identity)
                for _ in range(WORKLOAD["workers"]):
                    queue.create(request)
                soak["peak_queue_depth"] = max(soak["peak_queue_depth"], queue.depth())
                finished_jobs = sum(executor.map(worker, range(WORKLOAD["workers"])))
                assert finished_jobs == WORKLOAD["workers"] and queue.depth() == 0
                soak["completed_jobs"] += finished_jobs
                read_again = store.read(
                    "candles",
                    "bybit",
                    "linear",
                    "BTCUSDT",
                    evidence()["start_ms"],
                    evidence()["end_ms"],
                    interval="1",
                    limit=1000,
                )
                assert digest(read_again) == digest(readback)
                before = time.perf_counter()
                reply = session.get(f"http://127.0.0.1:{args.api_port}/api/health", timeout=5)
                assert reply.status_code == 200 and reply.json()["data"]["database"] == "ok"
                soak_latencies.append((time.perf_counter() - before) * 1000)
                soak["http_requests"] += 1
                elapsed = time.perf_counter() - soak_start
                if elapsed >= next_sample:
                    soak["samples"].append(
                        {
                            "elapsed_seconds": elapsed,
                            "completed_jobs": soak["completed_jobs"],
                            "queue_depth": queue.depth(),
                        }
                    )
                    write_json(args.result.parent / "soak-progress.json", soak)
                    next_sample = elapsed + 30
        soak["elapsed_seconds"] = time.perf_counter() - soak_start
        if args.soak_seconds:
            soak["jobs_per_second"] = soak["completed_jobs"] / soak["elapsed_seconds"]
            soak["api_mean_ms"] = statistics.mean(soak_latencies)
            soak["api_p95_ms"] = sorted(soak_latencies)[max(0, int(len(soak_latencies) * 0.95) - 1)]
            assert soak["elapsed_seconds"] >= args.soak_seconds
            soak["status"] = "PASS"
        else:
            soak["status"] = "NOT RUN"
    finally:
        server.should_exit = True
        thread.join(10)
        session.close()
    with psycopg.connect(dsn) as conn:
        database_bytes = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        count = conn.execute(
            "SELECT count(*) FROM tester_platform.jobs WHERE state='completed'"
        ).fetchone()[0]
        assert count == WORKLOAD["jobs"] + soak["completed_jobs"]
    queue.lease_seconds = 1  # Only the deliberate crash-recovery fixture uses a short lease.
    pending = queue.create(request)
    lease = queue.claim()
    assert lease.id == pending.id
    queue.checkpoint(lease.id, lease.lease_token, {"offset": 7}, 0.7)
    write_json(
        state_path,
        {
            "rows_sha256": digest(readback),
            "crashed_job": str(lease.id),
            "crashed_token": str(lease.lease_token),
            "completed_jobs_before_crash": WORKLOAD["jobs"] + soak["completed_jobs"],
        },
    )
    write_json(
        args.result,
        {
            "status": "PASS",
            "rows": len(rows),
            "jobs": processed,
            "workload_sha256": hashlib.sha256(
                json.dumps(
                    {"workload": WORKLOAD, "soak_seconds": args.soak_seconds}, sort_keys=True
                ).encode()
            ).hexdigest(),
            "soak": soak,
            "fixture_kind": WORKLOAD["fixture_kind"],
            "provenance_rows": provenance,
            "coverage": "UNKNOWN; synthetic fixture does not establish real exchange history",
            "checks": [
                "migration_replay",
                "migration_checksum_rejection",
                "exact_decimal_ohlcv",
                "duplicate_replay",
                "provenance",
                "conflict_rejection",
                "interruption_rollback",
                "concurrent_read_replay",
                "real_worker_queue_transactions",
                "real_http_health_body",
            ],
            "metrics": {
                "load_seconds": load_seconds,
                "history_rows_per_second": len(rows) / load_seconds,
                "concurrent_replay_seconds": concurrent_seconds,
                "worker_seconds": worker_seconds,
                "worker_jobs_per_second": processed / worker_seconds,
                "database_bytes": database_bytes,
                "api_mean_ms": statistics.mean(api_latencies),
                "api_p95_ms": sorted(api_latencies)[int(len(api_latencies) * 0.95) - 1],
            },
            "api_health_requests": len(api_latencies),
            "queue_remaining_before_crash": queue.depth(),
            "python": sys.version,
            "python_executable": sys.executable,
        },
    )


def public_probes(args, source, root):
    sys.path.insert(0, str(source))
    import requests

    from src.ingest.transport import Transport

    session = requests.Session()
    session.trust_env = False
    if args.ca_bundle:
        session.verify = str(args.ca_bundle.resolve())
    transport = Transport(session=session, attempts=1)
    cases = [
        ("time", "https://api.bybit.com/v5/market/time"),
        (
            "kline",
            "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=1&limit=3&start=1704067200000&end=1704067320000",
        ),
        ("archive", "https://public.bybit.com/trading/BTCUSDT/"),
    ]
    report = []
    for name, url in cases:
        before = time.perf_counter()
        item = {"name": name, "url": url, "read_only": True, "attempt_budget": 1}
        try:
            with transport.get(url, timeout=(5, 10), stream=True) as response:
                content = b""
                for chunk in response.iter_content(4096):
                    content += chunk
                    if len(content) >= 32768:
                        break
                item.update(
                    http_status=response.status_code,
                    sampled_body_bytes=len(content),
                    sampled_sha256=hashlib.sha256(content).hexdigest(),
                )
                sample_path = root / ("public-" + name + "-sample.bin")
                sample_path.write_bytes(content)
                item["sample_file"] = sample_path.name
                if name in {"time", "kline"}:
                    body = json.loads(content)
                    assert body["retCode"] == 0 and isinstance(body["result"], dict)
                    if name == "time":
                        assert int(body["result"]["timeSecond"]) > 0
                    else:
                        candles = body["result"]["list"]
                        assert len(candles) == 3 and all(len(row) >= 7 for row in candles)
                        assert {int(row[0]) for row in candles} == {
                            1704067200000,
                            1704067260000,
                            1704067320000,
                        }
                        item["historical_candles"] = 3
                        item["historical_complete"] = False
                else:
                    assert b"href=" in content.lower()
                item["status"] = "PASS"
        except Exception as exc:
            item.update(status="FAIL", error_type=type(exc).__name__)
            if getattr(exc, "response", None) is not None:
                item["http_status"] = exc.response.status_code
            item["reason"] = (
                "Public availability or response qualification failed; no bypass attempted"
            )
        item["elapsed_seconds"] = time.perf_counter() - before
        report.append(item)
    session.close()
    write_json(root / "public-bybit.json", report)
    return report


def measured_child(command, *, env, cwd, output, pg_pid):
    import psutil

    process = subprocess.Popen(
        [str(x) for x in command],
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    observed = {}
    disk_before = psutil.disk_io_counters()
    rss_peak = 0
    start = time.perf_counter()
    while process.poll() is None:
        if time.perf_counter() - start > int(env.get("RELEASE_SOAK_SECONDS", "0")) + 180:
            process.kill()
            raise RuntimeError("Bounded workload timed out")
        targets = {}
        for pid in (process.pid, pg_pid):
            try:
                parent = psutil.Process(pid)
                for item in [parent, *parent.children(recursive=True)]:
                    targets[item.pid] = item
            except psutil.Error:
                pass
        rss = 0
        for pid, item in targets.items():
            try:
                cpu = item.cpu_times()
                counters = item.io_counters()
                rss += item.memory_info().rss
                value = (cpu.user + cpu.system, counters.read_bytes, counters.write_bytes)
                if pid not in observed:
                    observed[pid] = {"first": value, "last": value}
                else:
                    observed[pid]["last"] = value
            except psutil.Error:
                pass
        rss_peak = max(rss_peak, rss)
        time.sleep(0.05)
    stdout = process.communicate(timeout=5)[0]
    Path(output).write_text(stdout, encoding="utf-8")
    if process.returncode:
        raise RuntimeError("Runtime workload failed; inspect " + str(output))
    delta = [sum(v["last"][i] - v["first"][i] for v in observed.values()) for i in range(3)]
    elapsed = time.perf_counter() - start
    disk_after = psutil.disk_io_counters()
    return {
        "sampled_process_cpu_seconds": delta[0],
        "sampled_process_cpu_percent_one_core": 100 * delta[0] / elapsed,
        "sampled_tree_peak_rss_bytes": rss_peak,
        "sampled_process_read_bytes": delta[1],
        "sampled_process_write_bytes": delta[2],
        "sampled_wall_seconds": elapsed,
        "sampling_interval_seconds": 0.05,
        "system_disk_read_bytes": disk_after.read_bytes - disk_before.read_bytes
        if disk_before and disk_after
        else None,
        "system_disk_write_bytes": disk_after.write_bytes - disk_before.write_bytes
        if disk_before and disk_after
        else None,
    }


def run_pair(args):
    import psycopg
    from psycopg import sql

    validate_port(args.pg_port)
    os.environ["GUARDIANS_ENABLED"] = "false"
    os.environ["GIT_CONFIG_COUNT"] = "0"
    for name in tuple(os.environ):
        if name.startswith("PG"):
            os.environ.pop(name)
    for port in (args.pg_port, args.api_port):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    args.work_root.mkdir(parents=True, exist_ok=True)
    root = args.work_root.resolve() / (
        "run-" + time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    )
    root.mkdir()
    runner_copy = root / "runner.py"
    runner_copy.write_bytes(Path(__file__).read_bytes())
    source_root = root / "sources"
    source_root.mkdir()
    baseline = source_root / "baseline"
    candidate = source_root / "candidate"
    baseline_sha = snapshot(args.baseline_repo, args.baseline_ref, baseline)
    candidate_sha = snapshot(args.candidate_repo, args.candidate_ref, candidate)
    if baseline_sha != "e64e30d8369626197403b3962f91bb350656e4c9":
        raise ValueError("Unexpected approved baseline")
    manifest = {
        "baseline_sha": baseline_sha,
        "candidate_sha": candidate_sha,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "workload": WORKLOAD,
        "soak_seconds": args.soak_seconds,
        "candidate_only": args.candidate_only,
        "guardians_enabled": False,
        "fixture": "synthetic workload; not live exchange acquisition",
        "system_5432": "NO CONNECTION ATTEMPTED",
    }
    pg = args.pg_bin.resolve()
    suffix = ".exe" if os.name == "nt" else ""
    ctl = pg / ("pg_ctl" + suffix)
    manifest["postgres_version"] = run_command([pg / ("postgres" + suffix), "--version"]).strip()
    write_json(root / "manifest.json", manifest)
    results = {}
    pairs = (
        [("candidate", candidate)]
        if args.candidate_only
        else [("baseline", baseline), ("candidate", candidate)]
    )
    for label, source in pairs:
        work = root / label
        work.mkdir()
        data = work / "pgdata"
        run_command(
            [
                pg / ("initdb" + suffix),
                "-D",
                data,
                "-A",
                "trust",
                "-U",
                "tester_release",
                "--no-locale",
                "--encoding=UTF8",
            ],
            log=work / "initdb.log",
        )
        write_json(
            work / "ownership.json",
            {
                "data_directory": str(data.resolve()),
                "port": args.pg_port,
                "purpose": "disposable_release_runtime",
                "run_root": str(root),
            },
        )
        admin_dsn = f"host=127.0.0.1 hostaddr=127.0.0.1 port={args.pg_port} user=tester_release dbname=postgres connect_timeout=5"

        def start():
            run_command(
                [
                    ctl,
                    "-D",
                    data,
                    "-l",
                    work / "postgres.log",
                    "-o",
                    f"-h 127.0.0.1 -p {args.pg_port} -c fsync=on -c full_page_writes=on",
                    "-w",
                    "-t",
                    "30",
                    "start",
                ],
                log=work / "pg-start.log",
            )

        def stop(mode="fast"):
            run_command(
                [ctl, "-D", data, "-m", mode, "-w", "-t", "30", "stop"],
                log=work / ("pg-stop-" + mode + ".log"),
            )

        started = False
        try:
            start()
            started = True
            with psycopg.connect(admin_dsn, autocommit=True) as conn:
                actual = Path(conn.execute("SHOW data_directory").fetchone()[0]).resolve()
                if actual != data.resolve():
                    raise ValueError("Dedicated cluster ownership mismatch")
                conn.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier("tester_acceptance"))
                )
                conn.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier("tester_research_test"))
                )
                wal_before = conn.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
            dsn = admin_dsn.replace("dbname=postgres", "dbname=tester_acceptance")
            env = {
                **os.environ,
                "TEST_DATABASE_URL": dsn,
                "TESTER_TEST_ADMIN_DSN": admin_dsn,
                "TEST_RESEARCH_DATABASE_URL": admin_dsn.replace(
                    "dbname=postgres", "dbname=tester_research_test"
                ).replace(" hostaddr=127.0.0.1", ""),
                "RELEASE_SOAK_SECONDS": str(args.soak_seconds),
                "PYTHONPATH": str(source),
                "GUARDIANS_ENABLED": "false",
                "GIT_CONFIG_COUNT": "0",
                "TMP": str(work),
                "TEMP": str(work),
            }
            result_path = work / "workload.json"
            command = [
                sys.executable,
                runner_copy,
                "--child",
                "load",
                "--source",
                source,
                "--result",
                result_path,
                "--api-port",
                args.api_port,
                "--soak-seconds",
                args.soak_seconds,
            ]
            pg_pid = int((data / "postmaster.pid").read_text().splitlines()[0])
            sampled = measured_child(
                command, env=env, cwd=source, output=work / "load.log", pg_pid=pg_pid
            )
            result = json.loads(result_path.read_text())
            result["metrics"].update(sampled)
            with psycopg.connect(admin_dsn) as conn:
                wal_bytes = conn.execute(
                    "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),%s::pg_lsn)", (wal_before,)
                ).fetchone()[0]
                result["metrics"]["wal_bytes"] = int(wal_bytes)
            crash_start = time.perf_counter()
            stop("immediate")
            started = False
            start()
            started = True
            recovery_start_seconds = time.perf_counter() - crash_start
            verify = work / "recovery.json"
            run_command(
                [
                    sys.executable,
                    runner_copy,
                    "--child",
                    "verify",
                    "--source",
                    source,
                    "--result",
                    verify,
                    "--api-port",
                    args.api_port,
                ],
                env=env,
                cwd=source,
                log=work / "verify.log",
            )
            result["recovery"] = json.loads(verify.read_text())
            result["metrics"]["pg_immediate_stop_start_seconds"] = recovery_start_seconds
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/storage/test_postgres.py",
                "tests/platform/test_jobs.py",
                "tests/platform/test_research_postgres.py",
            ]
            test_output = run_command(
                command, cwd=source, env=env, timeout=180, log=work / "existing-pg-tests.log"
            )
            if " skipped" in test_output:
                raise RuntimeError("Selected PostgreSQL tests skipped; cannot claim PASS")
            result.update(
                source_sha=baseline_sha if label == "baseline" else candidate_sha,
                existing_pg_tests="PASS; see exact counters in existing-pg-tests.log",
            )
            write_json(work / "result.json", result)
            results[label] = result
        except Exception as exc:
            write_json(
                work / "failure.json",
                {
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                    "reason": "Inspect command logs; failure does not qualify runtime acceptance",
                },
            )
            raise
        finally:
            if started:
                stop()
            write_json(
                work / "cleanup.json",
                {
                    "stopped": not (data / "postmaster.pid").exists(),
                    "retained_for_evidence": True,
                    "data_directory": str(data),
                },
            )
    comparison = (
        compare_results(results["baseline"], results["candidate"])
        if not args.candidate_only
        else {"status": "NOT RUN", "reason": "candidate compatibility only"}
    )
    write_json(root / "comparison.json", comparison)
    probes = public_probes(args, candidate, root) if args.public_probes else []
    summary = {
        "root": str(root),
        "manifest": manifest,
        "runtime_status": "PASS",
        "public_status": "PASS"
        if probes and all(p["status"] == "PASS" for p in probes)
        else "FAIL"
        if probes
        else "NOT RUN",
        "comparison": comparison,
        "limits": [
            f"measured {args.soak_seconds}-second synthetic soak; not 24-hour acceptance"
            if args.soak_seconds
            else "short synthetic workload, not sustained load",
            "same interpreter for both revisions; production Python ABI transition not covered",
            "localhost API health validates real HTTP+database only; full browser flows separate",
            "no Windows SCM or machine reboot acceptance",
        ],
    }
    write_json(root / "summary.json", summary)
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-repo", type=Path)
    parser.add_argument("--candidate-repo", type=Path)
    parser.add_argument("--baseline-ref", default="e64e30d8369626197403b3962f91bb350656e4c9")
    parser.add_argument("--candidate-ref", default="HEAD")
    parser.add_argument("--pg-bin", type=Path, default=Path("C:/Program Files/PostgreSQL/18/bin"))
    parser.add_argument("--pg-port", type=int, default=55820)
    parser.add_argument("--api-port", type=int, choices=(55828, 55829), default=55828)
    parser.add_argument("--work-root", type=Path, default=Path("work/release-runtime"))
    parser.add_argument("--public-probes", action="store_true")
    parser.add_argument("--candidate-only", action="store_true")
    parser.add_argument("--soak-seconds", type=int, choices=range(0, 601), default=0)
    parser.add_argument("--ca-bundle", type=Path)
    parser.add_argument("--child", choices=("load", "verify"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    if args.child:
        child_workload(args)
    else:
        if not args.baseline_repo or not args.candidate_repo:
            parser.error("--baseline-repo and --candidate-repo are required")
        run_pair(args)


if __name__ == "__main__":
    main()
