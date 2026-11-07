import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from src.storage.models import canonical

from tests.storage import test_postgres as support


class ReadPageTests(support.PostgresTests):
    def test_empty_acquisition_evidence_and_run_fanout_are_not_dropped(self):
        ev=self.ev(status='EMPTY',pages=[dict(params={},cursor=None,next_cursor=None,count=0)])
        for prefix in ('empty-a:','empty-b:'): self.store.ingest([],ev,self.symbol,run_id=prefix+self.symbol)
        page=self.page()
        self.assertEqual(page['outcome'],'OK'); self.assertEqual(page['rows'],[])
        self.assertEqual(page['evidence'][0]['status'],'EMPTY')
        self.assertEqual({r['run_id'] for r in page['run_links']},{'empty-a:'+self.symbol,'empty-b:'+self.symbol})
        self.assertFalse(page['coverage']['complete']); self.assertFalse(page['evidence'][0]['active_proof'])
        capped=self.page(max_run_links=1)
        self.assertEqual(capped['outcome'],'RESOURCE_EXHAUSTED'); self.assertEqual(capped['coverage']['proof_refs'],[])

    def page(self,**kwargs):
        self.assertTrue(hasattr(self.store,'read_page'),'bounded consistent read API missing')
        return self.store.read_page('trades','bybit','linear',self.symbol,1700000000000,1700000060000,**kwargs)

    def ingest(self,run='a',trade_id=None,available=True,source_extra=None):
        run=run+':'+self.symbol; record=self.record(); record['source']['ingestion_run']=run
        if trade_id: record['payload']['trade_id']=trade_id
        if source_extra: record['source'].update(source_extra)
        ev=self.ev(ingestion_run=run)
        if available: ev['available_range']=dict(start_ms=ev['start_ms'],end_ms=ev['end_ms'],method='operator_attestation',source_url='https://example.test/manifest',sha256='a'*64)
        return self.store.ingest([record],ev,self.symbol+run,run_id=run),ev

    def test_strict_active_proof_and_same_ms_keyset(self):
        run='a:'+self.symbol
        records=[]
        for tid in ('first','second'):
            raw=self.record(); raw['payload']['trade_id']=tid; raw['source']['ingestion_run']=run; records.append(raw)
        ev=self.ev(ingestion_run=run,pages=[dict(params={},cursor=None,next_cursor=None,count=2)],
            available_range=dict(start_ms=1700000000000,end_ms=1700000060000,method='operator_attestation',source_url='https://example.test/manifest',sha256='a'*64))
        self.store.ingest(records,ev,self.symbol,run_id=run)
        page=self.page(limit=1)
        self.assertEqual(page['outcome'],'OK')
        self.assertFalse(page['page_exhausted']); self.assertIsNotNone(page['next_cursor'])
        self.assertTrue(page['coverage']['complete'])
        self.assertTrue(page['coverage']['proof_refs'])
        tail=self.page(limit=1,after=page['next_cursor'])
        self.assertTrue(tail['page_exhausted'])
        self.assertNotEqual(page['rows'][0]['record_key'],tail['rows'][0]['record_key'])
        self.assertEqual(page['snapshot']['isolation'],'repeatable read')
        self.assertTrue(all(o['source_run_status']=='matched' for o in page['observations']))

    def test_complete_candles_cadence_and_funding_inventory_have_active_proofs(self):
        for dataset,interval,step in [('candles','1',60000),('open_interest','5min',300000),('funding','',300000)]:
            with self.subTest(dataset=dataset):
                run=dataset+':'+self.symbol
                records=[]
                for timestamp in (0,step):
                    payload=dict(open='1',high='1',low='1',close='1',volume='0') if dataset=='candles' else dict(value='0.01')
                    raw=self.record(dataset=dataset,interval=interval,timestamp_ms=timestamp,payload=payload)
                    raw['source']['ingestion_run']=run; records.append(raw)
                available=dict(start_ms=0,end_ms=2*step,method='operator_attestation',source_url='https://example.test/inventory',sha256='a'*64)
                if dataset=='funding': available['expected_timestamps']=[0,step]
                ev=self.ev(dataset=dataset,interval=interval,start_ms=0,end_ms=2*step,ingestion_run=run,available_range=available,
                    pages=[dict(params={},cursor=None,next_cursor=None,count=2)])
                self.store.ingest(records,ev,self.symbol+dataset,run_id=run)
                page=self.store.read_page(dataset,'bybit','linear',self.symbol,0,2*step,interval=interval)
                self.assertEqual(page['outcome'],'OK'); self.assertTrue(page['coverage']['complete'])
                self.assertEqual(len(page['coverage']['proof_refs']),1)

    def test_observation_fanout_has_explicit_hard_failure(self):
        for run in ('a','b','c'): self.ingest(run)
        page=self.page(max_observations=2)
        self.assertEqual(page['outcome'],'RESOURCE_EXHAUSTED')
        self.assertEqual(page['rows'],[]); self.assertEqual(page['coverage']['proof_refs'],[])
        self.assertFalse(page['coverage']['complete'])
        self.assertIn('observations_limit',page['coverage']['reasons'])
        self.assertLessEqual(page['usage']['observations'],2)

    def test_large_single_source_and_evidence_caps_fail_closed(self):
        self.ingest('a',source_extra={'extra':'x'*50000})
        page=self.page(max_bytes=8192)
        self.assertEqual(page['outcome'],'RESOURCE_EXHAUSTED')
        self.assertEqual(page['coverage']['proof_refs'],[])
        self.assertLessEqual(page['usage']['bytes'],8192)
        self.ingest('b')
        page=self.page(max_evidence=1)
        self.assertEqual(page['outcome'],'RESOURCE_EXHAUSTED')

    def test_absent_or_undeclared_sources_do_not_certify_complete(self):
        self.assertFalse(self.page()['coverage']['complete'])
        self.store.ingest([self.record()],self.ev(),self.symbol,run_id='undeclared:'+self.symbol)
        page=self.page()
        self.assertEqual(page['outcome'],'OK')
        self.assertFalse(page['coverage']['complete']); self.assertEqual(page['coverage']['proof_refs'],[])

    def test_active_damage_and_late_valid_resolution_are_honest(self):
        _,ev=self.ingest('a')
        finding=self.store.mark_range(ev,'corrupt','fixture damage')
        bad=self.page(); self.assertFalse(bad['coverage']['complete'])
        run='repair:'+self.symbol; raw=self.record(); raw['source']['ingestion_run']=run
        repaired=dict(ev,ingestion_run=run,resolves=[finding])
        result=self.store.ingest([raw],repaired,self.symbol+'repair',run_id=run)
        good=self.page(); self.assertTrue(good['coverage']['complete'])
        self.assertIn(result['evidence_id'],good['coverage']['proof_refs'])
        self.assertNotIn(finding,good['coverage']['proof_refs'])

    def test_partial_acquisition_is_not_active_proof(self):
        run='partial:'+self.symbol; raw=self.record(); raw['source']['ingestion_run']=run
        self.store.ingest([raw],self.ev(status='PARTIAL',ingestion_run=run),self.symbol,run_id=run)
        page=self.page(); self.assertFalse(page['coverage']['complete'])
        self.assertEqual(page['coverage']['proof_refs'],[])

    def test_limits_are_validated_before_query(self):
        for kwargs in [dict(limit=0),dict(limit=1001),dict(max_observations=10001),dict(max_bytes=8191),
                       dict(max_bytes=16777217),dict(max_evidence=True),dict(after=[1700000000000,'invalid'])]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError): self.page(**kwargs)

    def test_same_snapshot_excludes_later_damage_and_next_read_sees_it(self):
        _,ev=self.ingest('a')
        from src.storage.read_page import Budget
        original=Budget.docs; reached=threading.Event(); release=threading.Event()
        def paused(budget,c,query,params,cap,label):
            yield from original(budget,c,query,params,cap,label)
            if label=='rows':
                reached.set()
                if not release.wait(10): raise RuntimeError('read barrier timeout')
        with patch.object(Budget,'docs',paused),ThreadPoolExecutor(max_workers=1) as pool:
            reading=pool.submit(self.page,statement_timeout_ms=10000)
            self.assertTrue(reached.wait(10))
            try: self.store.mark_range(ev,'corrupt','committed after reader snapshot')
            finally: release.set()
            old=reading.result(timeout=10)
        self.assertTrue(old['coverage']['complete'])
        self.assertFalse(self.page()['coverage']['complete'])

    def test_resolution_after_snapshot_does_not_retroactively_certify_old_read(self):
        _,ev=self.ingest('a')
        finding=self.store.mark_range(ev,'corrupt','late resolution fixture')
        from src.storage.read_page import Budget
        original=Budget.docs; reached=threading.Event(); release=threading.Event()
        def paused(budget,c,query,params,cap,label):
            yield from original(budget,c,query,params,cap,label)
            if label=='rows':
                reached.set()
                if not release.wait(10): raise RuntimeError('read barrier timeout')
        with patch.object(Budget,'docs',paused),ThreadPoolExecutor(max_workers=1) as pool:
            reading=pool.submit(self.page,statement_timeout_ms=10000)
            self.assertTrue(reached.wait(10))
            try:
                run='late-repair:'+self.symbol; raw=self.record(); raw['source']['ingestion_run']=run
                repaired=dict(ev,ingestion_run=run,resolves=[finding])
                self.store.ingest([raw],repaired,self.symbol+'late-repair',run_id=run)
            finally: release.set()
            old=reading.result(timeout=10)
        self.assertFalse(old['coverage']['complete']); self.assertEqual(old['coverage']['proof_refs'],[])
        self.assertTrue(self.page()['coverage']['complete'])

    def test_archive_hash_and_archive_byte_budget_are_checked(self):
        from src.storage.archives import publish_archive
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'download'; path.write_bytes(b'original archive fixture')
            manifest=publish_archive(path,Path(tmp)/'objects')|dict(url='https://example.test/archive',format='binary',row_count=1)
            run='archive:'+self.symbol; raw=self.record(); raw['source'].update(ingestion_run=run,sha256=manifest['sha256'])
            ev=self.ev(ingestion_run=run,available_range=dict(start_ms=1700000000000,end_ms=1700000060000,
                       method='archive_manifest',source_url=manifest['url'],sha256=manifest['sha256']))
            self.store.register_archive(ev,manifest)
            self.store.ingest([raw],ev,self.symbol,run_id=run)
            self.assertTrue(self.page()['coverage']['complete'])
            capped=self.page(max_archive_bytes=0)
            self.assertEqual(capped['outcome'],'RESOURCE_EXHAUSTED')
            self.assertEqual(capped['coverage']['proof_refs'],[])
            self.assertEqual(capped['usage']['archive_bytes'],0)
            Path(manifest['storage_uri']).write_bytes(b'x'*manifest['bytes'])
            corrupted=self.page()
            self.assertFalse(corrupted['coverage']['complete']); self.assertEqual(corrupted['coverage']['proof_refs'],[])

    def test_interval_finding_and_response_byte_bounds_are_explicit(self):
        _,ev=self.ingest('a')
        capped=self.page(max_intervals=1)
        self.assertEqual(capped['outcome'],'RESOURCE_EXHAUSTED')
        self.assertLessEqual(capped['usage']['intervals'],1)
        self.store.mark_range(dict(ev,incident_id='one'),'corrupt','one')
        self.store.mark_range(dict(ev,incident_id='two'),'corrupt','two')
        capped=self.page(max_findings=1,max_bytes=8192)
        self.assertEqual(capped['outcome'],'RESOURCE_EXHAUSTED')
        self.assertLessEqual(len(canonical(capped).encode('utf-8')),8192)
        self.assertLessEqual(capped['usage']['findings'],1)
        self.assertLessEqual(capped['usage']['fetched_bytes'],2*8192)

    def test_new_raw_records_make_old_full_window_proof_stale(self):
        self.ingest('old','first')
        run='new:'+self.symbol; raw=self.record(); raw['payload']['trade_id']='added-later'; raw['source']['ingestion_run']=run
        self.store.ingest([raw],self.ev(ingestion_run=run,status='PARTIAL'),self.symbol+'new',run_id=run)
        page=self.page()
        self.assertFalse(page['coverage']['complete']); self.assertEqual(page['coverage']['proof_refs'],[])

    def test_restoring_archive_bytes_does_not_clear_logical_quarantine(self):
        from src.storage.archives import publish_archive,repair_archive
        from src.storage.lifecycle import StorageLifecycle
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'download'; path.write_bytes(b'original archive fixture')
            manifest=publish_archive(path,Path(tmp)/'objects')|dict(url='https://example.test/quarantine',format='binary',row_count=1)
            run='quarantine:'+self.symbol; raw=self.record(); raw['source'].update(ingestion_run=run,sha256=manifest['sha256'])
            ev=self.ev(ingestion_run=run,available_range=dict(start_ms=1700000000000,end_ms=1700000060000,
                       method='archive_manifest',source_url=manifest['url'],sha256=manifest['sha256']))
            archive_id=self.store.register_archive(ev,manifest)
            self.store.ingest([raw],ev,self.symbol,run_id=run)
            Path(manifest['storage_uri']).write_bytes(b'x'*manifest['bytes'])
            scan=StorageLifecycle(self.store,tmp).scan()
            self.assertTrue(any(f['object_id']==archive_id for f in scan['findings']))
            self.assertTrue(repair_archive(path,manifest))
            page=self.page()
            self.assertFalse(page['coverage']['complete']); self.assertEqual(page['coverage']['proof_refs'],[])

    def test_historical_evidence_run_conflict_cannot_resolve_damage(self):
        from psycopg.types.json import Jsonb
        from src.storage.models import digest
        result,ev=self.ingest('original')
        finding=self.store.mark_range(ev,'corrupt','unresolved damage')
        with self.store.connect() as c:
            p=c.execute('SELECT * FROM tester_storage.pagination_evidence WHERE evidence_id=%s',(result['evidence_id'],)).fetchone()
            bad=dict(p['evidence'],ingestion_run='another-original',resolves=[finding])
            bad_id=digest(bad)
            c.execute('''INSERT INTO tester_storage.pagination_evidence(evidence_id,job_key,scope_key,symbol,start_ms,end_ms,status,record_count,evidence,validator_version,verified)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (bad_id,p['job_key'],p['scope_key'],p['symbol'],p['start_ms'],p['end_ms'],p['status'],p['record_count'],Jsonb(bad),p['validator_version'],True))
            c.execute('''INSERT INTO tester_storage.ingestion_run_evidence SELECT run_id,job_key,%s
                FROM tester_storage.ingestion_run_evidence WHERE evidence_id=%s''',(bad_id,result['evidence_id']))
            old=c.execute('SELECT * FROM tester_storage.source_observations WHERE evidence_id=%s',(result['evidence_id'],)).fetchone()
            c.execute('''INSERT INTO tester_storage.source_observations(observation_id,source_id,run_id,job_key,evidence_id,source_kind,timestamp_precision)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''',(digest([old['observation_id'],bad_id]),old['source_id'],old['run_id'],old['job_key'],bad_id,old['source_kind'],old['timestamp_precision']))
            c.execute('INSERT INTO tester_storage.finding_resolutions(finding_id,evidence_id) VALUES(%s,%s)',(finding,bad_id))
            c.execute('INSERT INTO tester_storage.coverage_resolutions(coverage_id,evidence_id) VALUES(%s,%s)',(finding,bad_id))
        page=self.page()
        self.assertFalse(page['coverage']['complete']); self.assertNotIn(bad_id,page['coverage']['proof_refs'])

    def test_old_evidence_cannot_resolve_a_later_incident(self):
        result,ev=self.ingest('old')
        finding=self.store.mark_range(ev,'corrupt','incident later than proof')
        with self.store.connect() as c:
            c.execute('INSERT INTO tester_storage.finding_resolutions(finding_id,evidence_id) VALUES(%s,%s)',(finding,result['evidence_id']))
            c.execute('INSERT INTO tester_storage.coverage_resolutions(coverage_id,evidence_id) VALUES(%s,%s)',(finding,result['evidence_id']))
        self.assertFalse(self.page()['coverage']['complete'])

    def test_deep_historical_source_does_not_escape_as_recursion_error(self):
        from psycopg.types.json import Jsonb
        from src.storage.models import digest
        result,ev=self.ingest('deep')
        raw=self.record(); raw['source']['ingestion_run']='deep:'+self.symbol
        nested={}
        for _ in range(500): nested={'child':nested}
        raw['source']['extra']=nested
        source=dict(raw['source'],fetched_at='2026-11-07T12:00:00+00:00')
        with self.store.connect() as c:
            old=c.execute('SELECT * FROM tester_storage.source_observations WHERE evidence_id=%s',(result['evidence_id'],)).fetchone()
            record_key=c.execute('SELECT record_key FROM tester_storage.record_sources WHERE source_id=%s',(old['source_id'],)).fetchone()['record_key']
            source_id=digest([record_key,source,raw])
            c.execute('INSERT INTO tester_storage.record_sources VALUES(%s,%s,%s,%s,%s,now())',(source_id,record_key,self.symbol,Jsonb(source),Jsonb(raw)))
            c.execute('''INSERT INTO tester_storage.source_observations(observation_id,source_id,run_id,job_key,evidence_id,source_kind,timestamp_precision)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''',(digest([source_id,old['run_id'],old['job_key'],old['evidence_id']]),
                source_id,old['run_id'],old['job_key'],old['evidence_id'],old['source_kind'],old['timestamp_precision']))
        page=self.page()
        self.assertFalse(page['coverage']['complete']); self.assertEqual(page['coverage']['proof_refs'],[])

    def test_historical_sparse_observation_cannot_reuse_a_true_contiguous_flag(self):
        from psycopg.types.json import Jsonb
        from src.storage.models import digest
        run='sparse:'+self.symbol
        raw=self.record(dataset='open_interest',interval='5min',timestamp_ms=0,payload={'value':'1'})
        raw['source']['ingestion_run']=run
        ev=self.ev(dataset='open_interest',interval='5min',start_ms=0,end_ms=600000,ingestion_run=run,
            available_range=dict(start_ms=0,end_ms=600000,method='operator_attestation',source_url='https://example.test/inventory',sha256='a'*64))
        result=self.store.ingest([raw],ev,self.symbol,run_id=run)
        with self.store.connect() as c:
            p=c.execute('SELECT * FROM tester_storage.pagination_evidence WHERE evidence_id=%s',(result['evidence_id'],)).fetchone()
            bad=dict(p['evidence']); bad['checks']=dict(bad['checks'],contiguous=True)
            bad_id=digest(bad)
            c.execute('''INSERT INTO tester_storage.pagination_evidence(evidence_id,job_key,scope_key,symbol,start_ms,end_ms,status,record_count,evidence,validator_version,verified)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)''',
                (bad_id,p['job_key'],p['scope_key'],p['symbol'],p['start_ms'],p['end_ms'],p['status'],p['record_count'],Jsonb(bad),p['validator_version']))
            c.execute('INSERT INTO tester_storage.ingestion_run_evidence VALUES(%s,%s,%s)',(run,self.symbol,bad_id))
            o=c.execute('SELECT * FROM tester_storage.source_observations WHERE evidence_id=%s',(result['evidence_id'],)).fetchone()
            c.execute('''INSERT INTO tester_storage.source_observations(observation_id,source_id,run_id,job_key,evidence_id,source_kind,timestamp_precision)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''',(digest([o['observation_id'],bad_id]),o['source_id'],run,self.symbol,bad_id,o['source_kind'],o['timestamp_precision']))
            for state in ('available','verified'):
                c.execute('''INSERT INTO tester_storage.coverage_intervals(coverage_id,scope_key,symbol,start_ms,end_ms,state,evidence_id,reason)
                    VALUES(%s,%s,%s,0,600000,%s,%s,'historical false flag')''',(digest([bad_id,state]),p['scope_key'],self.symbol,state,bad_id))
        page=self.store.read_page('open_interest','bybit','linear',self.symbol,0,600000,interval='5min')
        self.assertFalse(page['coverage']['complete']); self.assertEqual(page['coverage']['proof_refs'],[])


for _name in list(support.PostgresTests.__dict__):
    if _name.startswith('test_'): setattr(ReadPageTests,_name,None)
