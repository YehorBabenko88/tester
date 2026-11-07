"""Independent Issue #8 fixtures; synthetic, never exchange evidence."""
import copy
import unittest
from contracts.v1.validate import validate_record, ContractError


def trade():
    return dict(schema_version='1.0',venue='bybit',category='linear',symbol='BTCUSDT',
                timestamp_ms=1700000000123,timestamp_ns='1700000000123456789',
                timestamp_precision='ns',trade_id='fixture-1',id_origin='exchange',
                price='9007199254740993.001',quantity='0.00000001',taker_side='Buy',
                provenance=[dict(source_kind='synthetic_fixture',source_ref='fixture://trades',
                    source_hash='a'*64,ingestion_run='fixture-run',timestamp_precision='ns',
                    source_timestamp='1700000000.123456789')],
                availability=dict(status='AVAILABLE',reason=None),
                coverage=dict(status='UNKNOWN',start_ms=1700000000000,end_ms=1700000060000,evidence_refs=[]))


def derived_source():
    return dict(source_kind='derived',source_ref='fixture://aggregate',source_hash=None,
                ingestion_run='fixture-run',timestamp_precision='ns',source_timestamp=None,
                parent_refs=['fixture://trades'],method_version='fixture-v1')


class ApprovedContractTests(unittest.TestCase):
    def test_sub_ms_precision_and_decimal_round_trip(self):
        value=trade();validate_record('trade',value)
        self.assertEqual(value['timestamp_ns'],'1700000000123456789')
        self.assertEqual(value['price'],'9007199254740993.001')

    def test_source_ms_must_not_invent_ns(self):
        value=trade();value.update(timestamp_precision='ms',timestamp_ns=None)
        value['provenance'][0].update(timestamp_precision='ms',source_timestamp='1700000000123')
        validate_record('trade',value)
        value['timestamp_ns']='1700000000123000000'
        self.assertRaises(ContractError,validate_record,'trade',value)

    def test_timestamp_units_version_and_exact_financial_values(self):
        for change in [{'timestamp_ms':1700000000124},{'schema_version':'2.0'},
                       {'trade_id':''},{'price':0.1},{'price':'NaN'},{'quantity':'0.000'},
                       {'price':'0'},{'price':'1\n'},{'timestamp_ns':'1700000000123456789\n'}]:
            with self.subTest(change=change):
                value=trade();value.update(change)
                self.assertRaises(ContractError,validate_record,'trade',value)

    def test_synthetic_identity_is_documented_and_not_completeness_proof(self):
        value=trade();value['id_origin']='synthetic'
        self.assertRaises(ContractError,validate_record,'trade',value)
        value['identity_method']='sha256(source identity + exact raw timestamp + payload)'
        validate_record('trade',value)
        value['coverage'].update(status='COMPLETE',evidence_refs=['fixture-proof'])
        self.assertRaises(ContractError,validate_record,'trade',value)

    def test_unavailable_partial_unverified_require_reason(self):
        for status in ['PARTIAL','UNAVAILABLE','UNVERIFIED']:
            value=trade();value['availability']={'status':status,'reason':None}
            self.assertRaises(ContractError,validate_record,'trade',value)
            value['availability']['reason']='Synthetic source incomplete'
            validate_record('trade',value)

    def test_empty_or_exhausted_page_is_not_complete(self):
        value=dict(schema_version='1.0',request_id='fixture-request',endpoint='https://api.bybit.com/v5/market/kline',
                   request_params={'category':'linear'},start_ms=1700000000000,end_ms=1700000060000,
                   cursor_in=None,cursor_out=None,response_count=0,terminal_condition='EMPTY',coverage_status='COMPLETE')
        self.assertRaises(ContractError,validate_record,'pagination',value)
        value['coverage_status']='UNKNOWN';validate_record('pagination',value)
        value.update(terminal_condition='EXHAUSTED',response_count=1,coverage_status='COMPLETE')
        self.assertRaises(ContractError,validate_record,'pagination',value)

    def test_missing_side_never_becomes_verified_bid_ask_delta(self):
        source=trade();source['taker_side']='Unknown';validate_record('trade',source)
        value={k:copy.deepcopy(source[k]) for k in ['schema_version','venue','category','symbol','timestamp_ms','provenance','availability','coverage']}
        value.update(start_ms=1700000000000,end_ms=1700000060000,side_basis='taker',
                     buy_volume='1',sell_volume='0',unknown_side_volume='2',delta='1')
        value['provenance']=[derived_source()]
        self.assertRaises(ContractError,validate_record,'footprint',value)
        value['delta']=None;value['availability']={'status':'PARTIAL','reason':'Unknown taker side'}
        validate_record('footprint',value)
        value['side_basis']='bid_ask'
        self.assertRaises(ContractError,validate_record,'footprint',value)

    def test_closed_candle_and_complete_history_are_separate(self):
        source=trade()
        value={k:copy.deepcopy(source[k]) for k in ['schema_version','venue','category','symbol','timestamp_ms','provenance','availability','coverage']}
        value.update(start_ms=1700000000000,end_ms=1700000060000,interval='1',open='100',high='102',
                     low='99',close='101',volume='3',is_closed=True,kind='derived')
        value['provenance']=[derived_source()]
        validate_record('candle',value);self.assertEqual(value['coverage']['status'],'UNKNOWN')
        value['coverage'].update(status='COMPLETE',evidence_refs=[])
        self.assertRaises(ContractError,validate_record,'candle',value)
        value['coverage']['status']='UNKNOWN';value['high']='90'
        self.assertRaises(ContractError,validate_record,'candle',value)

    def test_ten_distinct_instrument_fixtures(self):
        keys=set()
        for i in range(10):
            source=trade()
            value={k:copy.deepcopy(source[k]) for k in ['schema_version','venue','category','symbol','timestamp_ms','provenance','availability','coverage']}
            value.update(symbol=f'TEST{i}USDT',status='Trading',tick_size='0.01',qty_step='0.001')
            validate_record('instrument',value);keys.add((value['venue'],value['category'],value['symbol']))
        self.assertEqual(len(keys),10)

    def test_signed_funding_range_and_unknown_progress(self):
        source=trade()
        value={k:copy.deepcopy(source[k]) for k in ['schema_version','venue','category','symbol','timestamp_ms','provenance','availability','coverage']}
        value.update(kind='funding',value='-0.001',unit='rate',interval='event')
        validate_record('observation',value)
        value['coverage']['end_ms']=value['coverage']['start_ms']
        self.assertRaises(ContractError,validate_record,'observation',value)
        progress=dict(schema_version='1.0',job_id='fixture-job',timestamp_ms=1700000000000,
                      state='DOWNLOADING',completed_units=2,total_units=None,unit='segments')
        validate_record('progress',progress)
        progress['total_units']=1
        self.assertRaises(ContractError,validate_record,'progress',progress)

    def test_second_precision_cannot_invent_milliseconds(self):
        value=trade();value.update(timestamp_precision='s',timestamp_ns=None)
        value['provenance'][0].update(timestamp_precision='s',source_timestamp='1700000000')
        self.assertRaises(ContractError,validate_record,'trade',value)
        value['timestamp_ms']=1700000000000;validate_record('trade',value)

    def test_derived_record_and_standalone_provenance_require_parents(self):
        source=trade()
        value={k:copy.deepcopy(source[k]) for k in ['schema_version','venue','category','symbol','timestamp_ms','provenance','availability','coverage']}
        value.update(start_ms=1700000000000,end_ms=1700000060000,interval='1',open='100',high='102',
                     low='99',close='101',volume='3',is_closed=True,kind='derived')
        self.assertRaises(ContractError,validate_record,'candle',value)
        p=derived_source();del p['parent_refs']
        self.assertRaises(ContractError,validate_record,'provenance',p)

    def test_footprint_delta_must_be_consistent_with_taker_volumes(self):
        source=trade()
        value={k:copy.deepcopy(source[k]) for k in ['schema_version','venue','category','symbol','timestamp_ms','provenance','availability','coverage']}
        value.update(start_ms=1700000000000,end_ms=1700000060000,side_basis='taker',
                     buy_volume='10000000000000000000000000001',sell_volume='1',
                     unknown_side_volume='0',delta='999')
        value['provenance']=[derived_source()]
        self.assertRaises(ContractError,validate_record,'footprint',value)
        value['delta']='10000000000000000000000000000';validate_record('footprint',value)

    def test_api_adapter_validates_rows_and_inherits_metadata(self):
        from contracts.v1.validate import validate_response
        row=trade()
        envelope=dict(schema_version='1.0',data=[row],next_cursor=None,
                      availability=copy.deepcopy(row['availability']),
                      coverage=copy.deepcopy(row['coverage']),provenance=copy.deepcopy(row['provenance']))
        validate_response('trade',envelope)
        envelope['data'][0]['schema_version']='2.0'
        self.assertRaises(ContractError,validate_response,'trade',envelope)
        envelope['data'][0]['schema_version']='1.0'
        envelope['data'][0]['availability']={'status':'PARTIAL','reason':'Fixture partial archive'}
        self.assertRaises(ContractError,validate_response,'trade',envelope)

    def test_official_comparison_requires_explicit_context(self):
        source=trade()
        value={k:copy.deepcopy(source[k]) for k in ['schema_version','venue','category','symbol','timestamp_ms','provenance','availability','coverage']}
        value.update(field='volume',calculated='1',official='1',difference='0',status='MATCH',reason=None,context=None)
        self.assertRaises(ContractError,validate_record,'comparison',value)
        value.update(status='NOT_COMPARABLE',reason='Missing source completeness and rounding context')
        validate_record('comparison',value)
        value.update(status='MATCH',reason=None,context=dict(calculated_coverage='COMPLETE',official_coverage='COMPLETE',
                     boundary_policy='UTC [start_ms,end_ms)',rounding='exact',tolerance='0',evidence_refs=['fixture-proof']))
        validate_record('comparison',value)
        value['context']['official_coverage']='UNKNOWN'
        self.assertRaises(ContractError,validate_record,'comparison',value)


if __name__=='__main__':unittest.main()

