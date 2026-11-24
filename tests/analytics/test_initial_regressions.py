"""Synthetic reproductions against the initial public footprint signature."""
import unittest
from src.analytics.footprint import aggregate


def sourced(row):
    return dict(row, category='linear', symbol='BTCUSDT', provenance={
        'source_kind':'synthetic_fixture','source_ref':'fixture:initial-regression',
        'source_hash':None,'ingestion_run':'synthetic-test','timestamp_precision':'ms',
        'source_timestamp':'0'})


class InitialRegressions(unittest.TestCase):
    def test_duplicate_exchange_id_is_not_double_counted(self):
        row = dict(timestamp=0, price='1', size='2', side='Buy', trade_id='a')
        self.assertEqual(aggregate([sourced(row), sourced(row)], 60000, '1')[0]['buy'], '2')

    def test_extreme_decimal_sum_is_exact(self):
        rows = [dict(timestamp=0, price='1', size=q, side='Buy', trade_id=str(i))
                for i, q in enumerate(['10000000000000000000000000000', '1', '1'])]
        self.assertEqual(aggregate([sourced(r) for r in rows], 60000, '1')[0]['buy'],
                         '10000000000000000000000000002')


if __name__ == '__main__':
    unittest.main()
