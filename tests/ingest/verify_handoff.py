"""Opt-in pinned consumer fixtures; foreign workstream files are read, never edited.

python -m tests.ingest.verify_handoff --references work/handoff-reference
Requires jsonschema and psycopg. Fetch the pinned commits first if absent locally.
"""
import argparse
import gzip
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import inspect
from pathlib import Path
from unittest.mock import Mock

from src.ingest.archive import iter_trade_records
from src.ingest.journal import Journal
from src.ingest.rest import Backfill

CONTRACT_SHA='9766c0093e2608280875ac56db074414a3d62c69'
STORAGE_SHA='5154efab05d44b96f4aa83819672396d75679f2e'
STORAGE_RUN_SHA='47fd36f32389174377682a13862f5b36c7a886da'
URL='https://api.bybit.com/v5/market/kline'

def decimal_fixture(root,second_price='1.00'):
    """Synthetic parser/consumer fixture; these URLs are not availability claims."""
    root.mkdir(parents=True,exist_ok=True); j=Journal(root/'decimal.sqlite')
    run=j.start_run('synthetic_decimal_fixture')
    try:
        for name,price,size in [('first','1.0','2.00'),('second',second_price,'2.0')]:
            path=root/(name+'.csv.gz')
            path.write_bytes(gzip.compress(('timestamp,symbol,side,size,price,trdMatchID\n1704067200.123,BTCUSDT,Buy,'+size+','+price+',decimal-fixture-id\n').encode()))
            records=iter_trade_records(path,'linear','BTCUSDT',{'url':'https://public.bybit.com/trading/BTCUSDT/fixture-'+name+'.csv.gz','ingestion_run':run})
            j.save(name,'observed',{},records,run_id=run)
        j.finish_run(run,'PARTIAL'); j.export(root/'decimal.json')
    finally: j.close()
    return json.loads((root/'decimal.json').read_text())

def verify_decimal_store(dsn,root):
    """Explicitly isolated PG fixture only; no downloads or real trader identities."""
    from src.storage.store import Store
    from src.storage.models import normalize
    bundle=decimal_fixture(root/'equivalent')
    if bundle['conflicts'] or len(bundle['storage_batches'])!=2: raise AssertionError('Equivalent versions must both publish')
    options={'storage_root':root.resolve()} if 'storage_root' in inspect.signature(Store).parameters else {}
    store=Store(dsn,**options)
    for batch in bundle['storage_batches']:
        kwargs={'run_id':batch['ingestion_run']} if 'run_id' in inspect.signature(store.ingest).parameters else {}
        store.ingest(batch['records'],batch['evidence'],batch['batch_id'],batch['checkpoint'],**kwargs)
    rows=store.read('trades','bybit','linear','BTCUSDT',1704067200123,1704067200124,limit=100)
    assert len(rows)==1
    observations=rows[0]['provenance']['observations']
    assert len(observations)==2 and len({o['source_id'] for o in observations})==2
    for batch in bundle['storage_batches']:
        kwargs={'run_id':batch['ingestion_run']} if 'run_id' in inspect.signature(store.ingest).parameters else {}
        store.ingest(batch['records'],batch['evidence'],batch['batch_id'],batch['checkpoint'],**kwargs)
    replay=store.read('trades','bybit','linear','BTCUSDT',1704067200123,1704067200124,limit=100)
    assert len(replay)==1 and len(replay[0]['provenance']['observations'])==2
    changed=decimal_fixture(root/'changed','1.01')
    assert len(changed['conflicts'])==1 and not changed['storage_batches']
    assert len({normalize(r['storage_record'])['content_hash'] for r in changed['records']})==2
    report={'fixture_kind':'synthetic_decimal_parser_and_B_store','economic_executions':1,'source_observations':2,'replay_economic_executions':1,'replay_source_observations':2,'changed_value_conflicts':1,'changed_value_publication_batches':0,'raw_versions_preserved':len(bundle['raw_record_versions']),'raw_source_hashes':len({r['raw_record']['source']['sha256'] for r in bundle['records']})}
    (root/'decimal-store-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

def prepare_references(root,storage_sha=STORAGE_SHA):
    repo=Path(__file__).resolve().parents[2]
    if storage_sha not in {STORAGE_SHA,STORAGE_RUN_SHA}: raise ValueError('Unreviewed storage reference')
    for sha,folder,prefixes in [(CONTRACT_SHA,'master',('contracts/',)),(storage_sha,'storage',('src/storage/','src/quality/'))]:
        resolved=subprocess.check_output(['git','rev-parse',sha],cwd=repo,text=True).strip()
        if resolved!=sha: raise ValueError('Reference SHA mismatch')
        names=subprocess.check_output(['git','ls-tree','-r','--name-only',sha],cwd=repo,text=True).splitlines()
        for name in names:
            if name.startswith(prefixes):
                path=root/folder/name; path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(subprocess.check_output(['git','show',sha+':'+name],cwd=repo))
    sys.path.insert(0,str(root/'master')); sys.path.insert(0,str(root/'storage'))

class PinnedHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validate=staticmethod(importlib.import_module('contracts.v1.validate').validate_record)
        cls.normalize=staticmethod(importlib.import_module('src.storage.models').normalize)
        cls.validate_evidence=staticmethod(importlib.import_module('src.storage.store').validate_evidence)

    def validate_bundle(self,bundle):
        for item in bundle['records']:
            self.assertIn(item['ingestion_run'],bundle['runs'])
            self.assertTrue(item['evidence_refs'])
            for ref in item['evidence_refs']: self.assertIn(ref,bundle['evidence'])
            if item['contract_record'] is not None:
                self.validate(item['contract_kind'],item['contract_record'])
            if item['storage_record'] is not None: self.normalize(item['storage_record'])
            self.assertNotEqual(item['coverage']['status'],'COMPLETE')
        for e in bundle['evidence'].values():
            if e['kind']=='rest_response': self.validate('pagination',e['pagination'])
        for batch in bundle['storage_batches']:
            evidence,count=self.validate_evidence(batch['evidence'])
            self.assertEqual(count,len(batch['records']))
            self.assertIn(evidence['status'],('UNVERIFIED','EMPTY','FAILED'))
            if evidence['status']=='EMPTY': self.assertEqual(count,0)
            manifest=evidence['acquisition_manifest']
            self.assertIn(batch['ingestion_run'],manifest['runs'])
            self.assertTrue(all(ref in manifest['evidence'] for ref in evidence['evidence_refs']))
            for row in batch['records']: self.normalize(row)

    def test_archive_shared_and_B_precision_and_identity_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); path=root/'trades.csv.gz'
            path.write_bytes(gzip.compress(b'timestamp,symbol,side,size,price,trdMatchID\n1704067200,BTCUSDT,Buy,0.100,42000.01,s\n1704067200123,BTCUSDT,Sell,0.200,42000.02,ms\n1704067200.123456,BTCUSDT,Buy,0.300,42000.03,us\n1704067200.123456789,BTCUSDT,Sell,0.400,42000.04,ns\n'))
            j=Journal(root/'journal.sqlite')
            j.save('archive','observed',{},iter_trade_records(path,'linear','BTCUSDT',{'url':'https://public.bybit.com/trading/BTCUSDT/example.csv.gz'}))
            j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text()); self.validate_bundle(bundle)
            rows={r['contract_record']['trade_id']:r for r in bundle['records']}
            self.assertEqual(rows['us']['storage_record']['timestamp_us'],1704067200123456)
            self.assertEqual(rows['us']['contract_record']['timestamp_ns'],'1704067200123456000')
            self.assertEqual(rows['ns']['storage_record']['timestamp_ns'],1704067200123456789)
            self.assertIsNone(rows['ms']['contract_record']['timestamp_ns'])
            self.assertEqual(rows['ns']['raw_record']['payload']['size'],'0.400')

    def test_equivalent_decimal_sources_match_B_economics_but_remain_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); bundle=decimal_fixture(root/'equivalent')
            self.validate_bundle(bundle)
            self.assertFalse(bundle['conflicts'])
            self.assertEqual(len(bundle['records']),2)
            values=[self.normalize(b['records'][0]) for b in bundle['storage_batches']]
            self.assertEqual(len(values),2)
            self.assertEqual(values[0]['record_key'],values[1]['record_key'])
            self.assertEqual(values[0]['content_hash'],values[1]['content_hash'])
            self.assertEqual(len({r['source']['sha256'] for r in values}),2)
            changed=decimal_fixture(root/'changed','1.01')
            self.validate_bundle(changed)
            self.assertEqual(len(changed['conflicts']),1)
            self.assertFalse(changed['storage_batches'])
            self.assertEqual(len({self.normalize(r['storage_record'])['content_hash'] for r in changed['records']}),2)

    def test_rest_named_payload_and_dataset_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'journal.sqlite')
            api=Mock(); api.server_time_ms.return_value=2000000000000
            api.call.return_value=({'list':[['0','1','2','1','2','3','5']]},URL)
            Backfill(api,j).run('linear','BTCUSDT','kline','1',0,60000)
            api.call.return_value=({'list':[{'timestamp':'0','buyRatio':'0.70','sellRatio':'0.30'}]},URL)
            Backfill(api,j).run('linear','BTCUSDT','long_short_ratio','5min',0,300000)
            j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text()); self.validate_bundle(bundle)
            rows={r['storage_record']['dataset']:r['storage_record'] for r in bundle['records']}
            self.assertEqual(rows['candles']['payload']['turnover'],'5')
            self.assertEqual(rows['ratio']['payload']['value'],'0.70')
            self.assertEqual(next(r for r in bundle['records'] if r['storage_record']['dataset']=='ratio')['raw_record']['payload']['sellRatio'],'0.30')

    def test_duplicates_keep_response_counts_separate_from_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'journal.sqlite')
            api=Mock(); api.server_time_ms.return_value=2000000000000
            row=['0','1','1','1','1','2','2']; api.call.return_value=({'list':[row,row]},URL)
            Backfill(api,j).run('linear','BTCUSDT','kline','1',0,60000)
            j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text()); self.validate_bundle(bundle)
            batch=bundle['storage_batches'][0]
            self.assertEqual(batch['evidence']['source_response_count'],2)
            self.assertEqual(batch['evidence']['pages'][0]['count'],1)
            request=next(e for e in bundle['evidence'].values() if e['kind']=='rest_response')
            self.assertIsNone(request['pagination']['cursor_out'])
            self.assertEqual(request['pagination']['terminal_condition'],'EXHAUSTED')
            self.assertEqual(len(request['raw_response']['list']),2)

    def test_empty_and_signed_reference_prices_are_not_faked(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'journal.sqlite')
            api=Mock(); api.server_time_ms.return_value=2000000000000
            api.call.return_value=({'list':[]},URL)
            Backfill(api,j).run('linear','BTCUSDT','funding','',0,60000)
            api.call.return_value=({'list':[['0','-0.1','-0.1','-0.2','-0.1']]},URL)
            Backfill(api,j).run('linear','BTCUSDT','premium','1',0,60000)
            j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text()); self.validate_bundle(bundle)
            item=bundle['records'][0]
            self.assertIsNone(item['contract_record'])
            self.assertTrue(item['projection_errors'])
            self.assertEqual(item['storage_record']['payload']['open'],'-0.1')
            self.assertNotIn('volume',item['storage_record']['payload'])
            self.assertTrue(any(e.get('pagination',{}).get('terminal_condition')=='EMPTY' for e in bundle['evidence'].values()))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--references',type=Path,required=True)
    parser.add_argument('--storage-sha',default=STORAGE_SHA,choices=[STORAGE_SHA,STORAGE_RUN_SHA])
    parser.add_argument('--postgres-dsn',help='Only an explicitly isolated migrated scratch database')
    parser.add_argument('--fixture-output',type=Path)
    args=parser.parse_args(); prepare_references(args.references.resolve(),args.storage_sha)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(PinnedHandoffTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful(): raise SystemExit(1)
    if args.postgres_dsn:
        if args.fixture_output is None: parser.error('--fixture-output required with PostgreSQL')
        print(json.dumps(verify_decimal_store(args.postgres_dsn,args.fixture_output.resolve()),indent=2))

if __name__=='__main__': main()
