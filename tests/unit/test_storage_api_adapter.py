"""Synthetic producer/consumer fixtures for B -> E, without analytic calculations."""
import copy
import unittest
from src.integration.storage_api import project_trade, read_trades, IntegrationUnavailable


def source():
    return dict(source_kind='synthetic_fixture',source_ref='fixture://batch',source_hash='a'*64,
                ingestion_run='fixture-acquisition-run',timestamp_precision='ns',source_timestamp='1700000000.123456789')


def row(key='a',ns=1700000000123456789):
    return dict(schema_version='1.0',venue='bybit',category='linear',symbol='BTCUSDT',dataset='trades',
                interval='',record_key=key,timestamp_ms=1700000000123,timestamp_ns=ns,
                timestamp_precision='ns',trade_id='fixture-'+key,id_origin='exchange',
                payload={'price':'9007199254740993.001','size':'0.00000001','side':'Buy'},
                source={'url':'fixture://batch'})


class Store:
    def __init__(self,rows,complete=False):self.rows=rows;self.complete=complete;self.calls=[]
    def read(self,*args,**kwargs):
        self.calls.append((args,kwargs));values=self.rows
        if kwargs['after'] is not None:
            values=[r for r in values if (r['timestamp_ms'],r['record_key'])>tuple(kwargs['after'])]
        return copy.deepcopy(values[:kwargs['limit']])
    def coverage(self,*args):
        return dict(start=1700000000000,end=1700000060000,complete=self.complete,
                    verified=[[1700000000000,1700000060000]] if self.complete else [],
                    unknown=[] if self.complete else [[1700000000000,1700000060000]],corrupt=[],unavailable=[])


class StorageApiAdapterTests(unittest.TestCase):
    def context(self):return dict(provenance=[source()],availability={'status':'UNVERIFIED','reason':'Fixture incomplete'},
                                coverage={'status':'UNKNOWN','start_ms':1700000000000,'end_ms':1700000060000,'evidence_refs':[]})

    def test_projection_preserves_financial_strings_precision_and_identity(self):
        r=row();original=copy.deepcopy(r);value=project_trade(r,**self.context())
        self.assertEqual(value['quantity'],'0.00000001');self.assertEqual(value['price'],'9007199254740993.001')
        self.assertEqual(value['timestamp_ns'],'1700000000123456789');self.assertEqual(value['trade_id'],'fixture-a')
        self.assertEqual(r,original);self.assertNotIn('payload',value)

    def test_unknown_original_run_is_not_reconstructed_at_read_time(self):
        context=self.context();context['provenance']=[]
        self.assertRaises(IntegrationUnavailable,project_trade,row(),**context)

    def test_microseconds_preserve_only_genuine_source_precision(self):
        r=row();r.update(timestamp_ns=None,timestamp_us=1700000000123456,timestamp_precision='us')
        context=self.context();context['provenance'][0].update(timestamp_precision='us',source_timestamp='1700000000.123456')
        self.assertEqual(project_trade(r,**context)['timestamp_ns'],'1700000000123456000')
        del r['timestamp_us'];self.assertRaises(IntegrationUnavailable,project_trade,r,**context)

    def test_unavailable_never_becomes_available_and_reason_survives(self):
        context=self.context();context['availability']={'status':'UNAVAILABLE','reason':'source_conflict_fixture'}
        value=project_trade(row(),**context)
        self.assertEqual(value['availability'],context['availability'])

    def test_same_ms_keyset_cursor_does_not_drop_records_or_cross_scope(self):
        store=Store([row('a'),row('b'),row('c')])
        kwargs=dict(venue='bybit',category='linear',symbol='BTCUSDT',start_ms=1700000000000,end_ms=1700000060000,
                    limit=2,provenance_resolver=lambda key:[source()])
        first=read_trades(store,**kwargs)
        second=read_trades(store,**kwargs,after=first['next_cursor'])
        self.assertEqual([r['trade_id'] for r in first['data']+second['data']],['fixture-a','fixture-b','fixture-c'])
        self.assertIsNone(second['next_cursor'])
        self.assertRaises(ValueError,read_trades,store,**{**kwargs,'symbol':'ETHUSDT'},after=first['next_cursor'])

    def test_storage_complete_without_evidence_is_unverified(self):
        value=read_trades(Store([row()],complete=True),venue='bybit',category='linear',symbol='BTCUSDT',
            start_ms=1700000000000,end_ms=1700000060000,limit=10,provenance_resolver=lambda key:[source()])
        self.assertEqual(value['availability']['status'],'UNVERIFIED')
        self.assertEqual(value['coverage']['status'],'UNKNOWN')

    def test_empty_page_is_not_complete(self):
        value=read_trades(Store([],complete=True),venue='bybit',category='linear',symbol='BTCUSDT',
            start_ms=1700000000000,end_ms=1700000060000,limit=10,provenance_resolver=lambda key:[])
        self.assertEqual(value['data'],[]);self.assertEqual(value['availability']['status'],'UNAVAILABLE')
        self.assertEqual(value['coverage']['status'],'UNKNOWN')

    def test_ten_synthetic_market_identities_do_not_mix(self):
        for i in range(10):
            r=row();r['symbol']=f'TEST{i}USDT'
            self.assertEqual(project_trade(r,**self.context())['symbol'],f'TEST{i}USDT')

    def test_out_of_range_or_nonmonotonic_storage_page_is_rejected(self):
        kwargs=dict(venue='bybit',category='linear',symbol='BTCUSDT',start_ms=1700000000000,
                    end_ms=1700000060000,limit=10,provenance_resolver=lambda key:[source()])
        outside=row();outside['timestamp_ms']=1700000060000
        for rows in [[outside],[row('b'),row('a')],[row('a'),row('a')]]:
            with self.subTest(rows=rows):
                self.assertRaises(IntegrationUnavailable,read_trades,Store(rows),**kwargs)

    def test_empty_corrupt_page_retains_integrity_error_and_original_reason(self):
        class Corrupt(Store):
            def coverage(self,*args):
                return dict(start=1700000000000,end=1700000060000,complete=False,verified=[],
                            unknown=[],corrupt=[[1700000000000,1700000060000]],unavailable=[],
                            reason='sha256 mismatch source archive')
        value=read_trades(Corrupt([]),venue='bybit',category='linear',symbol='BTCUSDT',
            start_ms=1700000000000,end_ms=1700000060000,limit=10,provenance_resolver=lambda key:[])
        self.assertEqual(value['coverage']['status'],'ERROR')
        self.assertEqual(value['availability'],{'status':'UNAVAILABLE','reason':'sha256 mismatch source archive'})


if __name__=='__main__':unittest.main()

