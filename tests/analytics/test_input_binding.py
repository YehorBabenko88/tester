"""Synthetic B/MASTER -> C -> real pinned E validation; no PG/live claim."""
import copy
import importlib.util
import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REFERENCE=Path(__file__).with_name('reference')/'binding'
sys.path[:0]=[str(REFERENCE/'master'),str(REFERENCE/'e')]
from contracts.v1.validate import validate_record,validate_response
from src.integration.storage_api import project_trade,provenance_from_storage
from src.api.scope import bound_response
from src.analytics import SourceConflict
from src.analytics import binding
from tests.analytics.test_analytics import trade

spec=importlib.util.spec_from_file_location('pinned_b_models',REFERENCE/'b'/'models.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)


def storage_row(tid='id',ns=100,price='10',qty='2',run='fixture:run-1',fetched='1970-01-01T00:01:00Z',precision='ns'):
    original=dict(schema_version='1.0',venue='bybit',category='linear',symbol='BTCUSDT',dataset='trades',
        interval='',timestamp_ms=ns//1000000,timestamp_ns=ns if precision=='ns' else None,timestamp_precision=precision,
        payload={'trade_id':tid,'price':price,'size':qty,'side':'Buy'},
        source={'url':'fixture:B/synthetic-trades','fetched_at':fetched,'source_kind':'rest','ingestion_run':run,
                'source_timestamp':str(ns)})
    if precision=='us': original['timestamp_us']=ns//1000
    row=b.normalize(copy.deepcopy(original))
    observation=dict(observation_id='obs:'+run,source_id='source:'+run,source=copy.deepcopy(row['source']),
        raw_envelope=copy.deepcopy(original),run_id=run,job_key='fixture:job',evidence_id='evidence:'+run,
        source_kind='rest',timestamp_precision=precision,source_run_status='matched',authentic_run=True,
        source_ingestion_run=run,verified=False,pagination_status='UNVERIFIED',evidence={},validator_version='fixture')
    row['provenance']=dict(observations=[observation],evidence_refs=[observation['evidence_id']],run_ids=[run],unlinked_sources=[],authentic=True)
    return row


def bind_b(rows,**options):
    return binding.bind_storage_trades(rows,venue='bybit',category='linear',symbol='BTCUSDT',start_ms=0,end_ms=60000,
        information_cutoff_ms=60000,quality_available_at_ms=60000,
        availability={'status':'UNVERIFIED','reason':'Synthetic UNKNOWN history'},
        coverage={'status':'UNKNOWN','start_ms':0,'end_ms':60000,'evidence_refs':['fixture:coverage']},
        trade_projector=project_trade,provenance_projector=provenance_from_storage,**options)


def bind_c(rows,**options):
    return binding.bind_canonical_trades(rows,venue='bybit',category='linear',symbol='BTCUSDT',start_ms=0,end_ms=60000,
        information_cutoff_ms=60000,available_at_ms=lambda record:60000,**options)


class InputBinding(unittest.TestCase):
    def test_pinned_reference_hashes(self):
        pins=json.loads((REFERENCE/'pins.json').read_text(encoding='utf-8'))
        for ref in pins['references']:
            self.assertEqual(hashlib.sha256((REFERENCE/ref['local']).read_bytes()).hexdigest(),ref['local_sha256'])

    def test_exact_b_ns_decimal_and_e_bound_response(self):
        rows=[storage_row('z',100,price='9',qty='10000000000000000000000000000'),
              storage_row('a',900,price='11',qty='0.00000000000000000000000000001')]
        batch=bind_b(rows,input_exhausted=True)
        self.assertEqual(batch.trades[0]['timestamp_ns'],'100')
        for resource,kind in [('candles','candle'),('footprint','footprint')]:
            envelope=batch.response(resource,interval='1',price_step='0.1')
            validate_response(kind,envelope)
            query=SimpleNamespace(venue='bybit',category='linear',symbol='BTCUSDT',start_ms=0,end_ms=60000,limit=1000,interval='1',candle_kind='derived')
            self.assertIs(bound_response(resource,kind,envelope,query),envelope)
            self.assertEqual(envelope['coverage']['status'],'UNKNOWN')
        c=batch.response('candles',interval='1',price_step='1')['data'][0]
        self.assertEqual((c['open'],c['close']),('9','11'))
        self.assertEqual(c['volume'],'10000000000000000000000000000.00000000000000000000000000001')

    def test_all_runs_and_storage_refs_survive_without_mutation(self):
        row=storage_row(); second=storage_row(run='fixture:run-2')['provenance']['observations'][0]
        row['provenance']['observations'].append(second)
        original=copy.deepcopy(row)
        batch=bind_b([row])
        c=batch.response('candles',interval='1',price_step='1')['data'][0]
        self.assertTrue({'fixture:run-1','fixture:run-2'} <= {p['ingestion_run'] for p in c['provenance']})
        links={ref for p in c['provenance'] for ref in p.get('parent_refs',[])}
        self.assertIn('storage:observation:obs:fixture:run-2',links)
        self.assertIn('storage:evidence:evidence:fixture:run-1',links)
        self.assertEqual(row,original)
        self.assertEqual(len(batch.source_links[row['record_key']]['observations']),2)

    def test_full_id_dedup_conflict_and_scoped_market(self):
        row=storage_row()
        c=bind_b([row,row]).response('candles',interval='1',price_step='1')['data'][0]
        self.assertEqual(c['volume'],'2')
        changed=storage_row(qty='3')
        with self.assertRaises(SourceConflict):
            bind_b([row,changed]).response('candles',interval='1',price_step='1')
        other=storage_row();other['symbol']='ETHUSDT'
        with self.assertRaises(binding.BindingUnavailable): bind_b([other])

    def test_canonical_unknown_side_partial_history(self):
        t=trade(side=None)
        t['availability']={'status':'PARTIAL','reason':'Synthetic partial history'}
        t['coverage']['status']='PARTIAL'
        original=copy.deepcopy(t)
        batch=bind_c([t],input_exhausted=True)
        envelope=batch.response('footprint',interval='1',price_step='1')
        self.assertIsNone(envelope['data'][0]['delta'])
        self.assertEqual(envelope['data'][0]['unknown_side_volume'],'2')
        self.assertEqual(envelope['availability']['status'],'PARTIAL')
        self.assertEqual(t,original)
        validate_response('footprint',envelope)

    def test_cutoff_filters_late_trade_and_late_observation(self):
        early=storage_row(); late=storage_row('later',900,qty='999',fetched='1970-01-01T00:01:00.001Z')
        early['provenance']['observations'].append(storage_row(run='fixture:future-run',fetched='1970-01-01T00:01:00.001Z')['provenance']['observations'][0])
        batch=bind_b([early,late])
        c=batch.response('candles',interval='1',price_step='1')['data'][0]
        self.assertEqual(c['volume'],'2')
        self.assertNotIn('fixture:future-run',{p['ingestion_run'] for p in c['provenance']})
        self.assertEqual(batch.excluded_records,1)

    def test_canonical_cutoff_and_missing_availability_time(self):
        t=trade()
        batch=binding.bind_canonical_trades([t],venue='bybit',category='linear',symbol='BTCUSDT',start_ms=0,end_ms=60000,
            information_cutoff_ms=60000,available_at_ms=lambda record:60001)
        envelope=batch.response('candles',interval='1',price_step='1')
        self.assertEqual(envelope['data'],[])
        self.assertNotEqual(envelope['coverage']['status'],'COMPLETE')
        with self.assertRaises(binding.BindingUnavailable):
            binding.bind_canonical_trades([t],venue='bybit',category='linear',symbol='BTCUSDT',start_ms=0,end_ms=60000,
                information_cutoff_ms=60000,available_at_ms=lambda record:None)

    def test_undeclared_or_missing_original_runs_fail_closed(self):
        for status in ['undeclared','mismatched','invalid']:
            row=storage_row();row['provenance']['observations'][0]['source_run_status']=status
            with self.assertRaises(binding.BindingUnavailable): bind_b([row])
        row=storage_row();row['provenance']['unlinked_sources']=[{'source_id':'legacy'}]
        with self.assertRaises(binding.BindingUnavailable): bind_b([row])

    def test_nonexhausted_sequence_cannot_claim_complete(self):
        t=trade();t['availability']={'status':'AVAILABLE','reason':None}
        t['coverage']={'status':'COMPLETE','start_ms':0,'end_ms':60000,'evidence_refs':['fixture:complete']}
        batch=bind_c([t])
        self.assertNotEqual(batch.response('candles',interval='1',price_step='1')['coverage']['status'],'COMPLETE')

    def test_extensions_and_misaligned_output_remain_unsupported(self):
        batch=bind_c([trade()])
        for resource in ['price_levels','statistics','trades']:
            with self.assertRaises(binding.BindingUnavailable): batch.response(resource,interval='1',price_step='1')
        with self.assertRaises(ValueError): batch.response('candles',interval='45s',price_step='1')

    def test_empty_b_snapshot_preserves_upstream_error(self):
        batch=binding.bind_storage_trades([],venue='bybit',category='linear',symbol='BTCUSDT',start_ms=0,end_ms=60000,
            information_cutoff_ms=60000,quality_available_at_ms=60000,
            availability={'status':'UNAVAILABLE','reason':'upstream checksum failure'},
            coverage={'status':'ERROR','start_ms':0,'end_ms':60000,'evidence_refs':['fixture:error']},
            trade_projector=project_trade,provenance_projector=provenance_from_storage)
        envelope=batch.response('candles',interval='1',price_step='1')
        self.assertEqual(envelope['coverage']['status'],'ERROR')
        self.assertIn('upstream checksum failure',envelope['availability']['reason'])
        self.assertEqual(envelope['coverage']['evidence_refs'],['fixture:error'])

    def test_footprint_future_bucket_cannot_claim_complete(self):
        t=trade();t['availability']={'status':'AVAILABLE','reason':None}
        t['coverage']={'status':'COMPLETE','start_ms':0,'end_ms':60000,'evidence_refs':['fixture:complete']}
        batch=binding.bind_canonical_trades([t],venue='bybit',category='linear',symbol='BTCUSDT',start_ms=0,end_ms=60000,
            information_cutoff_ms=30000,available_at_ms=lambda record:30000,input_exhausted=True)
        row=batch.response('footprint',interval='1',price_step='1')['data'][0]
        self.assertNotEqual(row['coverage']['status'],'COMPLETE')

    def test_fetch_ns_after_cutoff_is_excluded(self):
        row=storage_row(fetched='1970-01-01T00:01:00.000000001Z')
        self.assertEqual(row['source']['fetched_at'],'1970-01-01T00:01:00+00:00')
        self.assertEqual(bind_b([row]).response('candles',interval='1',price_step='1')['data'],[])

    def test_response_limit_fails_closed_without_pagination_math(self):
        batch=binding.bind_canonical_trades([trade('1'),trade('2',60000)],venue='bybit',category='linear',symbol='BTCUSDT',
            start_ms=0,end_ms=120000,information_cutoff_ms=120000,available_at_ms=lambda record:120000)
        with self.assertRaises(binding.BindingUnavailable):
            batch.response('candles',interval='1',price_step='1',limit=1)
        envelope=batch.response('candles',interval='1',price_step='1',limit=2)
        query=SimpleNamespace(venue='bybit',category='linear',symbol='BTCUSDT',start_ms=0,end_ms=120000,limit=2,interval='1',candle_kind='derived')
        bound_response('candles','candle',envelope,query)

    def test_b_microseconds_projection_is_source_preserved(self):
        row=storage_row(ns=123000,precision='us')
        t=bind_b([row]).trades[0]
        self.assertEqual((t['timestamp_precision'],t['timestamp_ns']),('us','123000'))
        validate_record('trade',t)

    def test_canonical_exact_event_residual_after_cutoff_is_excluded(self):
        for precision,ns in [('ns','60000000100'),('us','60000001000')]:
            with self.subTest(precision=precision):
                t=trade(ts=60000);t.update(timestamp_precision=precision,timestamp_ns=ns)
                t['provenance'][0]['timestamp_precision']=precision
                batch=binding.bind_canonical_trades([t],venue='bybit',category='linear',symbol='BTCUSDT',
                    start_ms=0,end_ms=120000,information_cutoff_ms=60000,available_at_ms=lambda record:60000)
                self.assertEqual(batch.trades,[])

    def test_b_exact_event_residual_after_cutoff_is_excluded(self):
        for precision,ns in [('ns',60000000100),('us',60000001000)]:
            with self.subTest(precision=precision):
                row=storage_row(ns=ns,precision=precision)
                batch=binding.bind_storage_trades([row],venue='bybit',category='linear',symbol='BTCUSDT',start_ms=0,end_ms=120000,
                    information_cutoff_ms=60000,quality_available_at_ms=60000,
                    availability={'status':'UNVERIFIED','reason':'Synthetic UNKNOWN history'},
                    coverage={'status':'UNKNOWN','start_ms':0,'end_ms':120000,'evidence_refs':[]},
                    trade_projector=project_trade,provenance_projector=provenance_from_storage)
                self.assertEqual(batch.trades,[])

    def test_original_and_normalized_fetch_conflicts_fail_closed(self):
        for value in ['1970-01-01T00:00:59Z','1970-01-01T00:01:00.001Z']:
            row=storage_row();row['provenance']['observations'][0]['source']['fetched_at']=value
            with self.assertRaises(binding.BindingUnavailable): bind_b([row])

    def test_missing_original_fetch_time_fails_closed(self):
        row=storage_row();del row['provenance']['observations'][0]['raw_envelope']['source']['fetched_at']
        with self.assertRaises(binding.BindingUnavailable): bind_b([row])

    def test_original_fetch_cutoff_is_inclusive_at_exact_ns(self):
        for stamp,included in [('1970-01-01T00:00:59.999999999Z',True),
                               ('1970-01-01T00:01:00.000000000Z',True),
                               ('1970-01-01T00:01:00.000000001Z',False)]:
            with self.subTest(stamp=stamp):
                row=storage_row(fetched=stamp)
                original=row['provenance']['observations'][0]['raw_envelope']['source']['fetched_at']
                self.assertEqual(original,stamp)
                self.assertEqual(bool(bind_b([row]).trades),included)

    def test_event_cutoff_is_inclusive_for_both_paths(self):
        for ns,included in [(59999999999,True),(60000000000,True),(60000000001,False)]:
            with self.subTest(ns=ns):
                t=trade(ts=ns//1000000);t.update(timestamp_ns=str(ns),timestamp_precision='ns')
                t['provenance'][0]['timestamp_precision']='ns'
                canonical=binding.bind_canonical_trades([t],venue='bybit',category='linear',symbol='BTCUSDT',
                    start_ms=0,end_ms=120000,information_cutoff_ms=60000,available_at_ms=lambda record:60000)
                stored=binding.bind_storage_trades([storage_row(ns=ns)],venue='bybit',category='linear',symbol='BTCUSDT',
                    start_ms=0,end_ms=120000,information_cutoff_ms=60000,quality_available_at_ms=60000,
                    availability={'status':'UNVERIFIED','reason':'Synthetic'},
                    coverage={'status':'UNKNOWN','start_ms':0,'end_ms':120000,'evidence_refs':[]},
                    trade_projector=project_trade,provenance_projector=provenance_from_storage)
                self.assertEqual(bool(canonical.trades),included)
                self.assertEqual(bool(stored.trades),included)

    def test_event_clock_and_original_event_metadata_conflicts_fail_closed(self):
        row=storage_row(fetched='1970-01-01T00:00:00Z')
        with self.assertRaises(binding.BindingUnavailable): bind_b([row])
        row=storage_row();row['provenance']['observations'][0]['raw_envelope']['timestamp_ns']=200
        with self.assertRaises(binding.BindingUnavailable): bind_b([row])
        t=trade();t.update(timestamp_ns='100',timestamp_precision='ns');t['provenance'][0]['timestamp_precision']='ns'
        with self.assertRaises(binding.BindingUnavailable):
            binding.bind_canonical_trades([t],venue='bybit',category='linear',symbol='BTCUSDT',start_ms=0,end_ms=60000,
                information_cutoff_ms=60000,available_at_ms=lambda record:0)

    def test_original_b_payload_time_fields_and_default_ms(self):
        for precision,ns in [('ns',100),('us',123000),('ms',0)]:
            with self.subTest(precision=precision):
                row=storage_row(ns=ns,precision=precision)
                observation=row['provenance']['observations'][0]
                original=copy.deepcopy(observation['raw_envelope'])
                if precision=='ms':
                    original.pop('timestamp_precision');original.pop('timestamp_ns')
                else:
                    for key in ['timestamp_precision','timestamp_ns','timestamp_us']:
                        if key in original: original['payload'][key]=original.pop(key)
                normalized=b.normalize(copy.deepcopy(original))
                row.update(normalized)
                observation['raw_envelope']=original;observation['source']=copy.deepcopy(normalized['source'])
                self.assertEqual(bind_b([row]).trades[0]['timestamp_precision'],precision)

    def test_calendar_outputs_pass_e_scope_validation(self):
        from tests.analytics.test_analytics import ms
        from src.analytics import interval_bounds
        for interval in ['D','W','M']:
            ts=ms('2024-02-29');start,end=interval_bounds(ts,interval)
            batch=binding.bind_canonical_trades([trade(ts=ts)],venue='bybit',category='linear',symbol='BTCUSDT',
                start_ms=start,end_ms=end,information_cutoff_ms=end,available_at_ms=lambda record:end)
            for resource,kind in [('candles','candle'),('footprint','footprint')]:
                envelope=batch.response(resource,interval=interval,price_step='1')
                query=SimpleNamespace(venue='bybit',category='linear',symbol='BTCUSDT',start_ms=start,end_ms=end,limit=1,interval=interval,candle_kind='derived')
                bound_response(resource,kind,envelope,query)


if __name__=='__main__':unittest.main()
