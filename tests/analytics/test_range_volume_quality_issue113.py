"""#113 GAP-4 (hourly High-Low range %) and GAP-5 (tick vs 1m kline volume).

All market rows are synthetic fixtures. Golden vectors are ported from the
removed legacy screener test (``git show d6059af:test_screener.py``) and the
legacy ``db.reconcile_minute_volume`` states (commit 80889da).
"""
import math
import unittest
from decimal import Decimal
from fractions import Fraction

import src.analytics as a
from src.analytics import indicators as i
from src.analytics.comparison import reconcile_minute_volume

H = 3 * 3600000  # a UTC hour boundary
M = 60000
DAY = 86400000

# Verbatim copy of d6059af:tests/platform/reference/legacy_screener.py one_hour_range
# (decorator removed); executed only as a float differential reference.
LEGACY_ONE_HOUR_RANGE = '''
def one_hour_range(cat,symbol):
    now=utc_ms(); end=now-now%60000
    start=end-60*60000
    raw=bybit().api("/v5/market/kline",dict(category=cat,symbol=symbol,
                  interval="1",start=start,end=end-1,limit=100)).get("list",[])
    bars={}
    try:
        for row in raw:
            ts=int(row[0])
            if not start<=ts<end:continue
            if ts in bars or ts%60000:return float("nan")
            values=tuple(float(row[i]) for i in range(1,5))
            if not all(math.isfinite(v) and v>0 for v in values):return float("nan")
            opening,high,low,close=values
            if not low<=min(opening,close)<=max(opening,close)<=high:return float("nan")
            bars[ts]=values
    except (ValueError,TypeError,IndexError,OverflowError):
        return float("nan")
    if set(bars)!=set(range(start,end,60000)):return float("nan")
    result=(max(v[1] for v in bars.values())-min(v[2] for v in bars.values()))*100/bars[start][0]
    return result if math.isfinite(result) else float("nan")
'''


def legacy_range(rows, end):
    class API:
        def api(self, path, params):
            return {'list': rows}
    scope = {'math': math, 'utc_ms': lambda: end + 12345, 'bybit': API}
    exec(LEGACY_ONE_HOUR_RANGE, scope)
    return scope['one_hour_range']('linear', 'TESTUSDT')


def candle(ts, o='100', h='101', low='99', c='100', volume='10', *, kind='official_trade',
           category='linear', symbol='TESTUSDT', closed=True, coverage='COMPLETE',
           availability='AVAILABLE'):
    return dict(schema_version='1.0', venue='bybit', category=category, symbol=symbol,
        timestamp_ms=ts, start_ms=ts, end_ms=ts + M, interval='1', kind=kind,
        open=o, high=h, low=low, close=c, volume=volume, is_closed=closed,
        provenance=[dict(source_kind='synthetic_fixture', source_ref='fixture:kline',
            source_hash=None, ingestion_run='synthetic', timestamp_precision='ms',
            source_timestamp=str(ts))],
        availability={'status': availability,
                      'reason': None if availability == 'AVAILABLE' else 'fixture'},
        coverage={'status': coverage, 'start_ms': ts, 'end_ms': ts + M,
                  'evidence_refs': ['fixture:kline'] if coverage == 'COMPLETE' else []})


def hour(**kw):
    return [candle(t, **kw) for t in range(H, H + 3600000, M)]


def legacy_rows(candles):
    return [[str(r['start_ms']), r['open'], r['high'], r['low'], r['close'], r['volume']]
            for r in candles]


def rng(candles, as_of=H + 3600000 + 12345):
    return i.hourly_range_percent(candles, hour_start_ms=H, as_of_ms=as_of)


class HourlyRange(unittest.TestCase):
    def test_hourly_range_legacy_hand_derived_two_percent(self):
        r = rng(hour())
        self.assertEqual(r['value'], '2')
        self.assertIsNone(r['reason'])
        self.assertEqual((r['reference_price'], r['high'], r['low']), ('100', '101', '99'))
        self.assertEqual((r['hour_start_ms'], r['hour_end_ms'], r['minute_count']), (H, H + 3600000, 60))
        self.assertEqual(rng(hour()[::-1])['value'], '2')
        self.assertEqual(legacy_range(legacy_rows(hour())[::-1], H + 3600000), 2.0)

    def test_hourly_range_ignores_older_extremes_and_next_hour_candle(self):
        extra = [candle(t, '100', '1000', '1', '100') for t in range(H - 5 * M, H, M)]
        extra.append(candle(H + 3600000, '100', '1000', '1', '100'))
        self.assertEqual(rng(extra + hour())['value'], '2')
        self.assertEqual(legacy_range(legacy_rows(extra + hour()), H + 3600000), 2.0)

    def test_hourly_range_incomplete_duplicate_or_unaligned_unavailable(self):
        rows = hour()
        shifted = [dict(r, start_ms=r['start_ms'] + 1, end_ms=r['end_ms'] + 1) for r in rows]
        for case in ([], rows[:-1], rows + [rows[0]], rows[:-1] + [rows[0]], shifted):
            with self.subTest(n=len(case)):
                r = rng(case)
                self.assertIsNone(r['value'])
                self.assertTrue(r['reason'])
                self.assertTrue(math.isnan(legacy_range(legacy_rows(case), H + 3600000)))
        self.assertIn('59 of 60', rng(rows[1:])['reason'])

    def test_hourly_range_invalid_ohlc_unavailable(self):
        for key in ('open', 'high', 'low', 'close'):
            for bad in ('NaN', 'Infinity', '-Infinity', '0', '-1', 'bad', 1.0):
                rows = hour()
                rows[10][key] = bad
                with self.subTest(key=key, bad=bad):
                    self.assertIsNone(rng(rows)['value'])
        rows = hour(); rows[3]['high'] = '99.5'  # high below open/close
        self.assertEqual(rng(rows)['reason'], 'Invalid OHLC ordering')
        rows = hour(); del rows[7]['low']
        self.assertIsNone(rng(rows)['value'])

    def test_hourly_range_matches_legacy_float_reference(self):
        vectors = [
            [('100', '101', '99', '100')] * 60,
            [(str(100 + n), str(102 + n), str(99 + n), str(101 + n)) for n in range(60)],
            [('0.5432', '0.5600', '0.5300', '0.5500')] + [('0.55', '0.61', '0.52', '0.6')] * 59,
            [('27123.4', '27150', '27001.1', '27100')] * 59 + [('27100', '27999.9', '26000', '26500')],
        ]
        for vector in vectors:
            rows = [candle(H + n * M, *v) for n, v in enumerate(vector)]
            exact = rng(rows)['value']
            legacy = legacy_range(legacy_rows(rows), H + 3600000)
            with self.subTest(vector=vector[0]):
                self.assertTrue(math.isclose(float(Decimal(exact)), legacy, rel_tol=1e-12))

    def test_hourly_range_exact_decimal_arithmetic(self):
        rows = hour(o='0.1', h='0.3', low='0.1', c='0.1')
        self.assertEqual(rng(rows)['value'], '200')  # float gives 199.99999999999997
        rows = hour(o='3', h='4', low='3', c='3')
        self.assertEqual(Fraction(Decimal(rng(rows)['value'])).limit_denominator(3), Fraction(100, 3))
        self.assertEqual(len(rng(rows)['value'].replace('.', '')), 50)

    def test_hourly_range_open_hour_or_unverified_coverage_unavailable(self):
        self.assertEqual(rng(hour(), as_of=H + 3600000 - 1)['reason'], 'Hour not closed at as_of_ms')
        rows = hour(); rows[5] = candle(H + 5 * M, coverage='UNKNOWN')
        self.assertEqual(rng(rows)['reason'], 'Unverified full-window coverage')
        rows = hour(); rows[5] = candle(H + 5 * M, availability='PARTIAL')
        self.assertIsNone(rng(rows)['value'])
        rows = hour(); rows[59] = candle(H + 59 * M, closed=False)
        self.assertEqual(rng(rows)['reason'], 'Unclosed candle in hour')

    def test_hourly_range_is_separate_from_realized_volatility_per_category(self):
        rows = hour()
        self.assertEqual(i.realized_volatility(rows)['value'], '0')  # flat closes
        self.assertEqual(rng(rows)['value'], '2')
        for category, symbol in [('inverse', 'BTCUSD'), ('spot', 'BTCUSDT')]:
            r = rng(hour(category=category, symbol=symbol))
            self.assertEqual((r['value'], r['category'], r['unit']), ('2', category, 'percent'))

    def test_hourly_range_rejects_malformed_calls(self):
        with self.assertRaises(ValueError):
            i.hourly_range_percent(hour(), hour_start_ms=H + M, as_of_ms=H + 7200000)
        mixed = hour(); mixed[0] = candle(H, symbol='OTHERUSDT')
        with self.assertRaises(ValueError): rng(mixed)
        kinds = hour(); kinds[0] = candle(H, kind='derived')
        with self.assertRaises(ValueError): rng(kinds)


def trade(n, ts, size, *, category='linear', symbol='TESTUSDT', price='100', day=0,
          trusted=True):
    raw = dict(execId=None if not trusted else 'id%d' % n, time=str(ts), price=price,
               size=size, side='Buy')
    if trusted:
        raw.update(availability={'status': 'AVAILABLE', 'reason': None},
                   coverage={'status': 'COMPLETE', 'start_ms': day, 'end_ms': day + DAY,
                             'evidence_refs': ['fixture:archive-day']})
    return a.normalize_trade(raw, category=category, symbol=symbol, source_ref={
        'source_kind': 'synthetic_fixture', 'source_ref': 'fixture:trades', 'source_hash': None,
        'ingestion_run': 'synthetic', 'timestamp_precision': 'ms', 'source_timestamp': str(ts)})


COVERAGE = [{'status': 'COMPLETE', 'start_ms': 0, 'end_ms': DAY, 'evidence_refs': ['fixture:archive-day']}]


def reconcile(trades, candles, *, category='linear', symbol='TESTUSDT', start=0, end=M,
              step='0.001', coverage=COVERAGE, as_of=DAY):
    return reconcile_minute_volume(trades, candles, category=category, symbol=symbol,
        start_ms=start, end_ms=end, qty_step=step, trade_coverage=coverage, as_of_ms=as_of)


def status(report, n=0):
    return report['minutes'][n]['status']


class MinuteVolume(unittest.TestCase):
    def test_minute_volume_exact_match_linear_base_units(self):
        r = reconcile([trade(1, 5, '0.001'), trade(2, 59999, '0.002')], [candle(0, volume='0.003')])
        m = r['minutes'][0]
        self.assertEqual((m['status'], m['trade_volume'], m['candle_volume'], m['difference']),
                         ('MATCH', '0.003', '0.003', '0'))
        self.assertEqual((m['unit'], m['tolerance'], m['trade_count']), ('base_coin', '0', 2))
        self.assertEqual(m['evidence_refs'], ['fixture:archive-day', 'fixture:kline'])
        self.assertEqual(r['summary'], {'MATCH': {'minute_count': 1, 'trade_count': 2,
                                                  'largest_abs_difference': '0'}})

    def test_minute_volume_mismatch_without_legacy_relative_tolerance(self):
        r = reconcile([trade(1, 5, '1000000')], [candle(0, volume='1000000.001')])
        self.assertEqual((status(r), r['minutes'][0]['difference']), ('MISMATCH', '-0.001'))
        # legacy: abs(diff) <= max(1e-8, abs(kline)*1e-6) would have called this 'matched'
        self.assertLessEqual(Decimal('0.001'), max(Decimal('1e-8'), Decimal('1000000.001') * Decimal('1e-6')))
        self.assertEqual(r['summary']['MISMATCH']['largest_abs_difference'], '0.001')

    def test_minute_volume_inverse_contract_quote_units(self):
        trades = [trade(1, 5, '100', category='inverse', symbol='BTCUSD', price='40000'),
                  trade(2, 6, '250', category='inverse', symbol='BTCUSD', price='35000')]
        ok = reconcile(trades, [candle(0, volume='350', category='inverse', symbol='BTCUSD')],
                       category='inverse', symbol='BTCUSD', step='1')
        self.assertEqual((status(ok), ok['unit']), ('MATCH', 'quote_coin_contracts'))
        # Inverse kline turnover is base coin (BTC); comparing it as volume must not match.
        turnover = str(Decimal(100) / 40000 + Decimal(250) / 35000)
        bad = reconcile(trades, [candle(0, volume=turnover, category='inverse', symbol='BTCUSD')],
                        category='inverse', symbol='BTCUSD', step='0.00000001')
        self.assertIn(status(bad), {'MISMATCH', 'OFF_STEP'})

    def test_minute_volume_spot_base_volume_not_turnover(self):
        trades = [trade(1, 5, '0.5', category='spot', symbol='BTCUSDT', price='30000'),
                  trade(2, 6, '0.25', category='spot', symbol='BTCUSDT', price='30010')]
        ok = reconcile(trades, [candle(0, volume='0.75', category='spot', symbol='BTCUSDT')],
                       category='spot', symbol='BTCUSDT', step='0.000001')
        self.assertEqual((status(ok), ok['unit']), ('MATCH', 'base_coin'))
        bad = reconcile(trades, [candle(0, volume='22502.5', category='spot', symbol='BTCUSDT')],
                        category='spot', symbol='BTCUSDT', step='0.000001')
        self.assertEqual(status(bad), 'MISMATCH')

    def test_minute_volume_incomplete_trades_never_match(self):
        c = [candle(0, volume='0.003')]
        t = [trade(1, 5, '0.003')]
        cases = {
            'missing archive day': dict(coverage=[]),
            'boundary minute': dict(coverage=[dict(COVERAGE[0], start_ms=30000)]),
            'unverified coverage': dict(coverage=[dict(COVERAGE[0], status='PARTIAL')]),
            'no evidence': dict(coverage=[dict(COVERAGE[0], evidence_refs=[])]),
            'open minute': dict(as_of=M - 1),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                self.assertEqual(status(reconcile(t, c, **kw)), 'INCOMPLETE_TRADES')
        untrusted = reconcile([trade(1, 5, '0.003', trusted=False)], c)
        self.assertEqual(status(untrusted), 'INCOMPLETE_TRADES')
        # Coverage assembled from two adjacent days spanning the minute is complete.
        split = [dict(COVERAGE[0], end_ms=30000), dict(COVERAGE[0], start_ms=30000)]
        self.assertEqual(status(reconcile(t, c, coverage=split)), 'MATCH')

    def test_minute_volume_incomplete_candles_never_match(self):
        t = [trade(1, 5, '0.003')]
        cases = {'missing (legacy missing_candle)': [],
                 'unclosed': [candle(0, volume='0.003', closed=False)],
                 'unknown coverage': [candle(0, volume='0.003', coverage='UNKNOWN')],
                 'partial availability': [candle(0, volume='0.003', availability='PARTIAL')],
                 'invalid volume': [candle(0, volume='NaN')],
                 'negative volume': [candle(0, volume='-1')]}
        for name, candles in cases.items():
            with self.subTest(name):
                self.assertEqual(status(reconcile(t, candles)), 'INCOMPLETE_CANDLES')
        both = reconcile(t, [], coverage=[])['minutes'][0]
        self.assertEqual(both['status'], 'INCOMPLETE_TRADES')
        self.assertIn('No official 1m candle', both['reasons'])

    def test_minute_volume_off_step_and_unit_unsupported(self):
        r = reconcile([trade(1, 5, '0.001')], [candle(0, volume='0.001')], step='0.01')
        self.assertEqual(status(r), 'OFF_STEP')
        r = reconcile([trade(1, 5, '0.01')], [candle(0, volume='0.015')], step='0.01')
        self.assertEqual(status(r), 'OFF_STEP')
        for kind in ('derived', 'mark', 'index'):
            r = reconcile([trade(1, 5, '0.001')], [candle(0, volume='0.001', kind=kind)])
            self.assertEqual(status(r), 'UNIT_UNSUPPORTED')

    def test_minute_volume_empty_minute_needs_coverage_evidence(self):
        self.assertEqual(status(reconcile([], [candle(0, volume='0')])), 'MATCH')
        self.assertEqual(status(reconcile([], [candle(0, volume='1')])), 'MISMATCH')
        self.assertEqual(status(reconcile([], [candle(0, volume='0')], coverage=[])), 'INCOMPLETE_TRADES')


    def test_minute_volume_rejects_outside_window_evidence_instead_of_false_match(self):
        # Previously, a tick outside [start,end) was silently omitted while
        # its matching candle still returned MATCH.
        in_window = trade(1, 5, '0.003')
        extra = trade(2, M + 5, '8')
        with self.assertRaisesRegex(ValueError, 'trade_outside_reconciliation_window'):
            reconcile([in_window, extra], [candle(0, volume='0.003')])
        with self.assertRaisesRegex(ValueError, 'candle_outside_reconciliation_window'):
            reconcile([in_window], [candle(0, volume='0.003'), candle(M, volume='8')])

    def test_minute_volume_future_trade_never_passes_as_of_gate(self):
        # This timestamp is within the chosen window but the source is not
        # known at as_of_ms.  Even equal sums cannot authorize MATCH.
        t = [trade(1, M - 1, '0.003')]
        report = reconcile(t, [candle(0, volume='0.003')], as_of=M-1)
        self.assertEqual(status(report), 'INCOMPLETE_TRADES')
        self.assertNotEqual(status(report), 'MATCH')

    def test_minute_volume_shared_canonical_units(self):
        from src.analytics.core import quantity_unit
        from src.analytics.comparison import VOLUME_UNITS
        for category, expected in [('spot','base_coin'),('linear','base_coin'),
                                   ('inverse','quote_coin_contracts')]:
            self.assertEqual(VOLUME_UNITS[quantity_unit(category)], expected)

    def test_minute_volume_window_summary_and_identity(self):
        trades = [trade(1, 5, '1'), trade(2, M + 5, '2'), trade(3, 3 * M + 1, '9')]
        candles = [candle(0, volume='1'), candle(M, volume='3'), candle(3 * M, volume='9')]
        r = reconcile(trades, candles, end=4 * M, step='1')
        self.assertEqual([m['status'] for m in r['minutes']],
                         ['MATCH', 'MISMATCH', 'INCOMPLETE_CANDLES', 'MATCH'])
        self.assertEqual(r['summary']['MATCH'], {'minute_count': 2, 'trade_count': 2,
                                                 'largest_abs_difference': '0'})
        self.assertEqual(r['summary']['INCOMPLETE_CANDLES']['largest_abs_difference'], None)
        with self.assertRaises(ValueError): reconcile(trades, candles, symbol='OTHERUSDT')
        with self.assertRaises(ValueError): reconcile(trades, candles + [candle(0)])
        with self.assertRaises(ValueError): reconcile(trades, candles, start=1)
        with self.assertRaises(ValueError): reconcile(trades, candles, step='0')


if __name__ == '__main__':
    unittest.main()
