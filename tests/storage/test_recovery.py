import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
from psycopg import sql
from src.storage.store import scope_key
from tests.storage import test_postgres as support


class RecoveryTests(support.PostgresTests):
    def worker(self,mode):
        env=os.environ.copy()
        env.update(TESTER_CRASH_DSN=self.dsn,TESTER_CRASH_SYMBOL=self.symbol,TESTER_CRASH_JOB=self.symbol)
        return subprocess.Popen([sys.executable,'-m','tests.storage.crash_worker',mode],env=env,
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)

    def line(self,process):
        q=queue.Queue()
        threading.Thread(target=lambda:q.put(process.stdout.readline().strip()),daemon=True).start()
        try: return q.get(timeout=10)
        except queue.Empty:
            process.kill(); process.wait(timeout=10)
            self.fail('worker did not reach expected transaction boundary')

    def test_kill_before_commit_rolls_back_and_replay_preserves_confirmed_data(self):
        self.store.ingest([self.record()],self.ev(),self.symbol,checkpoint={'offset':1})
        p=self.worker('before')
        try:
            self.assertEqual(self.line(p),'uncommitted')
            p.kill(); p.wait(timeout=10)
            self.assertEqual(self.count('raw_records'),1)
            self.assertEqual(self.store.checkpoint(self.symbol),{'offset':1})
            self.assertEqual(self.count('pagination_evidence'),1)
            r=self.record(); r['payload']['trade_id']='second-confirmed-id'
            self.store.ingest([r],self.ev(),self.symbol,checkpoint={'offset':2})
            self.assertEqual(self.count('raw_records'),2)
            self.assertEqual(self.store.checkpoint(self.symbol),{'offset':2})
        finally:
            if p.poll() is None: p.kill()
            p.communicate(timeout=10)

    def test_kill_after_commit_retains_raw_provenance_and_checkpoint(self):
        self.store.ingest([self.record()],self.ev(),self.symbol,checkpoint={'offset':1})
        p=self.worker('after')
        try:
            self.assertEqual(self.line(p),'started'); self.assertEqual(self.line(p),'committed')
            p.kill(); p.wait(timeout=10)
            self.assertEqual(self.count('raw_records'),2)
            self.assertEqual(self.count('record_sources'),2)
            self.assertEqual(self.store.checkpoint(self.symbol),{'offset':2})
            r=self.record(); r['payload']['trade_id']='second-confirmed-id'
            self.store.ingest([r],self.ev(),self.symbol,checkpoint={'offset':2})
            self.assertEqual(self.count('raw_records'),2)
        finally:
            if p.poll() is None: p.kill()
            p.communicate(timeout=10)

    def test_concurrent_writer_waits_for_same_dataset_transaction(self):
        self.store.ingest([self.record()],self.ev(),self.symbol)
        key=scope_key(self.ev()); p=None
        try:
            with psycopg.connect(self.dsn) as c:
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tester_storage:'+key,))
                p=self.worker('concurrent'); self.assertEqual(self.line(p),'started')
                deadline=time.monotonic()+10; blocked=False
                while time.monotonic()<deadline:
                    with psycopg.connect(self.dsn) as observer:
                        blocked=observer.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event='advisory')").fetchone()[0]
                    if blocked: break
                    time.sleep(0.02)
                self.assertTrue(blocked,'writer did not acquire the documented conflicting task lock')
                self.assertEqual(self.count('raw_records'),1)
            self.assertEqual(self.line(p),'committed')
            self.assertEqual(p.wait(timeout=10),0)
            self.assertEqual(self.count('raw_records'),2)
        finally:
            if p:
                if p.poll() is None: p.kill()
                p.communicate(timeout=10)

    def test_backup_restore_into_new_database_preserves_data_and_migrations(self):
        self.assertIsNotNone(shutil.which('pg_dump'),'pg_dump required for acceptance')
        self.assertIsNotNone(shutil.which('pg_restore'),'pg_restore required for acceptance')
        evidence=self.store.ingest([self.record()],self.ev(),self.symbol,checkpoint={'offset':1},run_id='backup-run:'+self.symbol)
        with self.store.connect() as c:
            cutoff_ms=int(c.execute('SELECT extract(epoch FROM updated_at)*1000 AS ms FROM tester_storage.download_jobs WHERE job_key=%s',(self.symbol,)).fetchone()['ms'])
        restored='tester_b_restore_'+uuid.uuid4().hex[:12]
        env=os.environ.copy(); params=psycopg.conninfo.conninfo_to_dict(self.dsn)
        for k,target in [('host','PGHOST'),('port','PGPORT'),('user','PGUSER'),('password','PGPASSWORD')]:
            if k in params: env[target]=params[k]
        with tempfile.TemporaryDirectory() as tmp:
            from src.storage.lifecycle import StorageLifecycle
            lifecycle=StorageLifecycle(self.store,tmp)
            backup=str(Path(tmp)/'backup.dump')
            backup_start_ms=time.time_ns()//1000000
            with ThreadPoolExecutor(max_workers=1) as pool:
                scan=pool.submit(lifecycle.scan)
                subprocess.run(['pg_dump','--format=custom','--no-owner','--no-privileges','--file',backup,'--dbname',self.database],env=env,check=True,capture_output=True,timeout=30)
                scan_report=scan.result(timeout=10)
                self.assertFalse(scan_report['truncated'])
            backup_end_ms=time.time_ns()//1000000
            try:
                with psycopg.connect(self.admin,autocommit=True) as c:
                    c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restored)))
                restore_start_ms=time.time_ns()//1000000
                subprocess.run(['pg_restore','--exit-on-error','--no-owner','--no-privileges','--dbname',restored,backup],env=env,check=True,capture_output=True,timeout=30)
                with psycopg.connect(psycopg.conninfo.make_conninfo(self.admin,dbname=restored)) as c:
                    support.migrate(c)
                    row=c.execute('SELECT payload FROM tester_storage.raw_records WHERE symbol=%s',(self.symbol,)).fetchone()
                    self.assertEqual(row[0]['price'],'123.000000000000000001')
                    self.assertEqual(c.execute('SELECT checkpoint FROM tester_storage.ingestion_checkpoints WHERE job_key=%s',(self.symbol,)).fetchone()[0],{'offset':1})
                    self.assertEqual(c.execute('SELECT count(*) FROM tester_storage.record_sources WHERE symbol=%s',(self.symbol,)).fetchone()[0],1)
                    self.assertEqual(c.execute('SELECT count(*) FROM tester_storage.source_observations o JOIN tester_storage.record_sources s USING(source_id) WHERE s.symbol=%s',(self.symbol,)).fetchone()[0],1)
                restore_end_ms=time.time_ns()//1000000
                report=lifecycle.record_backup('measured:'+self.symbol,backup,[evidence['evidence_id']],restored=True,
                    data_cutoff_ms=cutoff_ms,backup_start_ms=backup_start_ms,backup_end_ms=backup_end_ms,
                    restore_start_ms=restore_start_ms,restore_end_ms=restore_end_ms)
                print('MEASURED_RESTORE',{'rpo_ms':report['rpo_ms'],'rto_ms':report['rto_ms'],'dump_bytes':report['manifest']['bytes']})
            finally:
                with psycopg.connect(self.admin,autocommit=True) as c:
                    c.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(restored)))


for _name in list(support.PostgresTests.__dict__):
    if _name.startswith('test_'): setattr(RecoveryTests,_name,None)
