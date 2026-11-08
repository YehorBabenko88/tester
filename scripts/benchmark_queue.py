"""Bounded synthetic streaming benchmark; NOT exchange/network throughput acceptance."""

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psutil
from psycopg.conninfo import conninfo_to_dict

from src.api.db import migrate
from src.api.jobs import AdmissionRejected, JobQueue
from src.api.models import DownloadRequest
from src.api.worker import process_one


def benchmark(dsn):
    if conninfo_to_dict(dsn).get("dbname") not in {"tester_acceptance", "tester_ci"}:
        raise ValueError("Synthetic benchmark requires disposable tester_acceptance/tester_ci")
    migrate(dsn)
    queue = JobQueue(dsn, max_pending=4)
    if queue.depth():
        raise ValueError("Synthetic benchmark requires an empty pending queue")
    queue.configure_limits(4, 2)
    queue.set_draining(False)
    process = psutil.Process()
    cpu_start = process.cpu_times()
    peak_rss, peak_queue, submitted, completed = process.memory_info().rss, 0, 0, 0
    timings, admitted = [], {}
    start = time.monotonic()

    class StreamingFixture:
        def run(self, request, checkpoint, report, stop, commit):
            nonlocal peak_rss
            for i in range(256):
                if stop.is_set():
                    raise InterruptedError("cancelled")
                chunk = bytearray(64 * 1024)
                chunk[0] = i % 256
                if i % 16 == 0:
                    peak_rss = max(peak_rss, process.memory_info().rss)
                    if peak_rss > 512 * 1024**2:
                        raise MemoryError("synthetic RSS budget exceeded")
                del chunk
            commit(lambda conn: conn.execute("SELECT 1"), {"synthetic_chunks": 256}, 1.0)
            timings.append(time.monotonic() - admitted[request.symbol])

    try:
        while completed < 10:
            while submitted < 10:
                symbol = f"FIXTURE{submitted}USDT"
                try:
                    queue.create(
                        DownloadRequest(
                            category="linear",
                            symbol=symbol,
                            source="fixture",
                            kind="trades",
                            start_ms=0,
                            end_ms=60000,
                        )
                    )
                except AdmissionRejected:
                    break
                admitted[symbol] = time.monotonic()
                submitted += 1
                peak_queue = max(peak_queue, queue.depth())
            if not process_one(
                queue, StreamingFixture(), threading.Event(), logging.getLogger("fixture")
            ):
                raise RuntimeError("Synthetic queue made no progress")
            completed += 1
        if len(timings) != 10:
            raise RuntimeError("A synthetic handler failed")
    finally:
        queue.configure_limits(256, 2)
    cpu_end = process.cpu_times()
    timings.sort()
    return dict(
        schema_version="1.0",
        synthetic_fixture=True,
        symbols=10,
        completed_jobs=completed,
        peak_queue_depth=peak_queue,
        peak_rss_bytes=peak_rss,
        cpu_seconds=cpu_end.user + cpu_end.system - cpu_start.user - cpu_start.system,
        duration_seconds=time.monotonic() - start,
        p95_queue_to_handler_seconds=timings[9],
        streamed_bytes=10 * 256 * 64 * 1024,
        market_network_bytes=0,
        network_bytes=None,
        unverified=["real_provider_network_backpressure", "hard_CPU_RSS_OS_enforcement"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(benchmark(os.environ["TEST_DATABASE_URL"]), indent=2))
