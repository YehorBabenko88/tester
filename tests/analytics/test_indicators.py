"""Synthetic indicator inputs with explicit provenance and quality."""
import copy
import unittest
from decimal import Decimal, localcontext, ROUND_DOWN
from tests.analytics.test_analytics import trade, validator
import src.analytics as a
from src.analytics import indicators as i


def candles(prices=('10','11','12'), width=60000):
    rows = [trade(str(n),n*width,price=p,qty=str(n+1)) for n,p in enumerate(prices)]
    return a.aggregate(rows,interval='1',price_step='1',as_of_ms=len(rows)*width)


def obs(ts,value,kind='oi'):
    base = trade(ts=ts)
    return {k:copy.deepcopy(base[k]) for k in ['schema_version','venue','category','symbol','timestamp_ms','provenance','availability','coverage']} | {
        'kind':kind,'value':value,'unit':'base' if kind=='oi' else 'rate','interval':'1'}


class Indicators(unittest.TestCase):
    def test_volatility_and_distribution(self):
        c = candles(('10','10','10','10'))
        self.assertEqual(i.realized_volatility(c)['value'],'0')
        self.assertEqual(i.historical_volatility(c,periods_per_year='365')['value'],'0')
        self.assertEqual(i.returns_distribution(c)['mean'],'0')
        self.assertIsNone(i.realized_volatility([])['value'])
        self.assertEqual(i.returns_distribution(candles(('10','20','10')))['count'],2)

    def test_atr_wilder(self):
        self.assertEqual(i.atr(candles(),period=2)['value'],'1')
        self.assertIsNone(i.atr(candles(),period=5)['value'])

    def test_volume_anomaly_no_lookahead(self):
        c = candles(('10','10','10','10'))
        for r,v in zip(c,['1','3','2','9']): r['volume']=v
        result = i.volume_anomalies(c,window=3,threshold='3')
        self.assertTrue(result[-1]['is_anomaly'])
        self.assertIsNone(result[0]['z_score'])

    def test_oi_funding_alignment_and_units(self):
        c = candles(('10','11','12'))
        result = i.oi_price_divergence(c,[obs(0,'10'),obs(60000,'9'),obs(120000,'8')])
        self.assertEqual(result[-1]['oi_change'],'-1')
        self.assertTrue(result[-1]['divergent'])
        self.assertIsNone(i.oi_price_divergence(c,[obs(1,'10')])[0]['divergent'])
        f = i.funding_dynamics([obs(0,'0.01','funding'),obs(60000,'-0.01','funding')])
        self.assertEqual(f['rate_sum'],'0')
        self.assertEqual(f['last_change'],'-0.02')
        wrong = obs(60000,'12'); wrong['unit']='quote'
        with self.assertRaises(ValueError): i.oi_change([obs(0,'10'),wrong])

    def test_absent_oi_does_not_inherit_complete_price_quality(self):
        c=candles()
        for r in c:
            r['availability']={'status':'AVAILABLE','reason':None}
            r['coverage']={'status':'COMPLETE','start_ms':r['start_ms'],'end_ms':r['end_ms'],'evidence_refs':['fixture:complete']}
        result=i.oi_price_divergence(c,[])
        self.assertIsNone(result[-1]['divergent'])
        self.assertNotEqual(result[-1]['quality']['availability']['status'],'AVAILABLE')
        self.assertNotEqual(result[-1]['quality']['coverage']['status'],'COMPLETE')

    def test_missing_oi_preserves_worse_upstream_error(self):
        c=candles()
        for r in c:
            r['availability']={'status':'UNAVAILABLE','reason':'upstream fetch failed'}
            r['coverage']['status']='ERROR'
        result=i.oi_price_divergence(c,[])[-1]
        self.assertEqual(result['quality']['availability']['status'],'UNAVAILABLE')
        self.assertEqual(result['quality']['coverage']['status'],'ERROR')
        self.assertIn('upstream fetch failed',result['quality']['availability']['reason'])

    def test_correlation_and_ranking(self):
        c = candles(('10','11','13','12'))
        self.assertEqual(i.correlation(c,c)['value'],'1')
        self.assertIsNone(i.correlation(candles(('10','10')),candles(('10','10')))['value'])
        result = i.volatility_ranking({'BTC':c},as_of_ms=240000)
        self.assertIsNone(result['1h'][0]['value'])
        self.assertIn('Insufficient',result['1h'][0]['reason'])

    def test_rounding_is_independent_of_caller_context(self):
        c = candles(('10','11','12'))
        expected = i.realized_volatility(c)
        with localcontext() as ctx:
            ctx.prec=6; ctx.rounding=ROUND_DOWN
            self.assertEqual(i.realized_volatility(c),expected)

    def test_gaps_are_explicit_and_mixed_series_rejected(self):
        c = candles(); c[1]['start_ms']+=1
        with self.assertRaises(ValueError): i.realized_volatility(c)
        c = candles(); c[1]['symbol']='ETHUSDT'
        with self.assertRaises(ValueError): i.atr(c,period=2)

    def test_archive_precision_and_synthetic_identity(self):
        source = dict(source_kind='synthetic_fixture',source_ref='fixture:archive',source_hash=None,
                      ingestion_run='fixture',timestamp_precision='us',source_timestamp='1.000123')
        t = a.normalize_trade({'timestamp':'1.000123','price':'1','size':'2','side':'Buy','trdMatchID':'x'},
                              category='linear',symbol='BTCUSDT',source_ref=source)
        self.assertEqual((t['timestamp_ms'],t['timestamp_ns']),(1000,'1000123000'))
        validator.validate_record('trade',t)
        missing = a.normalize_trade({'timestamp':'1.000123','price':'1','size':'2'},category='linear',symbol='BTCUSDT',source_ref=source)
        self.assertEqual(missing['id_origin'],'synthetic')
        validator.validate_record('trade',missing)

    def test_archive_side_is_not_asserted_from_rest_documentation(self):
        source=dict(source_kind='bybit_archive',source_ref='fixture:archive-schema',source_hash=None,
                    ingestion_run='fixture',timestamp_precision='ms',source_timestamp='1.001')
        t=a.normalize_trade({'timestamp':'1.001','price':'1','size':'2','side':'Sell','trdMatchID':'x'},
                            category='linear',symbol='BTCUSDT',source_ref=source)
        self.assertEqual(t['taker_side'],'Unknown')


if __name__ == '__main__': unittest.main()
