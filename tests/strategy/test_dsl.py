import copy
import unittest

from src.strategy import validate_strategy, evaluate, preview_text, confirm_preview


def config():
    return {
        'schema_version': '1.0', 'id': 'fixture', 'version': '1',
        'entry': {'long': {'op': 'gt', 'left': {'field': 'close'}, 'right': {'value': '99'}}},
        'exit': {'op': 'lt', 'left': {'field': 'close'}, 'right': {'value': '80'}},
        'order': {'type': 'market', 'latency_bars': 1},
        'risk': {'stop_loss': '0.05', 'take_profit': '0.10', 'trailing_stop': '0',
                 'size': {'mode': 'units', 'value': '1'}, 'leverage': '1',
                 'maintenance_margin': '0.005'},
        'costs': {'fee_rate': '0.001', 'slippage_bps': '0', 'spread_bps': '0',
                  'funding_policy': 'not_applicable'},
        'data_policy': {'partial': 'reject', 'gaps': 'reject'},
    }


class DSLTests(unittest.TestCase):
    def test_validation_independent_of_ambient_decimal_precision(self):
        from decimal import localcontext
        c=config();c['risk']['leverage']='3';c['risk']['maintenance_margin']='0.333333'
        with localcontext() as ctx:
            ctx.prec=6
            validate_strategy(c)

    def test_indicators_and_sessions_numeric_fixtures(self):
        from src.strategy.dsl import value_at
        from decimal import Decimal
        bars=[{'close':str(x),'volume':'1','end_ms':i*60000} for i,x in enumerate([1,2,3,4])]
        for kind,expected in [('sma','3'),('ema','3.25'),('rsi','100'),('vwap','3')]:
            self.assertEqual(value_at({'indicator':kind,'period':3},bars),Decimal(expected))
        self.assertTrue(evaluate({'op':'session','start_minute':0,'end_minute':60,'weekdays':[3]},bars))

    def test_unknown_code_and_float_rejected(self):
        for mutate in (
            lambda c: c.update(code='print(1)'),
            lambda c: c['risk'].update(stop_loss=0.05),
            lambda c: c['entry']['long'].update(op='__import__'),
            lambda c: c['order'].update(latency_bars=0),
            lambda c: c['risk'].update(leverage='NaN'),
        ):
            c = config(); mutate(c)
            with self.assertRaises(ValueError): validate_strategy(c)

    def test_and_or_missing_does_not_pass(self):
        c = config()
        c['entry']['long'] = {'op': 'or', 'args': [c['entry']['long'],
            {'op': 'gt', 'left': {'feature': 'delta'}, 'right': {'value': '0'}}]}
        validate_strategy(c)
        self.assertFalse(evaluate(c['entry']['long'], [{'close': '90', 'end_ms': 10}]))
        self.assertTrue(evaluate(c['entry']['long'], [{'close': '100', 'end_ms': 10}]))

    def test_feature_available_at_cutoff(self):
        expr = {'op': 'gt', 'left': {'feature': 'delta'}, 'right': {'value': '0'}}
        bars = [{'end_ms': 10, 'features': {'delta': {'value': '50', 'available_at_ms': 11,
                'availability': {'status': 'AVAILABLE'}, 'coverage': {'status': 'COMPLETE'}}}}]
        self.assertFalse(evaluate(expr, bars))
        bars[0]['features']['delta']['available_at_ms'] = 10
        self.assertTrue(evaluate(expr, bars))

    def test_cross_requires_previous_bar(self):
        expr = {'op': 'cross_above', 'left': {'field': 'close'}, 'right': {'value': '100'}}
        self.assertFalse(evaluate(expr, [{'close': '101', 'end_ms': 1}]))
        self.assertTrue(evaluate(expr, [{'close': '99', 'end_ms': 1}, {'close': '101', 'end_ms': 2}]))

    def test_preview_must_be_confirmed_and_digest_pinned(self):
        p = preview_text('buy strong price', config(), ['strong means close > 99'])
        with self.assertRaises(ValueError): validate_strategy(p)
        approved = confirm_preview(p, p['preview_id'])
        validate_strategy(approved)
        q = copy.deepcopy(p); q['strategy']['risk']['stop_loss'] = '0.9'
        with self.assertRaises(ValueError): confirm_preview(q, p['preview_id'])


if __name__ == '__main__': unittest.main()
