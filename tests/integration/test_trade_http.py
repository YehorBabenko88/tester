"""Synthetic same-checkout B -> MASTER -> E; never live exchange acceptance."""
import os
import tempfile
import unittest
from unittest.mock import patch
from tests.storage import test_postgres as support
from fastapi.testclient import TestClient
from src.api.main import create_app
from src.api.config import Settings


class ReaderHTTP(support.PostgresTests):
    def test_ten_instruments_actual_store_factory_http_keysets(self):
        symbols=['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT']
        for symbol in symbols:
            self.symbol=symbol
            records=[]
            for suffix in ('a','b'):
                record=self.record();record['payload']['trade_id']='fixture-'+suffix
                record['source'].update(ingestion_run='fixture-run-'+symbol,source_timestamp='1700000000.123')
                records.append(record)
            self.store.ingest(records,self.ev(pages=[dict(params={},cursor=None,next_cursor=None,count=2)]),symbol,
                run_id='fixture-run-'+symbol)
        with tempfile.TemporaryDirectory(prefix='tester_reader_http_') as tmp,patch.dict(os.environ,{'TESTER_DATABASE_URL':self.dsn}):
            os.environ.pop('TESTER_CONFIG_FILE',None)
            settings=Settings(database_url=self.dsn,data_dir=tmp,provider_factory='src.integration.storage_reader:create_reader')
            with TestClient(create_app(settings),base_url='http://127.0.0.1:8765') as client:
                for symbol in symbols:
                    query=dict(category='linear',symbol=symbol,start_ms=1700000000000,end_ms=1700000060000,limit=1)
                    first=client.get('/api/trades',params=query)
                    self.assertEqual(first.status_code,200,first.text)
                    a=first.json();self.assertTrue(a['next_cursor'])
                    second=client.get('/api/trades',params=query|{'cursor':a['next_cursor']})
                    self.assertEqual(second.status_code,200,second.text)
                    b=second.json();self.assertIsNone(b['next_cursor'])
                    self.assertEqual({r['trade_id'] for r in a['data']+b['data']},{'fixture-a','fixture-b'})
                    self.assertEqual(a['coverage']['status'],'UNKNOWN')
                    self.assertEqual(a['data'][0]['price'],'123.000000000000000001')
                    self.assertEqual(a['data'][0]['provenance'][0]['ingestion_run'],'fixture-run-'+symbol)
                    cross=client.get('/api/trades',params=query|{'symbol':'OTHERUSDT','cursor':a['next_cursor']})
                    self.assertEqual(cross.status_code,422)
                self.assertEqual(client.get('/api/candles',params=query).status_code,503)


for name in support.PostgresTests.__dict__:
    if name.startswith('test_'):setattr(ReaderHTTP,name,None)

if __name__=='__main__':unittest.main(defaultTest='ReaderHTTP')
