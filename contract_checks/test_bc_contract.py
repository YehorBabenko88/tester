"""Opt-in original B/C conformance; run directly, excluded from normal discovery."""
import hashlib
import importlib.util
import io
import itertools
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
PINS={
 'B':('801297fb3f7e20aba5341f2aae3a5c58074b704b','src/quality/candles.py','b_candles_initial.py'),
 'C':('0070b5fcb9180a79b213698c99ea289df4442383','src/analytics/footprint.py','c_footprint_initial.py')}
SOURCES={}
def load(label):
    sha,path,fixture=PINS[label]
    source=subprocess.check_output(['git','show',f'{sha}:{path}'],cwd=ROOT)
    snapshot=(Path(__file__).parent/'fixtures'/fixture).read_bytes()
    if source!=snapshot:
        raise RuntimeError('Pinned snapshot mismatch: '+label)
    SOURCES[label]={'sha':sha,'path':path,'sha256':hashlib.sha256(source).hexdigest()}
    spec=importlib.util.spec_from_loader('original_'+label,loader=None)
    module=importlib.util.module_from_spec(spec)
    exec(compile(source,f'{sha}:{path}','exec'),module.__dict__)
    return module
B=load('B');C=load('C')

class BContract(unittest.TestCase):
    def test_empty_is_not_complete(self):
        self.assertFalse(B.inspect([],'1',120000)['complete'],'Empty source page is not completeness proof')

    def test_nonfinite_ohlc_is_not_complete(self):
        for value in ('NaN','Infinity'):
            with self.subTest(value=value):
                self.assertFalse(B.inspect([[0,value,value,value,value,'1']],'1',120000)['complete'])

    def test_exact_decimal_invalid_ohlc_is_not_complete(self):
        row=[0,'1.00000000000000000002','1.00000000000000000001','1','1','1']
        self.assertFalse(B.inspect([row],'1',120000)['complete'],'Binary float collapsed an invalid decimal high/open ordering')

    def test_quality_metadata_has_contract_version_and_provenance(self):
        value=B.inspect([[0,'1','1','1','1','1']],'1',120000)
        for field in ('schema_version','provenance'):
            with self.subTest(field=field):
                self.assertIn(field,value,'Required contract metadata absent from quality result')

class CContract(unittest.TestCase):
    def trade(self,**changes):
        value={'timestamp':1700000000123,'timestamp_ns':1700000000123456789,
               'price':'1','size':'1','side':'Buy','trade_id':'t1','exchange':'bybit',
               'category':'linear','symbol':'BTCUSDT','schema_version':'1',
               'provenance':{'source_kind':'archive','ingestion_run':'fixture','source_timestamp_precision':'ns'}}
        value.update(changes);return value

    def test_duplicate_exchange_id_is_not_double_counted(self):
        trade=self.trade()
        try: result=C.aggregate([trade,dict(trade)],60000,'1')
        except ValueError:return  # Explicit duplicate rejection also preserves totals.
        self.assertEqual(sum(int(row['trades']) for row in result),1,'Duplicate stable trade ID doubled footprint volume')
        self.assertEqual(result[0]['delta'],'1')

    def test_missing_side_preserves_unknown_delta(self):
        trade=self.trade();trade.pop('side')
        try:result=C.aggregate([trade],60000,'1')
        except Exception as exc:
            self.fail(f'UNAVAILABLE: exported aggregate cannot represent missing-side record with unknown/null delta ({type(exc).__name__}: {exc})')
        self.assertTrue(result,'Missing-side source silently disappeared')
        self.assertIsNone(result[0].get('delta'),'Missing side must not be inferred as Sell or zero')

    def test_decimal_totals_are_permutation_invariant(self):
        trades=[self.trade(trade_id='large',size='1000000000000000000000000000'),
                self.trade(trade_id='small1',size='0.6'),self.trade(trade_id='small2',size='0.6')]
        values=[C.aggregate(list(order),60000,'1')[0]['buy'] for order in itertools.permutations(trades)]
        self.assertEqual(len(set(values)),1,'Decimal context rounding changes results with input order')
        self.assertEqual(values[0],'1000000000000000000000000001.2','Decimal precision lost source quantities')

    def test_aggregate_metadata_carries_version_provenance_and_ms(self):
        value=C.aggregate([self.trade()],60000,'1')[0]
        for field in ('schema_version','provenance','timestamp_ms'):
            with self.subTest(field=field):
                self.assertIn(field,value,'Required contract metadata absent from derived result')
                if field=='timestamp_ms':self.assertIsInstance(value[field],int)

def main():
    stream=io.StringIO()
    suite=unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report={'issue':8,'contract':'v1','sources':SOURCES,'tests_run':result.testsRun,
            'passed':result.wasSuccessful(),
            'failures':[{'test':test.id(),'traceback':detail} for test,detail in result.failures],
            'errors':[{'test':test.id(),'traceback':detail} for test,detail in result.errors]}
    log=ROOT/'docs'/'coordination'/'evidence'/'contract-bc.log'
    log.parent.mkdir(parents=True,exist_ok=True)
    log.write_text(stream.getvalue()+'\n'+json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(stream.getvalue())
    print(json.dumps(report))
    return 0 if result.wasSuccessful() else 1

if __name__=='__main__':sys.exit(main())

