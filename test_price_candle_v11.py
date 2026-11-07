import copy
import unittest
from contracts.v1.validate import ContractError,validate_record
from contracts.v1_1.price_candle import validate_price_candle


def record(kind='official_mark'):
    return dict(schema_version='1.1',venue='bybit',category='linear',symbol='BTCUSDT',timestamp_ms=1700000040000,
                start_ms=1700000040000,end_ms=1700000100000,interval='1',kind=kind,
                open='100',high='101',low='99',close='100.5',is_closed=True,
                provenance=[dict(source_kind='synthetic_fixture',source_ref='fixture://price-candle',source_hash=None,
                    ingestion_run='fixture-run',timestamp_precision='ms',source_timestamp='1700000040000')],
                availability={'status':'UNVERIFIED','reason':'Synthetic fixture'},
                coverage={'status':'UNKNOWN','start_ms':1700000040000,'end_ms':1700000100000,'evidence_refs':[]})


class PriceCandleV11Tests(unittest.TestCase):
    def test_mark_index_have_no_invented_volume(self):
        for kind in ['official_mark','official_index']:
            value=record(kind);validate_price_candle(value)
            self.assertNotIn('volume',value)
            value['volume']=None;validate_price_candle(value)
            value['volume']='0'
            self.assertRaises(ContractError,validate_price_candle,value)
    def test_signed_premium_is_preserved(self):
        value=record('official_premium');value.update(open='-0.001',high='0',low='-0.002',close='-0.0015')
        validate_price_candle(value);self.assertEqual(value['close'],'-0.0015')
    def test_positive_trade_v1_remains_strict_and_unmodified(self):
        value=record();value['schema_version']='1.0'
        self.assertRaises(ContractError,validate_price_candle,value)
        self.assertRaises(ContractError,validate_record,'candle',value)
    def test_nonfinite_inconsistent_and_out_of_range_are_rejected(self):
        for change in [{'high':'NaN'},{'high':'90'},{'close':0.1},{'timestamp_ms':1700000100000},
                       {'end_ms':1700000040000},{'kind':'official_trade'}]:
            with self.subTest(change=change):
                value=record();value.update(change)
                self.assertRaises(ContractError,validate_price_candle,value)
    def test_quality_and_unclosed_are_not_promoted(self):
        value=record();value['availability']['reason']=None
        self.assertRaises(ContractError,validate_price_candle,value)
        value=record();value.update(is_closed=False);value['availability']={'status':'AVAILABLE','reason':None}
        value['coverage'].update(status='COMPLETE',evidence_refs=['fixture-proof'])
        self.assertRaises(ContractError,validate_price_candle,value)
    def test_negative_mark_or_index_is_not_premium(self):
        value=record();value.update(open='-1',high='0',low='-2',close='-1')
        self.assertRaises(ContractError,validate_price_candle,value)

    def test_off_grid_and_wrong_duration_do_not_claim_valid_interval(self):
        for start,end,interval in [(1700000040001,1700000040002,'1'),
                                   (1700000040000,1700000040001,'1'),
                                   (1700000040000,1700086440000,'D'),
                                   (1699833600000,1700438400001,'W'),
                                   (1706745600000,1709337600000,'M')]:
            value=record();value.update(timestamp_ms=start,start_ms=start,end_ms=end,interval=interval)
            value['coverage'].update(start_ms=start,end_ms=end)
            self.assertRaises(ContractError,validate_price_candle,value)

    def test_utc_monday_week_and_leap_calendar_month_are_valid(self):
        for start,end,interval in [(1699833600000,1700438400000,'W'),
                                   (1706745600000,1709251200000,'M')]:
            value=record();value.update(timestamp_ms=start,start_ms=start,end_ms=end,interval=interval)
            value['coverage'].update(start_ms=start,end_ms=end)
            validate_price_candle(value)


if __name__=='__main__':unittest.main()
