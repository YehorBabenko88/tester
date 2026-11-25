"""Local no-network versioned DATA <-> RESEARCH compatibility acceptance."""
import base64
import copy
import hashlib
import json
import unittest
from unittest.mock import Mock

from src.ingest.execution_contract import (
    READ_BUDGET,
    RESEARCH_CATEGORIES,
    SOURCE_QUANTITY_UNITS,
    VERSION,
    ExecutionContractError,
    ReadBudgetExceeded,
    _canonical,
    project_storage_execution,
    read_execution_events,
    read_research_trades,
    to_research_trade,
)
from src.storage.bounded_read import ReadBudgetExceeded as StorageBudgetExceeded

MS=1794560000000
START=MS-1
END=MS+1
URL='https://api.bybit.com/v5/market/recent-trade?category=linear&symbol=BTCUSDT&limit=2'
OBSERVED='2026-11-13T20:30:00+00:00'


def sha(x):
    return hashlib.sha256(x).hexdigest()


def fixture(side='Buy',category='linear'):
    raw={'symbol':'BTCUSDT','execId':'fixture-1','time':str(MS),'price':'80000.00','size':'0.1250','side':side}
    result={'list':[raw]}
    body=json.dumps({'retCode':0,'result':result}).encode()
    row_hash=sha(_canonical(raw)); http_hash=sha(body); page_hash=sha(_canonical(result))
    url=URL.replace('category=linear','category='+category)
    run,job='owned-run','owned-job'; page_ref=sha(_canonical([run,job,url,http_hash]))
    ev={'venue':'bybit','category':category,'symbol':'BTCUSDT','dataset':'trades','interval':'',
        'start_ms':START,'end_ms':END,'status':'UNVERIFIED','coverage_status':'UNKNOWN',
        'ingestion_run':run,'source_url':url,'evidence_ref':page_ref,'observed_at_utc':OBSERVED,
        'http_sha256':http_hash,'page_sha256':page_hash,'http_hash_scope':'original_http_body',
        'http_bytes_base64':base64.b64encode(body).decode(),
        'pages':[{'params':{'category':category,'symbol':'BTCUSDT','limit':2},'cursor':None,'next_cursor':None,'count':1}]}
    source={'url':url,'sha256':row_hash,'hash_scope':'canonical_row_json','source_kind':'rest',
            'source_timestamp':str(MS),'fetched_at':OBSERVED,'observed_at_utc':OBSERVED,
            'ingestion_run':run,'evidence_refs':[page_ref]}
    r={'schema_version':'1.0','venue':'bybit','category':category,'symbol':'BTCUSDT','dataset':'trades','interval':'',
            'timestamp_precision':'ms','timestamp_ns':None,'id_origin':'exchange','timestamp_ms':MS,'trade_id':'fixture-1',
            'payload':{'price':'80000','size':'0.125','side':side,'trade_id':'fixture-1',
                       'unit':'source_native_linear_size' if category=='linear' else 'source_native_inverse_contract',
                       'event_timestamp_ms':MS,'publication_timestamp_ms':None,'anonymous_public_execution':True,'source_fields':raw},
            'provenance':{'authentic':True,'observations':[{'source_kind':'rest','timestamp_precision':'ms',
                'authentic_run':True,'source_run_status':'matched','source':source,'evidence':ev,
                'run_id':run,'job_key':job,'evidence_id':sha(_canonical([job,ev]))}]}}
    r['record_key']=sha(_canonical(['bybit',category,'BTCUSDT','trades','fixture-1']))
    r['content_hash']=sha(_canonical({**{k:r.get(k) for k in ('schema_version','timestamp_ms','timestamp_ns','timestamp_precision','payload','trade_id','id_origin')},'timestamp_us':None}))
    return r


def compact_store(rows):
    """Store double mirroring Store.read_bounded: budgets are checked over the
    whole page (bytes, fan-out, distinct evidence) before rows are returned,
    and each distinct evidence document is attached once."""
    rows = copy.deepcopy(rows)
    store = Mock(spec=['read_bounded', 'ingest'])

    def read_bounded(dataset, venue, category, symbol, start_ms, end_ms, interval='', *, limit, budget):
        page = rows[:limit]
        observations = [o for r in page for o in r['provenance']['observations']]
        documents = {o['evidence_id']: o['evidence'] for o in observations}
        usage = {
            'max_row_bytes': sum(len(json.dumps(r['payload'])) for r in page),
            'max_source_bytes': sum(len(json.dumps(o['source'])) for o in observations),
            'max_observations_per_record': max((len(r['provenance']['observations']) for r in page), default=0),
            'max_observations': len(observations),
            'max_evidence_documents': len(documents),
            'max_evidence_bytes': sum(len(json.dumps(d)) for d in documents.values()),
        }
        for key, used in usage.items():
            if used > budget[key]:
                raise StorageBudgetExceeded(key)
        return copy.deepcopy(page)

    store.read_bounded.side_effect = read_bounded
    return store


class ContractTests(unittest.TestCase):
    def project(self,r=None):
        return project_storage_execution(r or fixture(),category='linear',symbol='BTCUSDT',start_ms=START,end_ms=END)

    def test_positive_exact_contract_and_research_projection(self):
        event=self.project()
        self.assertEqual(event['contract_version'],VERSION)
        self.assertEqual((event['price'],event['quantity'],event['taker_side']),('80000','0.125','Buy'))
        self.assertIsNone(event['publication_ms'])
        self.assertEqual(event['source_coverage']['status'],'UNKNOWN')
        self.assertEqual(event['source_receipts'][0]['http_sha256'],fixture()['provenance']['observations'][0]['evidence']['http_sha256'])
        canonical=to_research_trade(event)
        self.assertEqual((canonical['quantity'],canonical['taker_side'],canonical['coverage']['status']),('0.125','Buy','UNKNOWN'))
        self.assertEqual(canonical['availability']['status'],'UNVERIFIED')

    def test_unknown_side_is_not_invented(self):
        self.assertEqual(self.project(fixture(side='Unknown'))['taker_side'],'Unknown')
        self.assertEqual(to_research_trade(self.project(fixture(side='Unknown')))['taker_side'],'Unknown')

    def test_tampered_missing_or_forged_provenance(self):
        edits=[
            lambda r: r['provenance']['observations'][0]['source'].__setitem__('sha256','0'*64),
            lambda r: r['provenance']['observations'][0]['evidence'].pop('http_bytes_base64'),
            lambda r: r['provenance']['observations'][0]['evidence'].__setitem__('page_sha256','0'*64),
            lambda r: r['provenance']['observations'][0]['evidence'].__setitem__('http_sha256','0'*64),
            lambda r: r['provenance']['observations'][0]['source'].__setitem__('evidence_refs',[]),
            lambda r: r['provenance'].__setitem__('authentic',False),
            lambda r: r['provenance']['observations'][0].__setitem__('source_run_status','mismatched'),
            lambda r: r['provenance']['observations'][0]['evidence'].__setitem__('coverage_status','COMPLETE'),
            lambda r: r['provenance']['observations'][0]['evidence'].__setitem__('status','EXHAUSTED'),
            lambda r: r['payload'].__setitem__('publication_timestamp_ms',MS),
            lambda r: r['payload'].__setitem__('event_timestamp_ms',None),
            lambda r: r['payload'].pop('unit'),
            lambda r: r['payload'].__setitem__('size','9'),
            lambda r: r.__setitem__('dataset','open_interest'),
            lambda r: r.__setitem__('symbol','ETHUSDT'),
            lambda r: r['provenance']['observations'][0]['evidence'].pop('start_ms'),
            lambda r: r['provenance']['observations'][0]['evidence'].pop('end_ms'),
            lambda r: r['provenance']['observations'][0].__setitem__('evidence_id','0'*64),
            lambda r: r['provenance']['observations'][0]['evidence']['pages'][0].__setitem__('next_cursor','forged'),
        ]
        for i,edit in enumerate(edits):
            with self.subTest(case=i):
                r=fixture();edit(r)
                with self.assertRaises((ExecutionContractError, KeyError, ValueError)):
                    self.project(r)

    def test_native_inverse_projects_with_its_own_contract_unit(self):
        # Before: to_research_trade refused every inverse event.
        r=fixture(category='inverse')
        event=project_storage_execution(r,category='inverse',symbol='BTCUSDT',start_ms=START,end_ms=END)
        self.assertEqual(event['quantity_unit'],'source_native_inverse_contract')
        canonical=to_research_trade(event)
        # Source-native contract count is carried unchanged (never converted).
        self.assertEqual((canonical['category'],canonical['quantity'],canonical['price']),('inverse','0.125','80000'))
        self.assertEqual(RESEARCH_CATEGORIES,('linear','inverse'))
        self.assertEqual(SOURCE_QUANTITY_UNITS['inverse'],'source_native_inverse_contract')

    def test_cross_unit_events_are_refused(self):
        linear=self.project()
        inverse=project_storage_execution(fixture(category='inverse'),category='inverse',symbol='BTCUSDT',start_ms=START,end_ms=END)
        for event,unit in ((linear,'source_native_inverse_contract'),(inverse,'source_native_linear_size')):
            with self.subTest(category=event['category']), self.assertRaises(ExecutionContractError):
                to_research_trade({**event,'quantity_unit':unit})
        with self.assertRaises(ExecutionContractError):
            to_research_trade({**linear,'category':'spot'})
        # Storage row whose payload unit contradicts its category is refused.
        wrong=fixture(category='inverse');wrong['payload']['unit']='source_native_linear_size'
        with self.assertRaises(ExecutionContractError):
            project_storage_execution(wrong,category='inverse',symbol='BTCUSDT',start_ms=START,end_ms=END)

    def test_spot_has_no_execution_source_and_never_reads(self):
        store=compact_store([fixture()])
        for call in (read_execution_events, read_research_trades):
            with self.subTest(call=call.__name__), self.assertRaises(ExecutionContractError):
                call(store,category='spot',symbol='BTCUSDT',start_ms=START,end_ms=END,limit=2)
        store.read_bounded.assert_not_called()

    def test_inverse_research_read_through_bounded_store(self):
        store=compact_store([fixture(category='inverse')])
        trades=read_research_trades(store,category='inverse',symbol='BTCUSDT',start_ms=START,end_ms=END,limit=2)
        self.assertEqual([(t['category'],t['quantity']) for t in trades],[('inverse','0.125')])
        self.assertEqual(store.read_bounded.call_args.args[2],'inverse')

    def test_late_observation_does_not_invent_publication(self):
        r = fixture()
        observation = r["provenance"]["observations"][0]
        observed = "2026-11-14T21:00:00+00:00"
        observation["source"]["fetched_at"] = observed
        observation["source"]["observed_at_utc"] = observed
        observation["evidence"]["observed_at_utc"] = observed
        observation["evidence_id"] = sha(_canonical([observation["job_key"], observation["evidence"]]))
        event = self.project(r)
        self.assertGreater(event["observed_ms"], event["event_ms"])
        self.assertIsNone(event["publication_ms"])
        self.assertEqual(event["availability"]["status"], "UNVERIFIED")
        self.assertEqual(event["source_coverage"]["status"], "UNKNOWN")

    def test_source_observation_cannot_predate_exchange_event(self):
        r = fixture()
        observation = r["provenance"]["observations"][0]
        earlier = "2026-11-12T23:00:00+00:00"
        observation["source"]["fetched_at"] = earlier
        observation["source"]["observed_at_utc"] = earlier
        observation["evidence"]["observed_at_utc"] = earlier
        observation["evidence_id"] = sha(_canonical([observation["job_key"], observation["evidence"]]))
        with self.assertRaises(ExecutionContractError):
            self.project(r)

    def test_thirteen_calendar_intervals_are_utc_bounded(self):
        from src.analytics.core import interval_bounds
        all_intervals = (
            "1", "3", "5", "15", "30", "60", "120", "240", "360",
            "720", "D", "W", "M",
        )
        self.assertEqual(len(all_intervals), 13)
        for interval in all_intervals:
            with self.subTest(interval=interval):
                start, end = interval_bounds(MS, interval)
                self.assertLessEqual(start, MS)
                self.assertLess(MS, end)
                if interval.isdigit():
                    self.assertEqual(end - start, int(interval) * 60000)
                elif interval == "W":
                    from datetime import datetime, timezone
                    self.assertEqual(
                        datetime.fromtimestamp(start / 1000, timezone.utc).weekday(), 0
                    )
                elif interval == "M":
                    from datetime import datetime, timezone
                    self.assertEqual(
                        datetime.fromtimestamp(start / 1000, timezone.utc).day, 1
                    )

    def test_bounded_read_only_store_and_duplicates(self):
        store=compact_store([fixture()])
        events=read_execution_events(store,category='linear',symbol='BTCUSDT',start_ms=START,end_ms=END,limit=2)
        self.assertEqual(len(events),1)
        store.read_bounded.assert_called_once_with('trades','bybit','linear','BTCUSDT',START,END,'',limit=2,budget=READ_BUDGET)
        store.ingest.assert_not_called()
        store=compact_store([fixture(),fixture()])
        with self.assertRaises(ExecutionContractError):
            read_execution_events(store,category='linear',symbol='BTCUSDT',start_ms=START,end_ms=END,limit=2)
        store=compact_store([fixture()])
        with self.assertRaises(ExecutionContractError):
            read_execution_events(store,category='linear',symbol='BTCUSDT',start_ms=START,end_ms=END,limit=101)
        store.read_bounded.assert_not_called()

    def test_issue104_unbounded_legacy_read_is_refused(self):
        # Before #104 the read used Store.read(compact_provenance=False): every
        # raw envelope and base64 HTTP body per row, with no byte/fan-out cap.
        legacy=Mock(spec=['read'])
        legacy.read.return_value=[fixture()]
        with self.assertRaisesRegex(ExecutionContractError,'resource-bounded'):
            read_execution_events(legacy,category='linear',symbol='BTCUSDT',start_ms=START,end_ms=END,limit=2)
        legacy.read.assert_not_called()

    def test_issue104_budget_fails_closed_with_named_limit(self):
        many=fixture()
        many['provenance']['observations']*=READ_BUDGET['max_observations_per_record']+1
        for rows,budget,name in (
            ([fixture()],{'max_evidence_bytes':64},'max_evidence_bytes'),
            ([many],None,'max_observations_per_record'),
            ([fixture()],{'max_row_bytes':8},'max_row_bytes'),
        ):
            with self.subTest(name=name), self.assertRaises(ReadBudgetExceeded) as caught:
                read_execution_events(compact_store(rows),category='linear',symbol='BTCUSDT',start_ms=START,end_ms=END,limit=2,budget=budget)
            self.assertEqual((caught.exception.reason,caught.exception.budget),('read_budget_exceeded',name))
        store=compact_store([fixture()])
        for bad in ({'max_evidence_bytes':0},{'max_evidence_bytes':True},
                    {'max_evidence_bytes':READ_BUDGET['max_evidence_bytes']+1},{'unknown':1},[]):
            with self.subTest(bad=bad), self.assertRaises(ExecutionContractError):
                read_execution_events(store,category='linear',symbol='BTCUSDT',start_ms=START,end_ms=END,limit=2,budget=bad)
        store.read_bounded.assert_not_called()

    def test_full_page_research_read_refuses_unproven_next_page(self):
        store = compact_store([fixture()])
        # The event itself is authenticated, but exactly reaching the
        # requested limit cannot establish that the next page is empty.
        self.assertEqual(len(read_execution_events(
            store, category="linear", symbol="BTCUSDT",
            start_ms=START, end_ms=END, limit=1,
        )), 1)
        store = compact_store([fixture()])
        with self.assertRaisesRegex(ExecutionContractError, "pagination"):
            read_research_trades(
                store, category="linear", symbol="BTCUSDT",
                start_ms=START, end_ms=END, limit=1,
            )
        store.ingest.assert_not_called()

    def test_duplicate_execid_conflicting_economics(self):
        r=fixture();second=fixture();second['payload']['size']='0.25'
        store=compact_store([r,second])
        with self.assertRaises(ExecutionContractError):
            read_execution_events(store,category='linear',symbol='BTCUSDT',start_ms=START,end_ms=END,limit=2)


if __name__=='__main__':unittest.main()