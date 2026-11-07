import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
from test_dsl import config
from src.backtest import Backtest, run, restore


def provenance():
    return [{'source_kind': 'synthetic_fixture', 'source_ref': 'fixture:hand-calculated',
             'source_hash': None, 'ingestion_run': 'test', 'timestamp_precision': 'ms',
             'source_timestamp': None}]


def snapshot(prices=None):
    prices = prices or [('100', '101', '99', '100'), ('100', '112', '94', '100'), ('100', '101', '99', '100')]
    end = len(prices)*60000
    coverage = {'status': 'COMPLETE', 'start_ms': 0, 'end_ms': end, 'evidence_refs': ['fixture:bars']}
    bars = []
    for i, (o,h,l,c) in enumerate(prices):
        bars.append({'schema_version': '1.0', 'venue': 'bybit', 'category': 'spot', 'symbol': 'BTCUSDT',
                     'timestamp_ms': i*60000, 'start_ms': i*60000, 'end_ms': (i+1)*60000,
                     'interval': '1', 'kind': 'official_trade', 'open': o, 'high': h, 'low': l,
                     'close': c, 'volume': '100', 'is_closed': True,
                     'availability': {'status': 'AVAILABLE', 'reason': None},
                     'coverage': copy.deepcopy(coverage), 'provenance': provenance()})
    return {'schema_version': '1.0', 'venue': 'bybit', 'category': 'spot', 'symbol': 'BTCUSDT',
            'start_ms': 0, 'end_ms': end, 'timeframe_ms': 60000, 'initial_equity': '1000',
            'quantity_step': '0.00000001', 'candles': bars, 'availability': {'status': 'AVAILABLE', 'reason': None},
            'coverage': coverage, 'provenance': provenance(), 'funding': None}


class EngineTests(unittest.TestCase):
    def test_ambiguous_stop_first_fees_and_markers(self):
        r = run(config(), snapshot())
        t = r['trades'][0]
        self.assertEqual(t['exit_price'], '95.00')
        self.assertEqual(Decimal(t['net_pnl']), Decimal('-5.195'))
        self.assertEqual(t['reason'], 'stop_loss')
        self.assertEqual(r['fills'][0]['timestamp_ms'], 60000)
        self.assertEqual(len(r['overlays']), len(r['fills']))
        self.assertEqual({m['fill_id'] for m in r['overlays']}, {f['id'] for f in r['fills']})
        self.assertEqual(r['metrics']['trade_count'], 1)

    def test_deterministic_resume_and_concurrency(self):
        c=config(); s=snapshot(); expected=run(c,s)
        b=Backtest(c,s); b.step(); checkpoint=json.loads(json.dumps(b.checkpoint()))
        resumed=restore(c,s,checkpoint)
        while resumed.step(): pass
        self.assertEqual(expected, resumed.result())
        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual([expected]*3, list(pool.map(lambda _:run(c,s), range(3))))
        checkpoint['next_index']=2
        with self.assertRaises(ValueError): restore(c,s,checkpoint)

    def test_future_mutation_does_not_change_prefix(self):
        s=snapshot(); a=Backtest(config(),s); a.step(); prefix=a.checkpoint()['prefix_digest']
        s['candles'][-1]['close']='100.5'
        b=Backtest(config(),s); b.step()
        self.assertEqual(prefix,b.checkpoint()['prefix_digest'])
        self.assertEqual(a.result()['signals'],b.result()['signals'])

    def test_quality_gaps_duplicates_and_unclosed(self):
        for mutate in (
            lambda s:s['candles'].pop(1),
            lambda s:s['candles'].append(copy.deepcopy(s['candles'][0])),
            lambda s:s['candles'][0].update(is_closed=False),
            lambda s:s['candles'][0]['availability'].update(status='UNVERIFIED'),
            lambda s:s['coverage'].update(status='PARTIAL'),
        ):
            s=snapshot(); mutate(s)
            with self.assertRaises(ValueError): run(config(),s)
        s=snapshot(); s['candles'].pop(1); s['coverage']['status']='GAPS'
        c=config(); c['data_policy']={'partial':'exploratory','gaps':'continue'}
        r=run(c,s)
        self.assertIn('NON_CERTIFIED',r['warnings'])
        self.assertIn('DATA_GAPS',r['warnings'])
        self.assertEqual(r['fills'], [])  # stale pending entry cancelled across gap

    def test_gap_stop_executes_at_adverse_open(self):
        s=snapshot([('100','101','99','100'), ('100','101','99','100'), ('80','82','78','80')])
        r=run(config(),s)
        self.assertEqual(r['trades'][0]['exit_price'],'80')

    def test_decimal_and_no_trades(self):
        c=config(); c['entry']['long']['right']['value']='999'
        r=run(c,snapshot())
        self.assertEqual(r['metrics']['trade_count'],0)
        self.assertIsNone(r['metrics']['profit_factor'])
        self.assertEqual(r['equity_curve'][-1]['equity'],'1000')
        c=config(); c['costs']['fee_rate']='0.000000000000000001'
        with localcontext() as ctx:
            ctx.prec=6
            r=run(c,snapshot())
        self.assertEqual(Decimal(r['trades'][0]['net_pnl']),Decimal('-5.00000000000000019500'))

    def test_short_and_funding(self):
        c=config(); c['entry']={'short':c['entry']['long']}; c['costs']['funding_policy']='required'
        s=snapshot(); s['category']='linear'
        for b in s['candles']: b['category']='linear'
        s['funding']={'availability':{'status':'AVAILABLE','reason':None}, 'coverage':s['coverage'],
                      'provenance':provenance(), 'expected_timestamps':[60000,120000],
                      'events':[{'timestamp_ms':60000,'rate':'0.01','mark_price':'100'},
                                {'timestamp_ms':120000,'rate':'0','mark_price':'100'}]}
        # Opening funding occurs before new fills: no artificial receipt at entry.
        r=run(c,s)
        self.assertEqual(r['trades'][0]['reason'],'stop_loss')
        self.assertEqual(Decimal(r['trades'][0]['gross_pnl']),Decimal('-5'))
        self.assertEqual(r['metrics']['funding_costs'],'0')

    def test_funding_held_position_and_missing_schedule(self):
        c=config(); c['costs']['funding_policy']='required'
        s=snapshot([('100','101','99','100')]*3); s['category']='linear'
        for b in s['candles']: b['category']='linear'
        s['funding']={'availability':{'status':'AVAILABLE','reason':None},'coverage':s['coverage'],
            'provenance':provenance(),'expected_timestamps':[120000],
            'events':[{'timestamp_ms':120000,'rate':'0.01','mark_price':'100'}]}
        self.assertEqual(run(c,s)['metrics']['funding_costs'],'1.00')
        s['funding']['events']=[]
        with self.assertRaises(ValueError): run(c,s)

    def test_limit_stop_latency_spread_and_partial_ioc(self):
        c=config(); c['order']={'type':'limit','offset_bps':'100','latency_bars':1,'max_participation':'0.5'}
        s=snapshot()
        for b in s['candles']: b['liquidity']={'quantity':'1','evidence_refs':['fixture:ticks']}
        r=run(c,s)
        self.assertEqual(r['fills'][0]['quantity'],'0.5')
        self.assertEqual(r['fills'][0]['price'],'99.00')
        self.assertEqual(r['orders'][0]['status'],'partial_cancelled')
        c=config(); c['order']={'type':'stop','offset_bps':'100','latency_bars':1}
        self.assertEqual(run(c,snapshot())['fills'][0]['price'],'101.00')
        c=config(); c['order']['latency_bars']=2; c['costs']['spread_bps']='20'
        self.assertEqual(run(c,snapshot())['fills'][0]['timestamp_ms'],120000)
        self.assertEqual(run(c,snapshot())['fills'][0]['price'],'100.100')

    def test_trailing_updates_next_bar(self):
        c=config(); c['risk']['trailing_stop']='0.02'; c['risk']['take_profit']='0.9'
        s=snapshot([('100','101','99','100'),('100','104','99','103'),('103','104','100','102')])
        t=run(c,s)['trades'][0]
        self.assertEqual(t['exit_price'],'100.94')  # previous CLOSE, not future high
        self.assertEqual(t['reason'],'trailing_stop')

    def test_margin_rejection_and_liquidation(self):
        c=config(); c['risk']['size']['value']='100'
        self.assertEqual(run(c,snapshot())['orders'][0]['status'],'rejected_margin')
        c=config(); c['risk']['leverage']='10'; c['risk']['stop_loss']='0.9'
        c['risk']['size']={'mode':'equity_fraction','value':'1'}; c['costs']['funding_policy']='ignore_exploratory'
        s=snapshot([('100','101','99','100'),('100','101','80','90')]); s['category']='linear'
        for b in s['candles']: b['category']='linear'
        r=run(c,s)
        self.assertEqual(r['trades'][0]['reason'],'liquidation')
        self.assertLess(Decimal(r['equity_curve'][-1]['equity']), Decimal('100'))


if __name__=='__main__': unittest.main()
