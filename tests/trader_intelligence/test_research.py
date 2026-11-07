import csv
import io
import json
import unittest
from test_core import row
from src.trader_intelligence.imports import import_bytes
from src.trader_intelligence.reconstruction import reconstruct
from src.trader_intelligence.core import analyze, decimal


def manifest(**changes):
    m = dict(source='authorized_export', source_revision='v1', permission='USER_AUTHORIZED',
             rights_ref='consent:fixture-only', coverage='COMPLETE', flat_start=True,
             position_mode='ONE_WAY', instrument_model='LINEAR', synthetic=True)
    m.update(changes)
    return m


def dataset(rows=None, **changes):
    return import_bytes(json.dumps(rows or [row()]).encode(), 'json', manifest(**changes))


class ImportTests(unittest.TestCase):
    def test_memory_budget_1000_rows(self):
        import tracemalloc
        raw=json.dumps([row(event_id=str(i)) for i in range(1000)]).encode()
        tracemalloc.start()
        try:
            result=import_bytes(raw,'json',manifest())
            _,peak=tracemalloc.get_traced_memory()
            self.assertEqual(len(result['events']),1000)
            self.assertLess(peak,32*1024*1024)
        finally: tracemalloc.stop()
    def test_revision_selected_as_of(self):
        rows=[row(),row(revision='v2',observed_at_ms=2500,price='110')]
        self.assertEqual(analyze(rows,start_ms=0,end_ms=3000,as_of_ms=2100)['events'],1)
        self.assertEqual(analyze(rows,start_ms=0,end_ms=3000,as_of_ms=3000)['events'],1)

    def test_huge_decimal_exponent_rejected(self):
        with self.assertRaises(ValueError): decimal('1e999999999',positive=True)

    def test_digest_pins_query(self):
        self.assertNotEqual(analyze([row()],start_ms=0,end_ms=3000)['digest'],
                            analyze([row()],start_ms=0,end_ms=4000)['digest'])

    def test_csv_json_equivalent(self):
        f = io.StringIO(); w = csv.DictWriter(f, fieldnames=list(row())); w.writeheader(); w.writerow(row())
        a = import_bytes(f.getvalue().encode(), 'csv', manifest())
        b = dataset()
        self.assertEqual(a['events'], b['events'])
        self.assertNotEqual(a['dataset_revision'], b['dataset_revision'])  # original evidence bytes differ

    def test_rights_fail_closed(self):
        for changes in ({'permission':'PUBLIC_UI'}, {'rights_ref':''}, {'source':'bybit_ui'}):
            with self.assertRaises(ValueError): dataset(**changes)

    def test_bound_before_parse(self):
        with self.assertRaises(ValueError): import_bytes(b'['*100, 'json', manifest(), max_bytes=50)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ValueError): import_bytes(b'[{"source":"a","source":"b"}]','json',manifest())

    def test_revision_conflict(self):
        with self.assertRaises(ValueError): dataset([row(),row(price='101')])

    def test_revisions_replace_not_double_count(self):
        d = dataset([row(), row(revision='v2',observed_at_ms=2500,price='110')])
        self.assertEqual(len(d['events']),2)

    def test_extra_sensitive_fields_rejected(self):
        with self.assertRaises(ValueError): dataset([row(email='private@example.com')])


class ReconstructionTests(unittest.TestCase):
    def test_partial_close_gross_unknown_net(self):
        d = dataset([row(), row(event_id='close',side='SELL',quantity='1',price='120',timestamp_ms=2100,observed_at_ms=2200)])
        r = reconstruct(d['events'],d['manifest'])
        self.assertEqual(r['status'],'INFERRED')
        self.assertEqual(r['positions'][0]['quantity'],'1.0')
        self.assertEqual(r['gross_realized_pnl'],'20.00')
        self.assertIsNone(r['net_realized_pnl'])
        self.assertIsNone(r['positions'][0]['sl'])

    def test_unknown_initial_inventory(self):
        d=dataset(flat_start=False)
        self.assertEqual(reconstruct(d['events'],d['manifest'])['status'],'UNKNOWN')

    def test_hedge_and_inverse_unknown(self):
        for change in ({'position_mode':'HEDGE'},{'instrument_model':'INVERSE'}):
            d=dataset(**change)
            self.assertEqual(reconstruct(d['events'],d['manifest'])['status'],'UNKNOWN')

    def test_same_timestamp_order_ambiguous(self):
        d=dataset([row(),row(event_id='other',side='SELL')])
        self.assertEqual(reconstruct(d['events'],d['manifest'])['status'],'UNKNOWN')

    def test_two_instruments_separate(self):
        d=dataset([row(),row(symbol='ETHUSDT')])
        self.assertEqual(len(reconstruct(d['events'],d['manifest'])['positions']),2)
        self.assertIsNone(reconstruct(d['events'],d['manifest'])['gross_realized_pnl'])

    def test_closed_cycle_statistics_are_gross_model_only(self):
        d=dataset([row(),row(event_id='exit',timestamp_ms=2100,observed_at_ms=2200,side='SELL',price='120')])
        r=reconstruct(d['events'],d['manifest'])
        self.assertEqual(r['model_statistics']['completed_cycles'],1)
        self.assertEqual(r['model_statistics']['gross_win_rate'],'1')
        self.assertIsNone(r['win_rate'])

    def test_no_anonymous_reconstruction(self):
        d=dataset([row(trader_id=None,kind='ANONYMOUS_MARKET_PRINT',validity='OBSERVED')])
        self.assertEqual(reconstruct(d['events'],d['manifest'])['status'],'UNKNOWN')


if __name__=='__main__': unittest.main()
