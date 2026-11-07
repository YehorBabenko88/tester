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
from pathlib import Path
from unittest.mock import Mock

from src.ingest.archive import iter_trade_records
from src.ingest.journal import Journal
from src.ingest.rest import Backfill

CONTRACT_SHA='9766c0093e2608280875ac56db074414a3d62c69'
STORAGE_SHA='5154efab05d44b96f4aa83819672396d75679f2e'
STORAGE_RUN_SHA='47fd36f32389174377682a13862f5b36c7a886da'
URL='https://api.bybit.com/v5/market/kline'

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
    args=parser.parse_args(); prepare_references(args.references.resolve(),args.storage_sha)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(PinnedHandoffTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful(): raise SystemExit(1)

if __name__=='__main__': main()
