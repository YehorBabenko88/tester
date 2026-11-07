"""All market rows here are explicitly synthetic, not live evidence."""
import copy
import importlib.util
import itertools
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

import src.analytics as a

REF = Path(__file__).with_name('reference')
spec = importlib.util.spec_from_file_location('shared_validator', REF / 'validate.py')
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()) * 1000


def trade(i='1', ts=0, price='10', qty='2', side='Buy', **extra):
    raw = dict(execId=i, time=str(ts), price=price, size=qty, side=side)
    raw.update(extra)
    return a.normalize_trade(raw, category='linear', symbol='BTCUSDT', source_ref={
        'source_kind': 'synthetic_fixture', 'source_ref': 'fixture:trades',
        'source_hash': None, 'ingestion_run': 'synthetic-test',
        'timestamp_precision': 'ms', 'source_timestamp': str(ts)})


class Core(unittest.TestCase):
    def test_independent_ohlcv_delta_reference(self):
        rows=[trade(str(n),n,price=p,qty=q,side=s) for n,(p,q,s) in enumerate([
            ('10','2','Buy'),('13','3','Sell'),('9','5','Buy'),('11','1','Sell'),('12','1','Buy')])]
        c=a.aggregate(list(reversed(rows))+rows,interval='1',price_step='1')[0]
        self.assertEqual([c[k] for k in ['open','high','low','close','volume']],['10','13','9','12','12'])
        fp=a.footprint(rows,interval='1',price_step='1')[0]
        self.assertEqual([fp[k] for k in ['buy_volume','sell_volume','delta']],['8','4','4'])
    def test_normalize_and_schema(self):
        t = trade()
        validator.validate_record('trade', t)
        self.assertEqual(t['timestamp_ms'], 0)
        self.assertIsNone(t['timestamp_ns'])
        self.assertEqual(t['coverage']['status'], 'UNKNOWN')
        self.assertEqual(a.trade_notional(t)['value'], '20')

    def test_invalid_and_unknown(self):
        self.assertEqual(trade(side=None)['taker_side'], 'Unknown')
        for value in ['NaN', 'Infinity', '-1', '0', 1.1]:
            with self.assertRaises(ValueError):
                trade(price=value)
        t = trade(); t['schema_version'] = '2.0'
        with self.assertRaises(ValueError):
            a.aggregate([t], interval='1', price_step='1')
        with self.assertRaises(ValueError):
            trade(symbol='ETHUSDT')

    def test_dedup_conflict_scoped_identity(self):
        t = trade()
        self.assertEqual(a.aggregate([t, t], interval='1', price_step='1')[0]['volume'], '2')
        changed = copy.deepcopy(t); changed['quantity'] = '3'
        for rows in [[t, changed], [changed, t]]:
            with self.assertRaises(a.SourceConflict):
                a.aggregate(rows, interval='1', price_step='1')
        other = copy.deepcopy(t); other['symbol'] = 'ETHUSDT'
        self.assertEqual(len(a.aggregate([t, other], interval='1', price_step='1')), 2)

    def test_equivalent_decimal_format_and_strict_provenance(self):
        t=trade(); other=copy.deepcopy(t)
        other['price']='10.0'; other['quantity']='2.00'
        self.assertEqual(a.aggregate([t,other],interval='1',price_step='1')[0]['volume'],'2')
        del other['provenance'][0]['source_hash']
        with self.assertRaises(ValueError):
            a.normalize_trade(other,category='linear',symbol='BTCUSDT',source_ref=other['provenance'])

    def test_nested_metadata_schema_types(self):
        for mutate in [lambda t:t['coverage'].update(start_ms='0'),
                       lambda t:t['availability'].update(reason=123),
                       lambda t:t['provenance'][0].update(source_ref=123),
                       lambda t:t['provenance'][0].update(ingestion_run=True)]:
            t=trade(); mutate(t)
            with self.assertRaises(ValueError):
                a.normalize_trade(t,category='linear',symbol='BTCUSDT',source_ref=t['provenance'])

    def test_unknown_side_metric_quality_is_partial(self):
        t=trade(side=None)
        t['availability']={'status':'AVAILABLE','reason':None}
        t['coverage']={'status':'COMPLETE','start_ms':0,'end_ms':60000,'evidence_refs':['fixture:complete']}
        s=a.candle_statistics([t],interval='1',price_step='1')[0]
        self.assertIsNone(s['delta'])
        self.assertNotEqual(s['quality']['availability']['status'],'AVAILABLE')

    def test_permutations_exact_huge_sum(self):
        rows = [trade(str(i), qty=q) for i, q in enumerate([
            '10000000000000000000000000000', '1', '0.00000000000000000000000000001'])]
        expected = '10000000000000000000000000001.00000000000000000000000000001'
        with localcontext() as ctx:
            ctx.prec = 6
            outputs = [a.aggregate(list(p), interval='1', price_step='0.1') for p in itertools.permutations(rows)]
        self.assertTrue(all(x == outputs[0] for x in outputs))
        self.assertEqual(outputs[0][0]['volume'], expected)

    def test_genuine_sub_ms_order(self):
        first = trade('z', price='9'); last = trade('a', price='11')
        for row, ns in [(first, '100'), (last, '900')]:
            row['timestamp_ns'] = ns; row['timestamp_precision'] = 'ns'
            row['provenance'][0]['timestamp_precision'] = 'ns'
        c = a.aggregate([last, first], interval='1', price_step='1')[0]
        self.assertEqual((c['open'], c['close']), ('9', '11'))
        bad = trade(); bad['timestamp_ns'] = '123'
        with self.assertRaises(ValueError):
            a.aggregate([bad], interval='1', price_step='1')

    def test_boundaries_and_gaps(self):
        rows = [trade('1', 59999), trade('2', 60000), trade('3', 180000)]
        self.assertEqual([x['start_ms'] for x in a.aggregate(rows, interval='1', price_step='1')], [0, 60000, 180000])
        self.assertEqual(a.aggregate([], interval='1', price_step='1'), [])
        for ts, interval, start, end in [
            ('2024-02-29T23:59:59', 'M', '2024-02-01', '2024-03-01'),
            ('2024-12-31', 'W', '2024-12-30', '2025-01-06'),
            ('2024-03-31T23:59:59', 'D', '2024-03-31', '2024-04-01')]:
            self.assertEqual(a.interval_bounds(ms(ts), interval), (ms(start), ms(end)))
        for interval in ['1','3','5','15','30','60','120','240','360','720','D','W','M']:
            c = a.aggregate([trade(ts=ms('2024-01-01'))], interval=interval, price_step='1')[0]
            validator.validate_record('candle', c)
        with self.assertRaises(ValueError):
            a.aggregate([trade()], interval='W', price_step='1')
        self.assertEqual(a.custom_candles([trade(ts=45000)], interval='45s', price_step='1')[0]['start_ms'], 45000)

    def test_unknown_side_delta_cvd_and_clusters(self):
        rows = [trade('1', price='10.1'), trade('2', price='10.2', side='Sell', qty='1'),
                trade('3', 60000, side=None), trade('4', 120000)]
        stats = a.candle_statistics(rows, interval='1', price_step='1')
        self.assertEqual(stats[0]['delta'], '1')
        self.assertEqual(stats[0]['cvd'], '1')
        self.assertIsNone(stats[1]['delta']); self.assertIsNone(stats[2]['cvd'])
        fp = a.footprint(rows, interval='1', price_step='1')
        for r in fp: validator.validate_record('footprint', r)
        self.assertEqual(fp[0]['side_basis'], 'taker')
        self.assertIsNone(fp[1]['delta'])
        levels = a.price_levels(rows[:2], interval='1', price_step='1')
        self.assertEqual(len(levels), 1)
        self.assertEqual(levels[0]['price_lower'], '10')
        self.assertEqual(len(a.price_levels(rows[:2], interval='1', price_step='0.1')), 2)

    def test_profile_vwap_value_area_and_large_trades(self):
        rows = [trade('1', price='10', qty='3'), trade('2', price='12', qty='1', side='Sell')]
        s = a.candle_statistics(rows, interval='1', price_step='1', large_trade_threshold='2')[0]
        self.assertEqual(s['vwap'], '10.5')
        self.assertEqual(s['point_of_control'], '10')
        self.assertEqual(s['value_area'], {'low':'10','high_exclusive':'11','fraction':'0.7'})
        self.assertEqual(s['large_trades']['count'], 1)
        self.assertEqual(s['volume_imbalance'], '0.5')
        inverse = trade(); inverse['category'] = 'inverse'
        self.assertEqual(a.trade_notional(inverse), {'value':'2','unit':'quote','base_quantity':'0.2'})

    def test_inheritance_closure_and_coverage(self):
        t = trade()
        t['availability'] = {'status':'PARTIAL','reason':'missing archive part'}
        t['coverage'] = {'status':'GAPS','start_ms':0,'end_ms':60000,'evidence_refs':['fixture:gaps']}
        c = a.aggregate([t], interval='1', price_step='1', as_of_ms=60000)[0]
        self.assertTrue(c['is_closed'])
        self.assertEqual(c['coverage']['status'], 'GAPS')
        self.assertEqual(c['availability']['status'], 'PARTIAL')
        self.assertIn(t['provenance'][0], c['provenance'])
        validator.validate_record('candle', c)
        t = trade(); t['availability'] = {'status':'AVAILABLE','reason':None}
        t['coverage'] = {'status':'COMPLETE','start_ms':0,'end_ms':60000,'evidence_refs':['fixture:complete']}
        c = a.aggregate([t], interval='1', price_step='1', as_of_ms=60000)[0]
        self.assertEqual(c['coverage']['status'], 'COMPLETE')
        validator.validate_record('candle', c)
        unclosed = a.aggregate([t], interval='1', price_step='1')[0]
        self.assertFalse(unclosed['is_closed'])
        self.assertNotEqual(unclosed['coverage']['status'], 'COMPLETE')

    def test_comparison_requires_evidence_and_context(self):
        c = a.aggregate([trade()], interval='1', price_step='1', as_of_ms=60000)[0]
        o = copy.deepcopy(c); o['kind'] = 'official_trade'
        rows = a.compare_candles([c], [o])
        self.assertTrue(all(r['status']=='NOT_COMPARABLE' for r in rows))
        for r in rows: validator.validate_record('comparison', r)
        for x in [c, o]:
            x['coverage'] = {'status':'COMPLETE','start_ms':0,'end_ms':60000,'evidence_refs':['fixture:complete']}
            x['availability'] = {'status':'AVAILABLE','reason':None}
        o['close'] = '11'; o['high'] = '11'
        rows = a.compare_candles([c],[o], tolerance='0', rounding='none')
        self.assertEqual(next(r for r in rows if r['field']=='close')['status'], 'MISMATCH')
        for r in rows: validator.validate_record('comparison', r)
        self.assertEqual(a.compare_candles([c],[])[0]['status'], 'MISSING_OFFICIAL')


if __name__ == '__main__': unittest.main()
