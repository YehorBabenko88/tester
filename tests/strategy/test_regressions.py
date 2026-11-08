import copy
import unittest
from test_dsl import config
from test_engine import snapshot, liquidity
from src.backtest import run
from src.strategy import preview_text, confirm_preview
from src.strategy.text import compile_text


class RegressionTests(unittest.TestCase):
    def test_partial_protective_exits_leave_no_abandoned_orders(self):
        c=config();c['order']['max_participation']='1'
        s=snapshot([('100','101','99','100')]*2+[('94','94','90','94')]*3)
        for b,q in zip(s['candles'],['1','1','0.4','0.2','1']):
            b['liquidity']=liquidity(b,q)
        c['entry']['long']['right']['value']='99'
        r=run(c,s)
        self.assertIsNone(r['open_position'])
        self.assertFalse(any(o['status'] in ('pending','partially_filled') for o in r['orders']))
        self.assertEqual(len({f['order_id'] for f in r['fills'] if f['role']=='exit'}),1)

    def test_partial_ioc_requires_full_request_margin(self):
        c=config();c['risk']['size']['value']='100';c['order']['max_participation']='0.5'
        s=snapshot()
        for b in s['candles']:b['liquidity']=liquidity(b,'1')
        self.assertEqual(run(c,s)['orders'][0]['status'],'rejected_margin')

    def test_close_exit_executes_before_later_intrabar_target(self):
        c=config();c['exit']={'op':'gte','left':{'field':'close'},'right':{'value':'100'}}
        s=snapshot([('100','101','99','100'),('100','101','99','100'),('100','120','99','100')])
        t=run(c,s)['trades'][0]
        self.assertEqual(t['reason'],'exit_condition')
        self.assertEqual(t['exit_price'],'100')

    def test_open_gap_target_precedes_later_stop(self):
        s=snapshot([('100','101','99','100'),('100','101','99','100'),('120','121','90','100')])
        t=run(config(),s)['trades'][0]
        self.assertEqual(t['reason'],'take_profit')
        self.assertEqual(t['exit_price'],'110.00')

    def test_text_compiles_preview_and_unknown_text_cannot_run(self):
        p=compile_text('long when close > 99',config())
        self.assertTrue(p['requires_confirmation'])
        self.assertEqual(confirm_preview(p,p['preview_id'])['entry']['long']['right']['value'],'99')
        p=compile_text('buy when it feels strong',config())
        self.assertIsNone(p['strategy'])
        with self.assertRaises(ValueError):confirm_preview(p,p['preview_id'])


if __name__=='__main__':unittest.main()
