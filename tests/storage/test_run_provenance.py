import threading
from concurrent.futures import ThreadPoolExecutor
import psycopg
from tests.storage import test_postgres as support


class RunProvenanceTests(support.PostgresTests):
    def test_declared_original_run_matches_caller_and_is_preserved(self):
        run='original:'+self.symbol
        raw=self.record(); raw['source']['ingestion_run']=run
        self.store.ingest([raw],self.ev(),self.symbol,run_id=run)
        row=self.store.read('trades','bybit','linear',self.symbol,1700000000000,1700000060000)[0]
        observation=row['provenance']['observations'][0]
        self.assertEqual(observation['source']['ingestion_run'],run)
        self.assertEqual(observation['raw_envelope']['source']['ingestion_run'],run)
        self.assertEqual(observation['source_run_status'],'matched')
        self.assertTrue(observation['authentic_run'])

    def test_mismatched_original_run_rolls_back_entire_batch_and_checkpoint(self):
        run='original:'+self.symbol
        original=self.record(); original['source']['ingestion_run']=run
        self.store.ingest([original],self.ev(),self.symbol,checkpoint={'sequence':1},run_id=run)
        tables=('raw_records','record_sources','source_observations','pagination_evidence','ingestion_runs',
                'ingestion_run_jobs','ingestion_run_evidence','download_jobs','audit_events','coverage_intervals','ingestion_checkpoints')
        def snapshot():
            with self.store.connect() as c:
                return {t:c.execute('SELECT count(*) AS n FROM tester_storage.'+t).fetchone()['n'] for t in tables}
        before=snapshot()
        first=self.record(); first['payload']['trade_id']='new-good'; first['source']['ingestion_run']=run
        bad=self.record(); bad['payload']['trade_id']='new-bad'; bad['source']['ingestion_run']='another-original'
        ev=self.ev(pages=[dict(params={},cursor=None,next_cursor=None,count=2)])
        with self.assertRaisesRegex(ValueError,'ingestion_run'):
            self.store.ingest([first,bad],ev,self.symbol,checkpoint={'sequence':2},run_id=run)
        self.assertEqual(snapshot(),before)
        self.assertEqual(self.store.checkpoint(self.symbol),{'sequence':1})
        self.assertEqual(self.count('raw_records'),1)
        # Fresh jobs/runs must also leave no rows behind when rejected.
        with self.assertRaises(ValueError): self.store.ingest([bad],self.ev(),self.symbol+'rejected',run_id='publication:'+self.symbol)
        self.assertEqual(snapshot(),before)

    def test_source_run_declaration_requires_valid_explicit_caller(self):
        for declaration in (None,'',True,123,'x'*301,'original'):
            raw=self.record(); raw['source']['ingestion_run']=declaration
            with self.assertRaises(ValueError): self.store.ingest([raw],self.ev(),self.symbol)
        self.assertEqual(self.count('raw_records'),0)

    def test_undeclared_original_run_is_explicit_in_resolver(self):
        self.store.ingest([self.record()],self.ev(),self.symbol,run_id='caller:'+self.symbol)
        row=self.store.read('trades','bybit','linear',self.symbol,1700000000000,1700000060000)[0]
        observation=row['provenance']['observations'][0]
        self.assertEqual(observation['source_run_status'],'undeclared')
        self.assertIsNone(observation['source_ingestion_run'])
        self.assertNotIn('ingestion_run',observation['source'])

    def test_historical_mismatch_is_not_reported_authentic_or_rewritten(self):
        from src.storage.models import normalize,digest
        from psycopg.types.json import Jsonb
        run='publication:'+self.symbol
        result=self.store.ingest([self.record()],self.ev(),self.symbol,run_id=run)
        raw=self.record(); raw['source']['ingestion_run']='historical-original'
        n=normalize(raw); source_id=digest([n['record_key'],n['source'],raw])
        # Fixture models a row accepted by pre-fix47fd; normal ingest now rejects it.
        with self.store.connect() as c:
            c.execute('INSERT INTO tester_storage.record_sources VALUES(%s,%s,%s,%s,%s,now())',
                (source_id,n['record_key'],self.symbol,Jsonb(n['source']),Jsonb(raw)))
            c.execute('''INSERT INTO tester_storage.source_observations
                (observation_id,source_id,run_id,job_key,evidence_id,source_kind,timestamp_precision)
                VALUES(%s,%s,%s,%s,%s,'archive','ms')''',
                (digest([source_id,run,self.symbol,result['evidence_id']]),source_id,run,self.symbol,result['evidence_id']))
        proof=self.store.provenance([n['record_key']])[n['record_key']]
        old=next(o for o in proof['observations'] if o['source_id']==source_id)
        self.assertFalse(proof['authentic']); self.assertFalse(old['authentic_run'])
        self.assertEqual(old['source_run_status'],'mismatched')
        self.assertEqual(old['source_ingestion_run'],'historical-original')
        self.assertEqual(old['source']['ingestion_run'],'historical-original')

    def test_recovery_preserves_explicit_acquisition_run_from_evidence(self):
        run='acquisition:'+self.symbol
        ev=self.ev(ingestion_run=run,available_range=dict(start_ms=1700000000000,end_ms=1700000060000,
                 method='operator_attestation',source_url='https://example.test/range',sha256='a'*64))
        self.store.mark_range(ev,'corrupt','declared acquisition repair')
        raw=self.record(); raw['source']['ingestion_run']=run
        def fetch(job): return [raw],ev,{'sequence':1}
        result=self.store.recover(fetch,'bybit','linear',self.symbol,'trades',retry_delay_ms=0)
        self.assertEqual(result[0]['state'],'verified')
        row=self.store.read('trades','bybit','linear',self.symbol,ev['start_ms'],ev['end_ms'])[0]
        observation=row['provenance']['observations'][0]
        self.assertEqual(observation['run_id'],run)
        self.assertEqual(observation['source_run_status'],'matched')

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
