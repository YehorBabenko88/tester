import copy
from types import SimpleNamespace
import unittest
from test_storage_api_adapter import Store,row
import test_storage_provenance_adapter as provenance_fixtures


class NotConnected(Exception):pass


class TradeReaderTests(unittest.TestCase):
    def reader(self,rows):
        from src.integration.storage_reader import StorageTradeReader
        return StorageTradeReader(Store(rows),unavailable_error=NotConnected)

    def query(self,**updates):
        return SimpleNamespace(**{**dict(venue='bybit',category='linear',symbol='BTCUSDT',
            start_ms=1700000000000,end_ms=1700000060000,limit=1,cursor=None),**updates})

    def rows(self):
        result=[row('a'),row('b')]
        for r in result:
            r['provenance']=provenance_fixtures.StorageProvenanceTests().blob()
            observation=r['provenance']['observations'][0]
            observation.update(source_run_status='matched',source_ingestion_run='actual-acquisition')
            observation['source']['ingestion_run']='actual-acquisition'
        return result

    def test_actual_b_attached_provenance_pages_without_n_plus_one_queries(self):
        rows=self.rows();original=copy.deepcopy(rows);reader=self.reader(rows)
        a=reader.read('trades',self.query());b=reader.read('trades',self.query(cursor=a['next_cursor']))
        self.assertEqual([x['trade_id'] for x in a['data']+b['data']],['fixture-a','fixture-b'])
        self.assertEqual(a['data'][0]['provenance'][0]['ingestion_run'],'actual-acquisition')
        self.assertEqual(a['coverage']['status'],'UNKNOWN');self.assertEqual(rows,original)

    def test_other_resources_fail_explicitly_without_analytics(self):
        reader=self.reader(self.rows())
        for resource in ('candles','footprint','funding','open-interest','sources'):
            with self.subTest(resource=resource):self.assertRaises(NotConnected,reader.read,resource,self.query())

    def test_missing_and_mismatched_provenance_fail_closed(self):
        for changed in ('missing','mismatch'):
            rows=self.rows()
            if changed=='missing':del rows[0]['provenance']
            else:rows[0]['provenance']['observations'][0]['source']['ingestion_run']='different'
            with self.subTest(changed=changed):self.assertRaises(NotConnected,self.reader(rows).read,'trades',self.query())

    def test_corrupt_empty_retains_original_reason(self):
        reader=self.reader([])
        reader.store.coverage=lambda *args:dict(start=1700000000000,end=1700000060000,
            complete=False,verified=[],corrupt=[[1700000000000,1700000060000]],reason='fixture:hash_mismatch')
        value=reader.read('trades',self.query())
        self.assertEqual(value['availability'],{'status':'UNAVAILABLE','reason':'fixture:hash_mismatch'})
        self.assertEqual(value['coverage']['status'],'ERROR')

    def test_factory_uses_own_tester_providers_only(self):
        from src.integration.standalone_policy import validate_factory_spec
        target=validate_factory_spec('src.integration.storage_reader:create_reader')
        self.assertIn('storage_reader.py',str(target))

    def test_caller_origin_without_original_declaration_is_not_strong_binding(self):
        rows=self.rows();observation=rows[0]['provenance']['observations'][0]
        observation['source_run_status']='undeclared';observation.pop('source_ingestion_run')
        observation['source'].pop('ingestion_run')
        self.assertRaises(NotConnected,self.reader(rows).read,'trades',self.query())
