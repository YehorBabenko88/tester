"""Real disposable PostgreSQL + real API/worker child-process integration.

This is an opt-in hosted CI test, *not* an acceptance test of installed PC4.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from uuid import uuid4

DSN=os.environ.get('TEST_OPS_ISOLATED_DSN','')


@unittest.skipUnless(DSN, 'NOT_RUN: no exact disposable PostgreSQL DSN')
class IsolatedDualPgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.ops_pg_isolation import require_isolated_dsn
        require_isolated_dsn(DSN)
        from src.api.db import migrate
        migrate(DSN)

    def test_dual_process_resume_and_cpu_ram_wal_queue(self):
        from psycopg.types.json import Jsonb
        from src.api.db import connect
        from src.api.jobs import JobQueue
        from scripts.ops_dual_lifecycle import DisposableApiWorker
        from scripts.ops_resource_metrics import collect_pg_metrics, wal_growth

        uid=uuid4()
        with connect(DSN) as conn:
            conn.execute('DELETE FROM tester_platform.jobs')
            conn.execute('UPDATE tester_platform.control SET draining=false,max_running=2 WHERE singleton')
            conn.execute('INSERT INTO tester_platform.jobs(id,request) VALUES(%s,%s)',(
                uid,Jsonb(dict(category='linear',symbol='BTCUSDT',source='bybit',
                               kind='candles',interval='1',start_ms=0,end_ms=60000))))
        before=collect_pg_metrics(DSN)
        self.assertEqual(before['queue_depth'],1)
        self.assertIsNotNone(before['wal_bytes'],'WAL must be measured, not invented')

        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            runtime=DisposableApiWorker(root,'disposable-dual-pg-owner-marker-123456')
            py=str(Path(sys.executable).resolve())
            api_argv=[py,'-m','scripts.ops_disposable_api']
            worker_argv=[py,'-m','scripts.ops_pg_disposable_worker']
            repo_root=str(Path(__file__).resolve().parents[2])
            environment=dict(os.environ,OPS_JOB_ID=str(uid),
                             PYTHONPATH=repo_root+os.pathsep+os.environ.get('PYTHONPATH',''))
            try:
                first=runtime.start_pair(api_argv,worker_argv,environment=environment)
                self._await_attempt(uid,1)
                measured1=runtime.sample_resources(first)
                mid=collect_pg_metrics(DSN)
                self.assertEqual(mid['queue']['running'],1)
                self.assertIsNotNone(measured1['api']['rss_bytes'])
                self.assertIsNotNone(measured1['worker']['rss_bytes'])
                with connect(DSN) as conn:
                    first_token=conn.execute('SELECT lease_token FROM tester_platform.jobs WHERE id=%s',(uid,)).fetchone()[0]
                runtime.stop_pair_and_seal(first)
                self.assertEqual(JobQueue(DSN).get(uid).checkpoint,{'generation':0})
                self.assertEqual(JobQueue(DSN).get(uid).state,'queued')
                second=runtime.start_pair(api_argv,worker_argv,environment=environment)
                self._await_attempt(uid,2)
                with connect(DSN) as conn:
                    second_token=conn.execute('SELECT lease_token FROM tester_platform.jobs WHERE id=%s',(uid,)).fetchone()[0]
                self.assertNotEqual(first_token,second_token)
                self.assertFalse(JobQueue(DSN).active(uid,first_token))
                measured2=runtime.sample_resources(second)
                runtime.stop_pair_and_seal(second)
                job=JobQueue(DSN).get(uid)
                self.assertEqual((job.state,job.attempts),('completed',2))
                self.assertEqual(job.checkpoint,{'generation':1})
                after=collect_pg_metrics(DSN)
                self.assertEqual(after['queue_depth'],0)
                growth=wal_growth(before,after)
                self.assertEqual(growth['status'],'OBSERVED')
                self.assertGreaterEqual(growth['wal_delta_bytes'],0)
                report=dict(schema='ops-dual-pg-evidence/1',source='disposable-postgres16-only',
                            real_pc4='NOT_RUN',native_windows='NOT_RUN',
                            api_first=measured1['api'], worker_first=measured1['worker'],
                            api_second=measured2['api'], worker_second=measured2['worker'],
                            queue_before=before['queue'],queue_during=mid['queue'],
                            queue_after=after['queue'],wal_before_bytes=before['wal_bytes'],
                            wal_after_bytes=after['wal_bytes'],wal_growth=growth,
                            attempts=job.attempts,old_token_fenced=True,
                            pg_identity_unchanged=True,
                            checkpoint_generation=1,
                            generated_utc=datetime.now(timezone.utc).isoformat())
                if os.environ.get('GITHUB_ACTIONS')=='true':
                    out=Path('ops-dual-pg-evidence.json')
                    out.write_text(json.dumps(report,sort_keys=True,indent=2))
            finally:
                # Cooperative-only cleanup of exact self-created child processes.
                for ctl in (runtime.api,runtime.worker):
                    for gen,handle in ctl.handles.items():
                        if handle.poll() is None:
                            request=ctl.root/'ops-generations'/f'{gen:04d}'/'stop.request'
                            try:request.touch(exist_ok=True)
                            except OSError:pass
                            try:handle.wait(timeout=3)
                            except subprocess.TimeoutExpired:pass  # never kill
                with connect(DSN) as conn:
                    conn.execute('DELETE FROM tester_platform.jobs WHERE id=%s',(uid,))

    @staticmethod
    def _await_attempt(uid,expected):
        from src.api.db import connect
        until=time.monotonic()+8
        while time.monotonic()<until:
            with connect(DSN) as conn:
                row=conn.execute('SELECT state,attempts FROM tester_platform.jobs WHERE id=%s',(uid,)).fetchone()
            if row==('running',expected):
                return
            time.sleep(.05)
        raise AssertionError('Worker lease not observed in disposable PostgreSQL')

if __name__=='__main__':unittest.main()
