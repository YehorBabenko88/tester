import threading
from concurrent.futures import ThreadPoolExecutor
import psycopg
from tests.storage import test_postgres as support


class RunProvenanceTests(support.PostgresTests):
    def test_empty_runs_keep_independent_run_evidence_links(self):
        ev=self.ev(status='EMPTY',pages=[dict(params={},cursor=None,next_cursor=None,count=0)])
        self.store.ingest([],ev,self.symbol,run_id='empty-a:'+self.symbol)
        self.store.ingest([],ev,self.symbol,run_id='empty-b:'+self.symbol)
        rows=self.store.coverage_evidence('bybit','linear',self.symbol,'trades','',ev['start_ms'],ev['end_ms'])
        self.assertEqual(len(rows),1)
        self.assertEqual(set(rows[0]['run_ids']),{'empty-a:'+self.symbol,'empty-b:'+self.symbol})
        self.assertFalse(rows[0]['verified'])

    def test_explicit_run_rejects_missing_source_kind_and_read_never_creates_run(self):
        raw=self.record(); raw['source'].pop('source_kind')
        with self.assertRaises(ValueError): self.store.ingest([raw],self.ev(),self.symbol,run_id='missing:'+self.symbol)
        self.assertEqual(self.count('raw_records'),0)
        self.assertEqual(self.store.provenance(['a'*64])['a'*64]['run_ids'],[])
        with self.store.connect() as c:
            self.assertIsNone(c.execute('SELECT 1 FROM tester_storage.ingestion_runs WHERE run_id=%s',('missing:'+self.symbol,)).fetchone())

    def test_identical_source_is_observed_in_two_authentic_runs(self):
        a=self.store.ingest([self.record()],self.ev(),self.symbol+'a',run_id='run-a:'+self.symbol)
        b=self.store.ingest([self.record()],self.ev(),self.symbol+'b',run_id='run-b:'+self.symbol)
        self.store.ingest([self.record()],self.ev(),self.symbol+'b',run_id='run-b:'+self.symbol)
        rows=self.store.read('trades','bybit','linear',self.symbol,1700000000000,1700000060000)
        provenance=self.store.provenance([rows[0]['record_key']])[rows[0]['record_key']]
        self.assertEqual(len(provenance['observations']),2)
        self.assertEqual(set(provenance['evidence_refs']),{a['evidence_id'],b['evidence_id']})
        self.assertEqual({o['run_id'] for o in provenance['observations']},{'run-a:'+self.symbol,'run-b:'+self.symbol})
        self.assertTrue(all(o['authentic_run'] for o in provenance['observations']))
        self.assertTrue(all(o['timestamp_precision']=='ms' for o in provenance['observations']))
        self.assertEqual(len(rows[0]['provenance']['observations']),2)
        self.assertTrue(provenance['authentic'])
        self.assertEqual({o['source_kind'] for o in provenance['observations']},{'archive'})
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) AS n FROM tester_storage.raw_records WHERE symbol=%s',(self.symbol,)).fetchone()['n'],1)
            self.assertEqual(c.execute('SELECT count(*) AS n FROM tester_storage.ingestion_runs WHERE run_id LIKE %s',('%'+self.symbol,)).fetchone()['n'],2)

    def test_only_one_concurrent_recovery_fetch_is_claimed(self):
        ev=self.ev(); self.store.mark_range(ev,'corrupt','claim fixture')
        started=threading.Event(); release=threading.Event(); calls=[]
        def fetch(job):
            calls.append(job['job_key']); started.set()
            if not release.wait(10): raise RuntimeError('test barrier timeout')
            raise OSError('fixture failure')
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.store.recover,fetch,'bybit','linear',self.symbol,'trades')
            self.assertTrue(started.wait(10))
            try:
                second=pool.submit(self.store.recover,fetch,'bybit','linear',self.symbol,'trades')
                self.assertEqual(second.result(timeout=3),[])
            finally: release.set()
            first.result(timeout=10)
        self.assertEqual(len(calls),1)

    def test_lost_claim_late_failure_cannot_revert_verified_success(self):
        ev=self.ev(available_range=dict(start_ms=1700000000000,end_ms=1700000060000,
                 method='operator_attestation',source_url='https://example.test/range',sha256='a'*64))
        self.store.mark_range(ev,'corrupt','late failure fixture')
        started=threading.Event(); release=threading.Event()
        def delayed_failure(job):
            started.set()
            if not release.wait(10): raise RuntimeError('test barrier timeout')
            raise OSError('old transport failed')
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.store.recover,delayed_failure,'bybit','linear',self.symbol,'trades')
            self.assertTrue(started.wait(10))
            try:
                with self.store.connect() as c:
                    job=c.execute('SELECT job_key,attempt_token FROM tester_storage.download_jobs WHERE symbol=%s',(self.symbol,)).fetchone()
                    terminated=c.execute("SELECT pg_terminate_backend(pid) AS killed FROM pg_stat_activity WHERE application_name=%s",('tester_recovery:'+job['attempt_token'],)).fetchall()
                    self.assertTrue(terminated)
                def success(job): return [self.record()],ev,{'sequence':1}
                second=self.store.recover(success,'bybit','linear',self.symbol,'trades')
                self.assertEqual(second[0]['state'],'verified')
            finally: release.set()
            self.assertEqual(first.result(timeout=10)[0]['state'],'superseded')
        with self.store.connect() as c:
            row=c.execute('SELECT state,last_error_code FROM tester_storage.download_jobs WHERE job_key=%s',(job['job_key'],)).fetchone()
            self.assertEqual(row['state'],'committed'); self.assertIsNone(row['last_error_code'])


for _name in list(support.PostgresTests.__dict__):
    if _name.startswith('test_'): setattr(RunProvenanceTests,_name,None)
