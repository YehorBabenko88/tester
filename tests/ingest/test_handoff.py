import gzip
import base64
import json
import tempfile
import unittest
import requests
from decimal import localcontext
from pathlib import Path
from unittest.mock import Mock

from src.ingest.archive import iter_trade_records
from src.ingest.journal import Journal
from src.ingest.rest import Backfill
from src.ingest.handoff import semantic_hash
from tests.ingest.test_pipeline import response

URL='https://api.bybit.com/v5/market/kline'

class HandoffTests(unittest.TestCase):
    def test_equivalent_trade_decimals_preserve_both_source_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'j.sqlite')
            run=j.start_run('archive_pair_fixture')
            for name,price,size in [('first','1.0','2.00'),('second','1.00','2.0')]:
                path=root/(name+'.csv.gz')
                path.write_bytes(gzip.compress(('timestamp,symbol,side,size,price,trdMatchID\n1704067200.123,BTCUSDT,Buy,'+size+','+price+',same-id\n').encode()))
                records=iter_trade_records(path,'linear','BTCUSDT',{'url':'https://public.bybit.com/trading/BTCUSDT/'+name+'.csv.gz','ingestion_run':run})
                j.save(name,'observed',{},records,run_id=run)
            j.finish_run(run,'PARTIAL'); j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text())
            self.assertFalse(bundle['conflicts'])
            self.assertEqual(len(bundle['raw_record_versions']),2)
            self.assertEqual({r['raw_record']['payload']['price'] for r in bundle['records']},{'1.0','1.00'})
            self.assertEqual({r['raw_record']['payload']['size'] for r in bundle['records']},{'2.00','2.0'})
            self.assertEqual(len({semantic_hash(r['raw_record']) for r in bundle['records']}),1)
            self.assertEqual(len(bundle['storage_batches']),2)
            self.assertEqual([len(b['records']) for b in bundle['storage_batches']],[1,1])
            self.assertEqual({b['records'][0]['payload']['trade_id'] for b in bundle['storage_batches']},{'same-id'})
            self.assertEqual(len({b['records'][0]['source']['sha256'] for b in bundle['storage_batches']}),2)

    def test_trade_decimal_comparison_does_not_round_with_decimal_context(self):
        base={'schema_version':'1.0','dataset':'trades','timestamp_ms':1704067200123,'timestamp_ns':None,'timestamp_precision':'ms','trade_id':'same','id_origin':'exchange','payload':{'price':'12345678901234567890123456789.000','size':'0.00000000000000000000000000010','side':'Buy'}}
        equivalent={**base,'payload':{**base['payload'],'price':'12345678901234567890123456789','size':'0.0000000000000000000000000001'}}
        changed={**equivalent,'payload':{**equivalent['payload'],'price':'12345678901234567890123456788'}}
        with localcontext() as context:
            context.prec=3
            self.assertEqual(semantic_hash(base),semantic_hash(equivalent))
            self.assertNotEqual(semantic_hash(base),semantic_hash(changed))
    def test_second_window_failure_keeps_exact_failure_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'j.sqlite'); api=Mock()
            api.call.side_effect=[({'list':[{'fundingRateTimestamp':'0','fundingRate':'1'}]},URL),requests.ConnectionError('fixture')]
            with self.assertRaises(requests.ConnectionError): Backfill(api,j).run('linear','BTCUSDT','funding','',0,200,window_ms=100)
            j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text())
            failed=next(b for b in bundle['storage_batches'] if b['evidence']['status']=='FAILED')
            self.assertEqual((failed['evidence']['start_ms'],failed['evidence']['end_ms']),(100,200))
    def test_empty_acquisition_has_a_B_run_evidence_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'j.sqlite'); api=Mock()
            api.call.return_value=({'list':[]},URL)
            Backfill(api,j).run('linear','BTCUSDT','funding','',0,60000)
            j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text())
            self.assertEqual(len(bundle['storage_batches']),1)
            batch=bundle['storage_batches'][0]
            self.assertEqual(batch['records'],[])
            self.assertEqual(batch['evidence']['status'],'EMPTY')
            self.assertEqual(batch['evidence']['source_response_count'],0)
            self.assertIn(batch['ingestion_run'],batch['evidence']['acquisition_manifest']['runs'])
    def test_legacy_integer_version_is_retained_but_not_published(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'j.sqlite')
            raw={'schema_version':1,'venue':'bybit','category':'linear','symbol':'BTCUSDT','dataset':'kline','interval':'1','timestamp_ms':0,'is_closed':True,'payload':['0','1','1','1','1','1','1'],'source':{'url':URL,'fetched_at':'2026-11-07T13:00:00Z'}}
            j.save('legacy','observed',{},[raw]); j.export(root/'handoff.json'); j.close()
            record=json.loads((root/'handoff.json').read_text())['records'][0]
            self.assertEqual(record['raw_record']['schema_version'],1)
            self.assertIsNone(record['storage_record'])
    def test_failed_rest_attempt_and_retry_remain_resolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'j.sqlite')
            api=Mock(); api.server_time_ms.return_value=2000000000000
            row=['60000','1','1','1','1','1','1']
            api.call.side_effect=[({'list':[row]},URL),requests.ConnectionError('fixture network failure')]
            with self.assertRaises(requests.ConnectionError): Backfill(api,j).run('linear','BTCUSDT','kline','1',0,120000,limit=1)
            j.close(); j=Journal(root/'j.sqlite')
            api.call.side_effect=None; api.call.return_value=({'list':[row,['0','1','1','1','1','1','1']]},URL)
            Backfill(api,j).run('linear','BTCUSDT','kline','1',0,120000)
            j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text())
            self.assertTrue(any(r['status']=='FAILED' for r in bundle['runs'].values()))
            self.assertTrue(any(e['kind']=='rest_failure' and e['response_count'] is None for e in bundle['evidence'].values()))
            self.assertFalse(bundle['conflicts'])
            self.assertTrue(all(all(ref in bundle['evidence'] for ref in r['evidence_refs']) for r in bundle['records']))

    def test_url_and_observed_status_do_not_establish_availability(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'j.sqlite')
            raw={'schema_version':'1.0','venue':'bybit','category':'linear','symbol':'BTCUSDT','dataset':'kline','interval':'1','timestamp_ms':0,'is_closed':True,'payload':['0','1','1','1','1','1','1'],'source':{'url':URL,'fetched_at':'2026-11-07T13:00:00Z'}}
            j.save('legacy','observed',{},[raw]); j.export(root/'handoff.json'); j.close()
            record=json.loads((root/'handoff.json').read_text())['records'][0]
            self.assertEqual(record['availability']['status'],'UNVERIFIED')
            self.assertEqual(record['coverage']['status'],'UNKNOWN')
            self.assertIsNone(record['storage_record'])
            self.assertIsNone(record['contract_record'])

    def test_invalid_financial_value_does_not_abort_lossless_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'j.sqlite')
            raw={'schema_version':'1.0','venue':'bybit','category':'linear','symbol':'BTCUSDT','dataset':'kline','interval':'1','timestamp_ms':0,'is_closed':True,'payload':['0','NaN','2','1','2','3','5'],'source':{'url':URL,'fetched_at':'2026-11-07T13:00:00Z','source_timestamp':'0'}}
            api=Mock(); api.server_time_ms.return_value=2000000000000
            api.call.return_value=({'list':[raw['payload']]},URL)
            Backfill(api,j).run('linear','BTCUSDT','kline','1',0,60000)
            try: j.export(root/'handoff.json')
            finally: j.close()
            bundle=json.loads((root/'handoff.json').read_text())
            self.assertEqual(bundle['records'][0]['raw_record']['payload'][1],'NaN')
            self.assertIsNone(bundle['records'][0]['storage_record'])
            self.assertTrue(bundle['records'][0]['projection_errors'])
            self.assertFalse(any(batch['records'] for batch in bundle['storage_batches']))
            self.assertTrue(all(batch['evidence']['status']=='UNVERIFIED' for batch in bundle['storage_batches']))

    def test_http200_html_never_replaces_existing_archive(self):
        from src.ingest.archive import ArchiveLoader
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); path=root/'a.csv.gz'
            original=gzip.compress(b'timestamp,price\n1,2\n'); path.write_bytes(original)
            t=Mock(); t.get.side_effect=lambda *a,**kw:response(gzip.compress(b'<html>,a\n<body>,b\n'))
            j=Journal(root/'j.sqlite')
            try:
                with self.assertRaises(ValueError): ArchiveLoader(j,t).download('https://public.bybit.com/trading/BTCUSDT/a.csv.gz',path)
                self.assertEqual(path.read_bytes(),original)
            finally: j.close()

    def test_revised_archive_etag_restarts_instead_of_appending(self):
        from src.ingest.archive import ArchiveLoader
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); path=root/'a.csv.gz'; part=path.with_name(path.name+'.partial')
            original=gzip.compress(b'timestamp,price\n1,2\n'); revised=gzip.compress(b'timestamp,price\n1,3\n')
            part.write_bytes(original[:10]); url='https://public.bybit.com/trading/BTCUSDT/a.csv.gz'
            part.with_name(part.name+'.json').write_text(json.dumps({'url':url,'etag':'"old"'}))
            t=Mock(); t.get.side_effect=[response(revised[:1],206,{'Content-Range':f'bytes 0-0/{len(revised)}','ETag':'"new"'}),response(revised,headers={'Content-Length':str(len(revised)),'ETag':'"new"'})]
            j=Journal(root/'j.sqlite')
            try:
                ArchiveLoader(j,t).download(url,path)
                self.assertEqual(path.read_bytes(),revised)
                self.assertNotIn('Range',t.get.call_args.kwargs['headers'])
            finally: j.close()

    def test_rest_export_preserves_run_raw_response_and_exact_cursors(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'j.sqlite')
            api=Mock(); api.server_time_ms.return_value=2000000000000
            api.call.side_effect=[({'list':[{'timestamp':'300000','openInterest':'12.30'}],'nextPageCursor':'real-token'},URL),({'list':[{'timestamp':'0','openInterest':'11'}],'nextPageCursor':''},URL)]
            Backfill(api,j).run('linear','BTCUSDT','open_interest','5min',0,600000,limit=1)
            j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text())
            evidence=[e for e in bundle['evidence'].values() if e['kind']=='rest_response']
            self.assertEqual([e['pagination']['cursor_in'] for e in evidence[:2]],[None,'real-token'])
            self.assertEqual([e['pagination']['cursor_out'] for e in evidence[:2]],['real-token',''])
            self.assertEqual(evidence[0]['pagination']['response_count'],1)
            self.assertEqual(evidence[0]['raw_response']['list'][0]['openInterest'],'12.30')
            for item in bundle['records']:
                self.assertIn(item['ingestion_run'],bundle['runs'])
                self.assertTrue(item['evidence_refs'])
                self.assertTrue(all(ref in bundle['evidence'] for ref in item['evidence_refs']))
                self.assertNotEqual(item['coverage']['status'],'COMPLETE')
                self.assertEqual(item['provenance'][0]['source_kind'],'bybit_rest')

    def test_trade_export_is_lossless_with_us_and_distinct_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); path=root/'a.csv.gz'
            path.write_bytes(gzip.compress(b'timestamp,symbol,side,size,price,trdMatchID\n1704067200.123456,BTCUSDT,Buy,0.10,42000.01,one\n1704067200.123456,BTCUSDT,Sell,0.20,42000.02,two\n'))
            j=Journal(root/'j.sqlite')
            j.save('archive-parse','observed',{},iter_trade_records(path,'linear','BTCUSDT',{'url':'https://public.bybit.com/trading/BTCUSDT/a.csv.gz'}))
            j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text())
            self.assertEqual(len(bundle['records']),2)
            source=bundle['records'][0]['raw_record']['source']
            self.assertEqual(base64.b64decode(bundle['artifacts'][source['sha256']]['bytes_base64']),path.read_bytes())
            for item in bundle['records']:
                self.assertEqual(item['storage_record']['timestamp_us'],1704067200123456)
                self.assertIsNone(item['storage_record']['timestamp_ns'])
                self.assertEqual(item['contract_record']['timestamp_ns'],'1704067200123456000')
                self.assertEqual(item['raw_record']['payload']['timestamp'],'1704067200.123456')
                self.assertEqual(item['storage_record']['payload']['trade_id'],item['contract_record']['trade_id'])

    def test_conflicting_identity_retains_both_sources_and_is_quarantined(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'j.sqlite')
            r={'schema_version':'1.0','venue':'bybit','category':'linear','symbol':'BTCUSDT','dataset':'trades','interval':'','timestamp_ms':1704067200000,'timestamp_ns':None,'timestamp_precision':'ms','trade_id':'one','id_origin':'exchange','payload':{'price':'1','size':'2','side':'Buy'},'source':{'url':URL,'fetched_at':'2026-11-07T13:00:00Z','source_kind':'bybit_rest','source_timestamp':'1704067200000'}}
            j.save('first','observed',{},[r])
            r={**r,'source':{**r['source'],'url':URL+'?second=1'},'payload':{**r['payload'],'price':'3'}}
            j.save('second','observed',{},[r]); j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text())
            self.assertEqual(len(bundle['records']),2)
            self.assertEqual(len(bundle['conflicts']),1)
            self.assertFalse(bundle['storage_batches'])

    def test_interrupted_task_attempts_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'j.sqlite')
            j.save('job','paused',{'requests':[{'params':{'cursor':'original'},'cursor':'original','returned':0}]})
            j.close(); j=Journal(root/'j.sqlite')
            j.save('job','observed',{'requests':[]}); j.export(root/'handoff.json'); j.close()
            bundle=json.loads((root/'handoff.json').read_text())
            self.assertEqual([task['status'] for task in bundle['tasks']],['paused','observed'])
            self.assertEqual(bundle['tasks'][0]['detail']['requests'][0]['cursor'],'original')
            self.assertTrue(all(run['status']!='RUNNING' for run in bundle['runs'].values()))

    def test_unknown_source_does_not_gain_official_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); j=Journal(root/'j.sqlite')
            r={'venue':'bybit','category':'linear','symbol':'BTCUSDT','dataset':'kline','interval':'1','timestamp_ms':0,'payload':['0','1','1','1','1','1','1'],'source':{}}
            j.save('legacy','observed',{},[r]); j.export(root/'handoff.json'); j.close()
            item=json.loads((root/'handoff.json').read_text())['records'][0]
            self.assertEqual(item['availability']['status'],'UNVERIFIED')
            self.assertIsNone(item['storage_record'])
            self.assertIsNone(item['contract_record'])
            self.assertTrue(item['projection_errors'])

if __name__=='__main__': unittest.main()
