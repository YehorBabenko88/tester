import hashlib
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from tests.storage import test_postgres as support

try:
    from src.storage.lifecycle import StorageLifecycle
except ImportError:
    StorageLifecycle=None


class LifecycleTests(support.PostgresTests):
    def lifecycle(self,root):
        self.assertIsNotNone(StorageLifecycle,'dry-run lifecycle is not implemented')
        return StorageLifecycle(self.store,root)

    def test_raw_references_and_backup_gates_never_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle=self.lifecycle(tmp)
            result=self.store.ingest([self.record()],self.ev(),self.symbol,run_id='run:'+self.symbol)
            refs=[result['evidence_id']]
            for name,kind in [('raw','RAW_PROTECTED'),('archive','VERIFIED_ARCHIVE'),('cache','CACHE'),('derived','DERIVED_REBUILDABLE'),('temp','TEMP')]:
                path=Path(tmp)/name; path.write_bytes(name.encode())
                lifecycle.register_object(self.symbol+name,kind,path,refs)
            lifecycle.reference_object(self.symbol+'raw',self.symbol+'cache',refs)
            first=lifecycle.plan()
            by_id={x['object_id']:x for x in first['objects']}
            self.assertEqual(by_id[self.symbol+'raw']['reason'],'raw_protected')
            self.assertEqual(by_id[self.symbol+'archive']['reason'],'archive_protected')
            self.assertEqual(by_id[self.symbol+'cache']['reason'],'referenced')
            self.assertEqual(by_id[self.symbol+'derived']['reason'],'missing_verified_backup')
            self.assertTrue(all(not x['eligible'] for x in first['objects']))
            backup=Path(tmp)/'backup'; backup.write_bytes(b'synthetic backup fixture')
            lifecycle.record_backup('backup:'+self.symbol,backup,refs,restored=False,
                data_cutoff_ms=1000,backup_start_ms=1001,backup_end_ms=1002,restore_start_ms=1003,restore_end_ms=1004)
            self.assertFalse(any(x['eligible'] for x in lifecycle.plan()['objects']))
            lifecycle.record_backup('verified:'+self.symbol,backup,refs,restored=True,
                data_cutoff_ms=1000,backup_start_ms=1001,backup_end_ms=1002,restore_start_ms=1003,restore_end_ms=1004)
            final=lifecycle.plan(); by_id={x['object_id']:x for x in final['objects']}
            self.assertTrue(by_id[self.symbol+'derived']['eligible'])
            self.assertEqual(final['mode'],'DRY_RUN')
            self.assertEqual(lifecycle.plan()['plan_id'],final['plan_id'])
            with self.assertRaises(PermissionError): lifecycle.execute(final['plan_id'])
            self.assertEqual(self.count('raw_records'),1)
            self.assertTrue((Path(tmp)/'raw').exists())

    def test_protected_path_cannot_be_reclassified_via_another_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle=self.lifecycle(tmp)
            result=self.store.ingest([self.record()],self.ev(),self.symbol,run_id='run:'+self.symbol)
            path=Path(tmp)/'raw'; path.write_bytes(b'protected raw')
            lifecycle.register_object(self.symbol+'raw','RAW_PROTECTED',path,[result['evidence_id']])
            with self.assertRaises(ValueError): lifecycle.register_object(self.symbol+'alias','CACHE',path,[result['evidence_id']])
            self.assertFalse(any(o['eligible'] for o in lifecycle.plan()['objects']))

    def test_completed_backup_cannot_acquire_later_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle=self.lifecycle(tmp)
            first=self.store.ingest([self.record()],self.ev(),self.symbol+'a',run_id='a:'+self.symbol)
            second=self.store.ingest([self.record()],self.ev(),self.symbol+'b',run_id='b:'+self.symbol)
            path=Path(tmp)/'backup'; path.write_bytes(b'actual old backup fixture')
            kwargs=dict(restored=True,data_cutoff_ms=1,backup_start_ms=2,backup_end_ms=3,restore_start_ms=4,restore_end_ms=5)
            lifecycle.record_backup(self.symbol,path,[first['evidence_id']],**kwargs)
            with self.assertRaises(ValueError): lifecycle.record_backup(self.symbol,path,[first['evidence_id'],second['evidence_id']],**kwargs)

    def test_low_disk_pauses_acquisition_before_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle=self.lifecycle(tmp)
            with patch('src.storage.lifecycle.shutil.disk_usage',return_value=(1000,999,1)):
                gate=lifecycle.acquisition_gate(min_free_bytes=100)
                self.assertEqual(gate['state'],'PAUSED_LOW_DISK')
                calls=[]
                result=lifecycle.guarded_fetch(lambda:calls.append('called'),min_free_bytes=100)
                self.assertEqual(result['state'],'PAUSED_LOW_DISK'); self.assertEqual(calls,[])
                from src.storage.store import Store,AcquisitionPaused
                guarded=Store(self.dsn,storage_root=tmp,min_free_bytes=100)
                self.store.mark_range(self.ev(),'corrupt','disk fixture')
                result=guarded.recover(lambda job:calls.append('called'),'bybit','linear',self.symbol,'trades')
                self.assertEqual(result[0]['state'],'paused_low_disk'); self.assertEqual(calls,[])
                with self.assertRaises(AcquisitionPaused): guarded.ingest([self.record()],self.ev(),self.symbol)

    def test_corruption_scan_quarantines_without_changing_raw_during_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle=self.lifecycle(tmp)
            result=self.store.ingest([self.record()],self.ev(),self.symbol,run_id='run:'+self.symbol)
            path=Path(tmp)/'cache'; path.write_bytes(b'original')
            lifecycle.register_object(self.symbol+'cache','CACHE',path,[result['evidence_id']])
            path.write_bytes(b'tampered')
            with ThreadPoolExecutor(max_workers=2) as pool:
                scan=pool.submit(lifecycle.scan)
                read=pool.submit(self.store.read,'trades','bybit','linear',self.symbol,1700000000000,1700000060000)
                report=scan.result(timeout=10); rows=read.result(timeout=10)
            self.assertEqual(len(rows),1)
            self.assertTrue(any(f['code']=='object_checksum_mismatch' for f in report['findings']))
            self.assertEqual(self.count('raw_records'),1)
            self.assertTrue(path.exists())
            self.assertEqual(next(x for x in lifecycle.plan()['objects'] if x['object_id']==self.symbol+'cache')['reason'],'quarantined')
            metrics=lifecycle.metrics()
            self.assertTrue(any(t['table']=='raw_records' and t['bytes']>0 for t in metrics['tables']))
            self.assertIn('wal_bytes',metrics); self.assertIn('free_bytes',metrics)
            self.assertEqual(metrics['last_scan']['scan_id'],report['scan_id'])
            print('LIFECYCLE_METRICS',dict(raw_table_bytes=next(t['bytes'] for t in metrics['tables'] if t['table']=='raw_records'),
                  wal_bytes=metrics['wal_bytes'],free_bytes=metrics['free_bytes'],scan_elapsed_ms=report['elapsed_ms'],findings=len(report['findings'])))

    def test_scan_detects_forged_raw_content_and_missing_source_without_repairing_it(self):
        from src.storage.models import normalize,digest
        from psycopg.types.json import Jsonb
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle=self.lifecycle(tmp); raw=self.record(); n=normalize(raw)
            forged=dict(n['payload'],price='999')
            with self.store.connect() as c:
                c.execute('INSERT INTO tester_storage.instruments(venue,category,symbol) VALUES(%s,%s,%s)',('bybit','linear',self.symbol))
                c.execute('''INSERT INTO tester_storage.raw_records(record_key,venue,category,symbol,dataset,interval,schema_version,
                    timestamp_ms,timestamp_precision,trade_id,id_origin,payload,content_hash)
                    VALUES(%s,'bybit','linear',%s,'trades','','1.0',%s,'ms',%s,'exchange',%s,%s)''',
                    (n['record_key'],self.symbol,n['timestamp_ms'],n['trade_id'],Jsonb(forged),n['content_hash']))
            self.assertTrue(any(f['code']=='raw_without_source' for f in lifecycle.scan()['findings']))
            with self.store.connect() as c:
                c.execute('INSERT INTO tester_storage.record_sources VALUES(%s,%s,%s,%s,%s,now())',
                    (digest([n['record_key'],n['source'],raw]),n['record_key'],self.symbol,Jsonb(n['source']),Jsonb(raw)))
            codes={f['code'] for f in lifecycle.scan()['findings']}
            self.assertIn('raw_identity_or_content_mismatch',codes)
            self.assertIn('legacy_source_without_run_evidence',codes)
            rows=self.store.read('trades','bybit','linear',self.symbol,1700000000000,1700000060000)
            self.assertEqual(rows[0]['payload']['price'],'999') # scan does not alter or delete the fixture


for _name in list(support.PostgresTests.__dict__):
    if _name.startswith('test_'): setattr(LifecycleTests,_name,None)
