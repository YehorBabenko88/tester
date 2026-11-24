import base64,copy,hashlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from urllib.parse import urlencode
from src.ingest.journal import Journal
from src.ingest.metadata import acquire_instruments,write_handoff


def instrument(**updates):
    return {'symbol':'BTCUSDT','status':'Trading','baseCoin':'BTC','quoteCoin':'USDT','priceFilter':{'tickSize':'0.0100'},'lotSizeFilter':{'qtyStep':'0.000100'},**updates}

class FixtureAPI:
    def __init__(self,pages):self.pages=iter(pages);self.calls=[]
    def call(self,path,params):
        self.calls.append(copy.deepcopy(params));value=next(self.pages)
        if isinstance(value,Exception):raise value
        url='https://api.bybit.com/v5/market'+path+'?'+urlencode(params)
        raw=json.dumps({'retCode':0,'result':value,'time':1700000000123}).encode()
        self.last_response={'url':url,'bytes_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest(),'hash_scope':'original_http_body','status_code':200}
        return value,url

def page(rows=None,cursor='',category='linear'):
    return {'category':category,'list':[instrument()] if rows is None else rows,'nextPageCursor':cursor}

class MetadataTests(unittest.TestCase):
    def acquire(self,pages,category='linear',**kwargs):
        api=FixtureAPI(pages)
        with tempfile.TemporaryDirectory() as d:
            j=Journal(Path(d)/'journal.sqlite')
            try:bundle=acquire_instruments(api,j,category,max_pages=kwargs.pop('max_pages',3),**kwargs)
            finally:j.close()
        return bundle,api

    def test_exact_shared_record_raw_hash_time_run_and_B_kwargs(self):
        bundle,_=self.acquire([page()])
        record=bundle['instruments'][0];artifact=bundle['storage_metadata'][0]
        self.assertEqual((record['tick_size'],record['qty_step']),('0.0100','0.000100'))
        self.assertNotEqual(record['timestamp_ms'],1700000000123)
        self.assertEqual(record['provenance'][0]['source_timestamp'],'1700000000123')
        self.assertEqual(record['provenance'][0]['ingestion_run'],bundle['run']['ingestion_run'])
        self.assertEqual(artifact['source']['ingestion_run'],bundle['run']['ingestion_run'])
        self.assertEqual(set(artifact),{'venue','category','symbol','effective_ms','metadata','source'})
        self.assertEqual(artifact['metadata']['instrument'],record)
        evidence=next(iter(bundle['pages'].values()));raw=base64.b64decode(evidence['response']['bytes_base64'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),record['provenance'][0]['source_hash'])
        self.assertEqual(json.loads(raw)['result']['list'][0],bundle['raw_observations'][0]['row'])
        schema=json.loads(subprocess.check_output(['git','show','9766c0093e2608280875ac56db074414a3d62c69:contracts/v1/market.schema.json']))
        from jsonschema import Draft202012Validator
        Draft202012Validator({'$defs':schema['$defs'],'$ref':'#/$defs/instrument'}).validate(record)
        self.assertEqual(record['coverage']['status'],'UNKNOWN')

    def test_spot_precision_is_not_a_guessed_quantity_step(self):
        row=instrument(lotSizeFilter={'basePrecision':'0.000001','quotePrecision':'0.00001'})
        b,a=self.acquire([page([row],category='spot')],category='spot')
        self.assertFalse(b['instruments']);self.assertFalse(b['storage_metadata'])
        self.assertEqual(b['raw_observations'][0]['row'],row)
        self.assertNotIn('limit',a.calls[0]);self.assertNotIn('cursor',a.calls[0])
        self.assertIn('qty_step',b['rejections'][0]['reason'])

    def test_missing_category_and_step_are_preserved_unprojected(self):
        for category in (None,'spot'):
            p=page();p['category']=category
            b,_=self.acquire([p]);self.assertFalse(b['instruments']);self.assertTrue(b['raw_observations'])
        for field in ('qtyStep','tickSize'):
            row=instrument();row['lotSizeFilter' if field=='qtyStep' else 'priceFilter'].pop(field)
            b,_=self.acquire([page([row])]);self.assertFalse(b['storage_metadata']);self.assertTrue(b['rejections'])

    def test_duplicates_merge_sources_but_conflicts_quarantine_all_versions(self):
        b,_=self.acquire([page(cursor='next'),page()])
        self.assertEqual(len(b['instruments']),1);self.assertEqual(len(b['instruments'][0]['provenance']),2)
        self.assertEqual(len(b['raw_observations']),2)
        row=instrument(status='Closed')
        b,_=self.acquire([page(cursor='next'),page([row])])
        self.assertFalse(b['instruments']);self.assertFalse(b['storage_metadata']);self.assertEqual(len(b['conflicts']),1)
        self.assertEqual(len(b['raw_observations']),2)

    def test_invalid_same_symbol_version_cannot_leave_valid_version_publishable(self):
        row=instrument(lotSizeFilter={})
        b,_=self.acquire([page(cursor='next'),page([row])])
        self.assertFalse(b['storage_metadata']);self.assertFalse(b['instruments'])
        self.assertEqual(len(b['raw_observations']),2)

    def test_scope_hash_and_timestamp_failures_preserve_failure_bytes(self):
        for failure in ('hash','scope','time'):
            api=FixtureAPI([page()]);original=api.call
            def call(path,params):
                result,url=original(path,params)
                if failure=='hash':api.last_response['sha256']='0'*64
                elif failure=='scope':url+='&cursor=forged';api.last_response['url']=url
                else:
                    raw=json.loads(base64.b64decode(api.last_response['bytes_base64']));raw.pop('time')
                    raw=json.dumps(raw).encode();api.last_response.update(bytes_base64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
                return result,url
            api.call=call
            with tempfile.TemporaryDirectory() as d,JournalContext(Path(d)/'j') as j:
                b=acquire_instruments(api,j,'linear',max_pages=1)
                self.assertFalse(b['storage_metadata']);self.assertEqual(b['terminal_condition'],'REQUEST_FAILED')
                self.assertTrue(b['attempts'][0]['response']['bytes_base64'])

    def test_caps_repeated_cursor_and_empty_page_do_not_prove_completeness(self):
        for pages,cap,expected in [([page(cursor='x')],1,'PAGE_CAP'),([page(cursor='x'),page(cursor='x')],3,'REPEATED_CURSOR'),([page([],cursor='x'),page([])],3,'EXHAUSTED')]:
            b,a=self.acquire(pages,max_pages=cap)
            self.assertEqual(b['terminal_condition'],expected)
            self.assertEqual(b['coverage']['status'],'UNKNOWN');self.assertNotEqual(b['run']['status'],'COMPLETE')
            self.assertLessEqual(len(a.calls),cap)

    def test_network_failure_preserves_prior_page_and_failed_attempt(self):
        b,_=self.acquire([page(cursor='x'),RuntimeError('offline')])
        self.assertEqual(b['terminal_condition'],'REQUEST_FAILED');self.assertEqual(len(b['pages']),1)
        self.assertEqual(len(b['attempts']),2);self.assertEqual(b['run']['status'],'FAILED')

    def test_bounds_fail_before_network_and_inverse_maps_explicit_step(self):
        for args in ({'category':None,'max_pages':1},{'category':'linear','max_pages':0},{'category':'linear','max_pages':True}):
            with tempfile.TemporaryDirectory() as d,JournalContext(Path(d)/'j') as j:
                api=FixtureAPI([])
                with self.assertRaises(ValueError):acquire_instruments(api,j,**args)
                self.assertFalse(api.calls)
        b,_=self.acquire([page(category='inverse')],category='inverse')
        self.assertEqual(b['instruments'][0]['qty_step'],'0.000100')

    def test_pinned_B_save_instrument_kwargs_replay_and_revision_without_database(self):
        b,_=self.acquire([page()])
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'bundle.json').write_text(json.dumps(b))
            repo=Path(__file__).resolve().parents[2]
            for sha,prefixes in [('e236395684d661f9464a62a0f6f67803d9000de6',('src/storage/','src/quality/')),('9766c0093e2608280875ac56db074414a3d62c69',('contracts/',))]:
                for name in subprocess.check_output(['git','ls-tree','-r','--name-only',sha],cwd=repo,text=True).splitlines():
                    if name.startswith(prefixes):
                        target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
                        target.write_bytes(subprocess.check_output(['git','show',sha+':'+name],cwd=repo))
            code="""
import copy,json
from pathlib import Path
from unittest.mock import MagicMock
from src.storage.store import Store
from contracts.v1.validate import validate_record
bundle=json.loads(Path('bundle.json').read_text())
a=bundle['storage_metadata'][0]
validate_record('instrument',a['metadata']['instrument'])
s=object.__new__(Store);s.connect=MagicMock();s._lock=MagicMock()
r1=s.save_instrument(**a);r2=s.save_instrument(**a)
assert r1==r2
calls=s.connect.return_value.__enter__.return_value.execute.call_args_list
args=calls[-1].args[1]
assert args[3]==r1 and args[4]==a['effective_ms']
assert args[5].obj==a['metadata'] and args[6].obj==a['source']
changed=copy.deepcopy(a);changed['metadata']['instrument']['tick_size']='0.0200'
assert s.save_instrument(**changed)!=r1
print('shared1.0 valid; B e236 save_instrument accepts exact kwargs; replay stable; changed metadata revises; DB mocked')
"""
            env=os.environ.copy();env['PYTHONPATH']=str(root)
            result=subprocess.run([sys.executable,'-c',code],cwd=root,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_row_cap_never_publishes_unchecked_conflicting_versions(self):
        b,_=self.acquire([page([instrument(),instrument(status='Closed')])],max_rows=1)
        self.assertEqual(b['terminal_condition'],'ROW_CAP')
        self.assertFalse(b['storage_metadata']);self.assertFalse(b['instruments'])

    def test_malformed_result_finishes_failed_and_retains_original_bytes(self):
        b,_=self.acquire([[]])
        self.assertEqual(b['terminal_condition'],'REQUEST_FAILED')
        self.assertEqual(b['run']['status'],'FAILED');self.assertFalse(b['storage_metadata'])
        self.assertTrue(b['attempts'][0]['response']['bytes_base64'])

    def test_row_cap_preserves_entire_response_and_missing_cursor_is_terminal(self):
        b,_=self.acquire([page([instrument(),instrument(symbol='ETHUSDT')],cursor='next')],max_rows=1)
        self.assertEqual(b['terminal_condition'],'ROW_CAP');self.assertEqual(len(b['raw_observations']),1)
        raw=base64.b64decode(next(iter(b['pages'].values()))['response']['bytes_base64'])
        self.assertEqual(len(json.loads(raw)['result']['list']),2)
        p=page();p.pop('nextPageCursor');b,_=self.acquire([p])
        self.assertEqual(b['terminal_condition'],'EXHAUSTED');self.assertEqual(b['coverage']['status'],'UNKNOWN')

    def test_export_replay_is_stable_new_acquisition_is_a_new_revision(self):
        b,_=self.acquire([page()]);other,_=self.acquire([page()])
        self.assertNotEqual(b['run']['ingestion_run'],other['run']['ingestion_run'])
        self.assertNotEqual(b['storage_metadata'],other['storage_metadata'])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'handoff.json';write_handoff(b,p);raw=p.read_bytes();write_handoff(b,p)
            self.assertEqual(p.read_bytes(),raw);self.assertEqual(json.loads(raw),b)

class JournalContext:
    def __init__(self,path):self.journal=Journal(path)
    def __enter__(self):return self.journal
    def __exit__(self,*_):self.journal.close()
