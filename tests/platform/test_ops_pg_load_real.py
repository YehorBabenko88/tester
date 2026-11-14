"""Bounded ten-symbol disposable PostgreSQL queue contention and resource receipt.

This is actual PG transaction load, not historical Bybit data or sustained market load.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import unittest
from uuid import uuid4

DSN = os.environ.get('TEST_OPS_ISOLATED_DSN', '')
SYMBOLS = tuple(f'OPS{i:02d}USDT' for i in range(10))


@unittest.skipUnless(DSN, 'NOT_RUN: exact disposable PostgreSQL unavailable')
class IsolatedQueueContention(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.ops_pg_isolation import require_isolated_dsn
        from src.api.db import migrate
        require_isolated_dsn(DSN)
        migrate(DSN)

    def test_ten_symbols_fifty_jobs_fenced_load(self):
        import psutil
        from psycopg.types.json import Jsonb
        from src.api.db import connect
        from src.api.jobs import JobQueue
        from scripts.ops_resource_metrics import collect_pg_metrics, wal_growth

        ids = [uuid4() for _ in range(50)]
        with connect(DSN) as conn:
            conn.execute('DELETE FROM tester_platform.jobs')
            conn.execute('UPDATE tester_platform.control SET draining=false,max_pending=256,max_running=4 WHERE singleton')
            for n, job_id in enumerate(ids):
                request = dict(category='linear', symbol=SYMBOLS[n % 10], source='bybit',
                               kind='candles', interval='1', start_ms=0, end_ms=60000)
                conn.execute('INSERT INTO tester_platform.jobs(id,request) VALUES(%s,%s)',
                             (job_id, Jsonb(request)))
        before = collect_pg_metrics(DSN)
        self.assertEqual(before['queue_depth'], 50)
        self.assertIsNotNone(before['wal_bytes'])
        process = psutil.Process(os.getpid())
        rss_before = process.memory_info().rss
        started = time.monotonic()
        deadline = started + 35

        def consume():
            queue = JobQueue(DSN, lease_seconds=30)
            completed = []
            while time.monotonic() < deadline:
                lease = queue.claim()
                if lease is None:
                    if queue.depth() == 0:
                        return completed
                    time.sleep(0.01)
                    continue
                queue.checkpoint(lease.id, lease.lease_token,
                                 {'symbol': lease.request.symbol}, 0.5)
                queue.finish(lease.id, lease.lease_token)
                completed.append(str(lease.id))
            raise AssertionError('Disposable queue did not drain before deadline')

        try:
            with ThreadPoolExecutor(max_workers=4) as executor:
                parts = list(executor.map(lambda _: consume(), range(4)))
            finished = [job for part in parts for job in part]
            elapsed = time.monotonic() - started
            self.assertEqual(len(finished), 50)
            self.assertEqual(len(set(finished)), 50)
            self.assertEqual(set(finished), {str(uid) for uid in ids})
            with connect(DSN) as conn:
                rows = conn.execute('SELECT state,attempts,request->>\'symbol\' FROM tester_platform.jobs').fetchall()
            self.assertEqual(len(rows), 50)
            self.assertTrue(all(state == 'completed' and attempts == 1 for state, attempts, _ in rows))
            self.assertEqual({row[2] for row in rows}, set(SYMBOLS))
            after = collect_pg_metrics(DSN)
            growth = wal_growth(before, after)
            self.assertEqual(after['queue_depth'], 0)
            self.assertEqual(growth['status'], 'OBSERVED')
            self.assertIsNotNone(growth['wal_delta_bytes'])
            io = process.io_counters()
            report = dict(schema='ops-disposable-queue-load/1',
                          tested_symbols=10, jobs=50, worker_threads=4,
                          duration_s=elapsed, jobs_per_s=50/elapsed,
                          rss_before_bytes=rss_before, rss_after_bytes=process.memory_info().rss,
                          cpu_percent=process.cpu_percent(interval=0.1),
                          read_bytes=io.read_bytes, write_bytes=io.write_bytes,
                          wal_growth_bytes=growth['wal_delta_bytes'],
                          queue_before=before['queue'], queue_after=after['queue'],
                          duplicate_claims=0, attempts_per_job=1,
                          generated_utc=datetime.now(timezone.utc).isoformat(),
                          real_bybit='NOT_RUN', ten_symbol_market_sustained='NOT_RUN',
                          native_pc4='NOT_RUN', limits_enforced=False)
            if os.environ.get('GITHUB_ACTIONS') == 'true':
                Path('ops-load-pg-evidence.json').write_text(json.dumps(report, indent=2, sort_keys=True))
        finally:
            with connect(DSN) as conn:
                conn.execute('DELETE FROM tester_platform.jobs WHERE id=ANY(%s)', (ids,))


if __name__ == '__main__':
    unittest.main()
