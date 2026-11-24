"""Explicit synthetic JSON probes from MASTER review comment 6708269302."""
import copy
import unittest
from src.backtest import Backtest, run, restore
from test_dsl import config
from test_engine import snapshot, liquidity as bar_liquidity


def capacities(s, quantities):
    for bar, quantity in zip(s['candles'], quantities):
        bar['liquidity']=bar_liquidity(bar,quantity)
    return s


class ProtectivePersistenceTests(unittest.TestCase):
    def test_entry_consumes_capacity_stop_survives_rebound(self):
        c=config();c['order']['max_participation']='1'
        s=capacities(snapshot([('100','101','99','100'),('100','101','90','100'),
                               ('100','101','99','100')]), ['1','1','1'])
        b=Backtest(c,s);b.step();b.step()
        prefix=b.result()
        self.assertEqual(len(prefix['fills']),1)
        stops=[o for o in prefix['orders'] if o['role']=='exit']
        self.assertEqual(len(stops),1)
        self.assertEqual(stops[0]['status'],'pending')
        self.assertEqual(stops[0]['filled_quantity'],'0')
        b.step();r=b.result()
        self.assertIsNone(r['open_position'])
        self.assertEqual(r['trades'][0]['reason'],'stop_loss')
        self.assertEqual(r['trades'][0]['exit_price'],'100')
        self.assertEqual(r['fills'][-1]['order_id'],stops[0]['id'])

    def test_zero_capacity_latched_target_not_replaced_by_rebound_stop(self):
        c=config();c['order']['max_participation']='1'
        s=capacities(snapshot([('100','101','99','100'),('100','101','99','100'),
                               ('100','112','99','100'),('90','91','89','90')]), ['1','1','0','1'])
        r=run(c,s)
        self.assertEqual(len(r['trades']),1)
        self.assertEqual(r['trades'][0]['reason'],'take_profit')
        self.assertEqual(r['trades'][0]['exit_price'],'90')

    def test_zero_capacity_liquidation_remains_triggered_after_recovery(self):
        c=config();c['order']['max_participation']='1'
        c['risk']['leverage']='10';c['risk']['size']['value']='10';c['risk']['stop_loss']='0.9'
        c['costs']['funding_policy']='ignore_exploratory'
        s=capacities(snapshot([('100','101','99','100'),('100','101','99','100'),
                               ('100','101','80','90'),('100','101','99','100')]),['10','10','0','10'])
        s['initial_equity']='101';s['category']='linear'
        for bar in s['candles']:bar['category']='linear'
        r=run(c,s)
        self.assertIsNone(r['open_position'])
        self.assertEqual(r['trades'][0]['reason'],'liquidation')
        self.assertEqual(r['trades'][0]['exit_price'],'100')

    def test_triggered_stop_survives_gap_and_checkpoint(self):
        c=config();c['order']['max_participation']='1'
        c['data_policy']={'partial':'exploratory','gaps':'continue'}
        s=capacities(snapshot([('100','101','99','100'),('100','101','99','100'),
                               ('100','101','90','100'),('100','101','99','100'),
                               ('100','101','99','100')]),['1','1','0','1','1'])
        s['candles'].pop(3);s['coverage']['status']='GAPS'
        b=Backtest(c,s)
        for _ in range(3):b.step()
        checkpoint=b.checkpoint();replayed=restore(c,s,checkpoint)
        b.step();replayed.step()
        self.assertEqual(b.result(),replayed.result())
        self.assertEqual(replayed.result(),run(c,s))
        r=b.result()
        self.assertIsNone(r['open_position'])
        self.assertEqual(r['trades'][0]['reason'],'stop_loss')
        self.assertEqual(r['trades'][0]['exit_price'],'100')


class LiquidityTimingTests(unittest.TestCase):
    def test_legacy_capacity_cannot_claim_unqualified_open_liquidity(self):
        c=config();c['order']['max_participation']='1'
        s=capacities(snapshot(),['1','1','1'])
        for bar in s['candles']:
            bar['liquidity']={'quantity':'1','evidence_refs':['fixture:executions']}
        with self.assertRaises(ValueError):run(c,s)

    def test_whole_bar_capacity_uses_uncertain_interval_and_close_knowledge(self):
        c=config();c['order']['max_participation']='1'
        s=snapshot([('100','101','99','100')]*3)
        for bar in s['candles']:bar['liquidity']=bar_liquidity(bar,'0.5')
        r=run(c,s);f=r['fills'][0]
        self.assertEqual(f['quantity'],'0.5')
        self.assertEqual(f['time_precision'],'bar')
        self.assertEqual(f['timestamp_ms'],119999)
        self.assertEqual(f['known_at_ms'],120000)
        self.assertEqual(r['overlays'][0]['known_at_ms'],120000)
        self.assertEqual(f['time_window'],{'start_ms':60000,'end_ms':120000})
        self.assertIn('BAR_CAPACITY_TIMING_UNCERTAIN',r['warnings'])

    def test_future_capacity_changes_cannot_mutate_an_earlier_open_fill(self):
        c=config();c['order']['max_participation']='1'
        s=snapshot([('100','101','99','100')]*3)
        for bar in s['candles']:bar['liquidity']=bar_liquidity(bar,'1')
        changed=copy.deepcopy(s);changed['candles'][1]['liquidity']['quantity']='0.5'
        a=Backtest(c,s);b=Backtest(c,changed);a.step();b.step()
        self.assertEqual(a.checkpoint()['prefix_digest'],b.checkpoint()['prefix_digest'])
        a.step();b.step()
        for result in (a.result(),b.result()):
            self.assertFalse(any(f['time_precision']=='modeled_open' for f in result['fills']))
            self.assertFalse(any(f['known_at_ms']<120000 for f in result['fills']))
        self.assertNotEqual(a.result()['fills'][0]['quantity'],b.result()['fills'][0]['quantity'])

    def test_late_partial_unavailable_or_wrong_range_capacity_fails_closed(self):
        c=config();c['order']['max_participation']='1'
        c['data_policy']['partial']='exploratory'
        mutations=[
            lambda q:q.update(available_at_ms=q['end_ms']+1),
            lambda q:q.update(available_at_ms=q['start_ms']),
            lambda q:q.update(end_ms=q['end_ms']+1),
            lambda q:q['availability'].update(status='PARTIAL',reason='incomplete finer evidence'),
            lambda q:q['availability'].update(status='UNAVAILABLE',reason='no evidence'),
            lambda q:q['coverage'].update(status='PARTIAL'),
            lambda q:q.update(provenance=[]),
            lambda q:q.update(evidence_refs=[]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                s=snapshot()
                for bar in s['candles']:bar['liquidity']=bar_liquidity(bar,'1')
                mutate(s['candles'][1]['liquidity'])
                with self.assertRaises(ValueError):run(c,s)


if __name__=='__main__':unittest.main()
