import copy
import unittest
from decimal import Decimal

try:
    from src.storage.models import normalize
    from src.storage.coverage import coverage_report
except ImportError:
    normalize = coverage_report = None
from src.quality.candles import inspect


def trade(**changes):
    record = dict(schema_version='1.0', venue='bybit', category='linear', symbol='BTCUSDT',
                  dataset='trades', timestamp_ms=1700000000123, interval='',
                  payload=dict(trade_id='original-1', price='123.000000000000000001', size='0.01', side='Buy'),
                  source=dict(url='https://public.bybit.com/trading/BTCUSDT/a.csv.gz',
                              fetched_at='2026-11-07T12:00:00Z'))
    record.update(changes)
    return record


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(normalize, 'normalize contract is not implemented')

    def test_decimal_and_millisecond_precision_preserved(self):
        row = normalize(trade())
        self.assertEqual(row['payload']['price'], '123.000000000000000001')
        self.assertIsNone(row['timestamp_ns'])
        self.assertEqual(row['timestamp_precision'], 'ms')
        self.assertEqual(row['id_origin'], 'exchange')
        self.assertEqual(row['trade_id'], 'original-1')

    def test_original_ns_must_match_ms_and_precision(self):
        r = trade(timestamp_ns=1700000000123456789, timestamp_precision='ns')
        self.assertEqual(normalize(r)['timestamp_ns'], 1700000000123456789)
        for changes in [dict(timestamp_ns=1700000000123000000),
                        dict(timestamp_ns=1700000000123456789, timestamp_precision='ms'),
                        dict(timestamp_ns=1700000001123456789, timestamp_precision='ns')]:
            with self.assertRaises(ValueError): normalize(trade(**changes))

    def test_invalid_values_and_versions_rejected(self):
        for value in [1.2, 'NaN', 'Infinity', '-1', '0', True]:
            r = trade(); r['payload']['price'] = value
            with self.assertRaises(ValueError): normalize(r)
        for changes in [dict(schema_version='2.0'), dict(timestamp_ms=1.5),
                        dict(source={'url': 'x', 'fetched_at': '2026-11-07T00:00:00'})]:
            with self.assertRaises(ValueError): normalize(trade(**changes))

    def test_id_is_scoped_and_composite_is_explicit(self):
        self.assertNotEqual(normalize(trade())['record_key'], normalize(trade(category='spot'))['record_key'])
        r = trade(); del r['payload']['trade_id']
        with self.assertRaises(ValueError): normalize(r)
        r['source']['record_key'] = 'archive-hash:row-100'
        a = normalize(r); b = normalize(copy.deepcopy(r))
        self.assertEqual(a['trade_id'], b['trade_id'])
        self.assertEqual(a['id_origin'], 'composite_v1')

    def test_funding_negative_allowed_open_interest_negative_rejected(self):
        r = trade(dataset='funding', payload={'value': '-0.0001'})
        self.assertEqual(normalize(r)['payload']['value'], '-0.0001')
        with self.assertRaises(ValueError): normalize(trade(dataset='open_interest', payload={'value': '-1'}))

    def test_original_seconds_and_microseconds_precision(self):
        self.assertEqual(normalize(trade(timestamp_ms=1700000000000,timestamp_precision='s'))['timestamp_precision'],'s')
        r=normalize(trade(timestamp_precision='us',timestamp_us=1700000000123456))
        self.assertEqual(r['timestamp_us'],1700000000123456)
        self.assertIsNone(r['timestamp_ns'])
        with self.assertRaises(ValueError): normalize(trade(timestamp_precision='s'))

    def test_numeric_formatting_changes_do_not_conflict(self):
        a=trade(); a['payload']['price']='123.00'
        b=trade(); b['payload']['price']='1.23E+2'
        self.assertEqual(normalize(a)['content_hash'],normalize(b)['content_hash'])


class QualityTests(unittest.TestCase):
    def test_high_precision_bad_ohlc_is_not_rounded_away(self):
        result = inspect([[0, '1.000000000000000001', '1', '1', '1', '0']], '1', 120000)
        self.assertTrue(result['errors'])

    def test_sparse_gap_is_unknown_and_never_complete(self):
        r = inspect([[0,'1','1','1','1','0'], [120000,'1','1','1','1','0']], '1', 240000)
        self.assertFalse(r['complete'])
        self.assertEqual(r['gaps'][0]['classification'], 'unknown')
        self.assertEqual(r['errors'], [])

    def test_calendar_month_and_monday_week(self):
        r = inspect([[1706745600000,'1','1','1','1','0']], 'M', 1709251202000)
        self.assertEqual(r['errors'], [])  # February 2024 closed March 1
        r = inspect([[1706745600000,'1','1','1','1','0']], 'M', 1709164800000)
        self.assertTrue(any(x['reason']=='unclosed' for x in r['errors']))
        r = inspect([[1704067200000,'1','1','1','1','0']], 'W', 1704672002000)
        self.assertEqual(r['errors'], [])  # Monday Jan 1 2024

    def test_coverage_uses_union_and_never_hides_damage(self):
        self.assertIsNotNone(coverage_report, 'coverage engine is not implemented')
        rows = [dict(start=0,end=80,state='verified'), dict(start=40,end=100,state='verified')]
        self.assertEqual(coverage_report(0,100,rows)['verified_ms'],100)
        self.assertFalse(coverage_report(0,100,rows)['complete']) # no availability evidence
        rows += [dict(start=0,end=100,state='available')]
        self.assertTrue(coverage_report(0,100,rows)['complete'])
        rows += [dict(start=20,end=30,state='corrupt')]
        r = coverage_report(0,100,rows)
        self.assertFalse(r['complete']); self.assertEqual(r['verified_ms'],90)
        self.assertEqual(r['percent'],90)

    def test_unavailable_and_unknown_ranges_are_explicit(self):
        self.assertIsNotNone(coverage_report)
        r = coverage_report(0,100,[dict(start=0,end=50,state='verified'),dict(start=60,end=100,state='unavailable')])
        self.assertEqual(r['unknown'], [[50,60]])
        self.assertEqual(r['unavailable'], [[60,100]])
        self.assertFalse(r['complete'])


if __name__ == '__main__': unittest.main()
