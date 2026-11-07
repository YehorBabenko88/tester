"""Disposable PostgreSQL test of immutable A3098 -> B e236 -> shared-v1 read.

python -m tests.ingest.pinned_roundtrip --work-root work/pinned-pair --pg-bin "C:/Program Files/PostgreSQL/18/bin"
Only synthetic parser/API fixtures, no market downloads or trader attribution.
Requires git objects for the three pins, psycopg and jsonschema. The command
creates a NEW isolated loopback cluster and stops it, never using a user DB.
"""
import argparse
import copy
import gzip
import hashlib
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import unittest
import uuid
from datetime import datetime,timezone
from pathlib import Path

PRODUCER_SHA='3098d55f2bb41c5389f83b955d87a3050ca52f85'
STORAGE_SHA='e236395684d661f9464a62a0f6f67803d9000de6'
CONTRACT_SHA='9766c0093e2608280875ac56db074414a3d62c69'


def write_json(path,value):
    Path(path).write_text(json.dumps(value,indent=2),encoding='utf-8')


def cleanup_cluster(ctl,data,root,flags):
    """Check only this newly-created cluster, even after failed pg_ctl start."""
    status=subprocess.run([str(ctl),'-D',str(data),'status'],capture_output=True,text=True,creationflags=flags)
    (root/'pg-cleanup-status.log').write_text(status.stdout+status.stderr,encoding='utf-8')
    if status.returncode==0:
        stop=subprocess.run([str(ctl),'-D',str(data),'-m','fast','-w','-t','30','stop'],capture_output=True,text=True,creationflags=flags)
        (root/'pg-cleanup-stop.log').write_text(stop.stdout+stop.stderr,encoding='utf-8')
        if stop.returncode: raise RuntimeError('Disposable cluster shutdown failed: '+str(root))
        return {'running_before_cleanup':True,'stopped':True,'stop_returncode':0}
    if status.returncode==3:return {'running_before_cleanup':False,'stopped':True,'status_returncode':3}
    raise RuntimeError('Cannot verify disposable cluster cleanup: '+str(root))


def extract_snapshot(repo,root):
    snapshot=root/'snapshot'; snapshot.mkdir()
    files={}
    for role,sha,prefixes in [('producer',PRODUCER_SHA,('src/ingest/',)),('storage',STORAGE_SHA,('src/storage/','src/quality/')),('shared',CONTRACT_SHA,('contracts/',))]:
        resolved=subprocess.check_output(['git','rev-parse',sha],cwd=repo,text=True).strip()
        if resolved!=sha: raise ValueError('Immutable reference mismatch')
        names=subprocess.check_output(['git','ls-tree','-r','--name-only',sha],cwd=repo,text=True).splitlines()
        for name in names:
            if not name.startswith(prefixes): continue
            data=subprocess.check_output(['git','show',sha+':'+name],cwd=repo)
            target=snapshot/name; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
            files[name]={'role':role,'commit':sha,'sha256':hashlib.sha256(data).hexdigest()}
    # Test scaffold only. Production files above are copied byte-for-byte.
    test=snapshot/'tests/ingest/pinned_roundtrip.py'; test.parent.mkdir(parents=True)
    test.write_bytes(Path(__file__).read_bytes())
    for name in ['tests/__init__.py','tests/ingest/__init__.py']:
        (snapshot/name).write_text('',encoding='utf-8')
    manifest={'producer_sha':PRODUCER_SHA,'storage_sha':STORAGE_SHA,'contract_sha':CONTRACT_SHA,'production_files':files}
    write_json(root/'pins.json',manifest)
    return snapshot,manifest


def guard_child_context(root,verify_restart=False):
    """Read-only gate shared by both child modes, before any Store/DDL call.

    The parent supplies digests separately from child-readable metadata files.
    This is an accidental/misdirected fixture guard, not a hostile OS sandbox.
    """
    root=Path(root).resolve(); snapshot=(root/'snapshot').resolve()
    def checked_json(name,env_key):
        data=(root/name).read_bytes()
        if hashlib.sha256(data).hexdigest()!=os.environ[env_key]:
            raise AssertionError('Parent metadata digest mismatch: '+name)
        return json.loads(data)
    manifest=checked_json('pins.json','PINNED_PAIR_PINS_SHA256')
    expected={'producer_sha':PRODUCER_SHA,'storage_sha':STORAGE_SHA,'contract_sha':CONTRACT_SHA}
    if any(manifest.get(k)!=v for k,v in expected.items()):raise AssertionError('Unexpected source pin')
    if not manifest['production_files']:raise AssertionError('Empty pinned snapshot')
    for name,info in manifest['production_files'].items():
        path=(snapshot/name).resolve()
        if not path.is_relative_to(snapshot) or hashlib.sha256(path.read_bytes()).hexdigest()!=info['sha256']:
            raise AssertionError('Pinned source changed')
    state=None
    if verify_restart:
        state=checked_json('restart-state.json','PINNED_PAIR_RESTART_SHA256')
        import re
        fixture=root/'fixtures/test_replay_and_journal_restart'
        if not re.fullmatch(r'pair_[0-9a-f]{32}',state['database']):raise AssertionError('Unexpected restart database')
        if Path(state['storage_root']).resolve()!=fixture.resolve():raise AssertionError('Unexpected restart storage root')
        if Path(state['handoff']).resolve()!=(fixture/'equivalent/reopened.json').resolve():raise AssertionError('Unexpected restart handoff')
    import psycopg
    dsn=os.environ['PINNED_PAIR_DSN']
    with psycopg.connect(dsn) as conn:
        actual=Path(conn.execute('SHOW data_directory').fetchone()[0]).resolve()
        if actual!=(root/'pgdata').resolve():raise AssertionError('Refusing a database outside the new disposable cluster')
    modules={}
    for key,name in [('archive','src.ingest.archive'),('journal','src.ingest.journal'),('handoff','src.ingest.handoff'),('rest','src.ingest.rest'),('models','src.storage.models'),('store_module','src.storage.store'),('migrations','src.storage.migrations'),('validator','contracts.v1.validate')]:
        module=importlib.import_module(name)
        relative=name.replace('.','/')+'.py'
        if relative not in manifest['production_files'] or Path(module.__file__).resolve()!=(snapshot/relative).resolve():
            raise AssertionError('Imported an unpinned implementation')
        modules[key]=module
    return psycopg,dsn,modules,state


class PinnedPairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(os.environ['PINNED_PAIR_ROOT'])
        cls.pg,cls.admin_dsn,modules,_=guard_child_context(cls.root)
        for name,module in modules.items():setattr(cls,name,module)
        cls.metrics={}

    @classmethod
    def tearDownClass(cls): write_json(cls.root/'case-metrics.json',cls.metrics)

    def setUp(self):
        from psycopg import sql
        self.case=self.id().rsplit('.',1)[-1]
        self.directory=self.root/'fixtures'/self.case; self.directory.mkdir(parents=True)
        self.dbname='pair_'+uuid.uuid4().hex
        with self.pg.connect(self.admin_dsn,autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.dbname)))
        self.dsn=self.pg.conninfo.make_conninfo(self.admin_dsn,dbname=self.dbname)
        with self.pg.connect(self.dsn) as conn: self.migrations.migrate(conn)
        self.store=self.store_module.Store(self.dsn,storage_root=self.directory)
        self.assertTrue(self.store.acquisition_status()['allowed'])

    def bundle(self,name,rows_by_file):
        directory=self.directory/name; directory.mkdir()
        j=self.journal.Journal(directory/'journal.sqlite')
        run=j.start_run('synthetic_pinned_pair_fixture',{'fixture':name})
        try:
            for index,rows in enumerate(rows_by_file):
                path=directory/(str(index)+'.csv.gz')
                body='timestamp,symbol,side,size,price,trdMatchID\n'+''.join(','.join(row)+'\n' for row in rows)
                path.write_bytes(gzip.compress(body.encode()))
                source={'url':f'https://public.bybit.com/trading/BTCUSDT/synthetic-pair-{index}.csv.gz','ingestion_run':run}
                j.save(str(index),'observed',{},self.archive.iter_trade_records(path,'linear','BTCUSDT',source),run_id=run)
            j.finish_run(run,'PARTIAL'); j.export(directory/'handoff.json')
        finally: j.close()
        package=json.loads((directory/'handoff.json').read_text())
        for item in package['records']:
            self.assertEqual(item['ingestion_run'],run)
            self.assertTrue(all(ref in package['evidence'] for ref in item['evidence_refs']))
            if item['contract_record'] is not None:
                self.validator.validate_record(item['contract_kind'],item['contract_record'])
            self.assertNotEqual(item['coverage']['status'],'COMPLETE')
        return package,directory

    def equivalent(self,name='equivalent'):
        return self.bundle(name,[[['1704067200.123','BTCUSDT','Buy','2.00','1.0','pair-equal']], [['1704067200.123','BTCUSDT','Buy','2.0','1.00','pair-equal']]])

    def publish(self,package):
        for batch in package['storage_batches']:
            for row in batch['records']:
                self.assertEqual(row['source']['ingestion_run'],batch['ingestion_run'])
            result=self.store.ingest(batch['records'],batch['evidence'],batch['batch_id'],batch['checkpoint'],run_id=batch['ingestion_run'])
            self.assertFalse(result['verified'])

    def read(self,start=1704067200123,end=1704067200124,dataset='trades',interval=''):
        return self.store.read(dataset,'bybit','linear','BTCUSDT',start,end,interval,limit=100)

    def shared_read(self,row):
        proof=row['provenance']; self.assertTrue(proof['authentic'])
        sources=[]
        for obs in proof['observations']:
            self.assertEqual(obs['source_run_status'],'matched')
            self.assertEqual(obs['source_ingestion_run'],obs['run_id'])
            self.assertTrue(obs['authentic_run'])
            source=obs['source']
            sources.append({'source_kind':{'archive':'bybit_archive','rest':'bybit_rest','import':'synthetic_fixture'}[obs['source_kind']],'source_ref':source['url'],'source_hash':source.get('sha256'),'ingestion_run':obs['run_id'],'timestamp_precision':obs['timestamp_precision'],'source_timestamp':source.get('source_timestamp')})
        value={k:row[k] for k in ('schema_version','venue','category','symbol','timestamp_ms')}
        coverage={'status':'UNKNOWN','start_ms':row['timestamp_ms'],'end_ms':row['timestamp_ms']+1,'evidence_refs':proof['evidence_refs']}
        value.update(provenance=sources,availability={'status':'PARTIAL','reason':'Synthetic pinned consumer fixture; not live availability'},coverage=coverage)
        if row['dataset']=='trades':
            ns=row['timestamp_ns']
            if row['timestamp_precision']=='us': ns=row['timestamp_us']*1000
            value.update(timestamp_ns=str(ns) if ns is not None else None,timestamp_precision=row['timestamp_precision'],trade_id=row['trade_id'],id_origin=row['id_origin'],price=row['payload']['price'],quantity=row['payload']['size'],taker_side=row['payload']['side'])
            self.validator.validate_record('trade',value)
        else:
            p=row['payload']
            value.update(start_ms=row['timestamp_ms'],end_ms=self.rest.next_time(row['timestamp_ms'],row['interval']),interval=row['interval'],kind='official_trade',is_closed=True,**{k:p[k] for k in ('open','high','low','close','volume')})
            self.validator.validate_record('candle',value)
        ledger=self.store.coverage_evidence(row['venue'],row['category'],row['symbol'],row['dataset'],row['interval'],coverage['start_ms'],coverage['end_ms'])
        by_ref={entry['evidence_id']:entry for entry in ledger}
        self.assertTrue(all(ref in by_ref for ref in proof['evidence_refs']))
        for obs in proof['observations']:
            self.assertIn(obs['run_id'],by_ref[obs['evidence_id']]['run_ids'])
            manifest=obs['evidence']['acquisition_manifest']
            self.assertIn(obs['run_id'],manifest['runs'])
            self.assertTrue(all(ref in manifest['evidence'] for ref in obs['evidence']['evidence_refs']))
        return value

    def counts(self):
        from psycopg import sql
        with self.pg.connect(self.dsn) as conn:
            tables=[r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='tester_storage' AND table_type='BASE TABLE' ORDER BY table_name")]
            return {name:conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier('tester_storage',name))).fetchone()[0] for name in tables}

    def test_equivalent_decimals_two_observations_one_trade(self):
        package,_=self.equivalent(); self.assertFalse(package['conflicts'])
        self.assertEqual(len(package['raw_record_versions']),2)
        self.publish(package); rows=self.read(); self.assertEqual(len(rows),1)
        proof=rows[0]['provenance']; self.assertEqual(len(proof['observations']),2)
        self.assertEqual(len({o['source_id'] for o in proof['observations']}),2)
        shared=self.shared_read(rows[0]); self.assertEqual((shared['price'],shared['quantity']),('1','2'))
        self.metrics[self.case]={'economic_executions':1,'source_observations':2,'raw_versions':2,'shared_read_valid':True}

    def test_original_run_exact_match_and_atomic_rejection(self):
        baseline,_=self.equivalent('baseline'); self.publish(baseline)
        prior_job=baseline['storage_batches'][0]['batch_id']; prior=self.store.checkpoint(prior_job)
        candidate,_=self.bundle('candidate',[[['1704067200.123','BTCUSDT','Buy','2','1','valid-before-rejection'],['1704067200.123','BTCUSDT','Buy','2','1','rejected-second']]])
        batch=candidate['storage_batches'][0]
        variants=[('wrong_caller','wrong',batch['ingestion_run']),('missing_caller',None,batch['ingestion_run']),('wrong_original',batch['ingestion_run'],'wrong'),('empty_original',batch['ingestion_run'],''),('null_original',batch['ingestion_run'],None),('too_long_original',batch['ingestion_run'],'x'*301)]
        for label,caller,original in variants:
            with self.subTest(case=label):
                rows=copy.deepcopy(batch['records']); rows[1]['source']['ingestion_run']=original
                before=self.counts(); kwargs={} if caller is None else {'run_id':caller}
                with self.assertRaises(ValueError):
                    self.store.ingest(iter(rows),batch['evidence'],'reject-'+label,batch['checkpoint'],**kwargs)
                self.assertEqual(self.counts(),before)
                self.assertEqual(self.store.checkpoint(prior_job),prior)
        self.shared_read(self.read()[0])
        self.metrics[self.case]={'negative_variants':len(variants),'whole_schema_counts_unchanged':True,'prior_checkpoint_unchanged':True,'original_run_status':'matched'}

    def test_original_ns_ms_us_preserved_through_shared_read(self):
        package,_=self.bundle('precision',[[['1704067200.123456789','BTCUSDT','Buy','1','2','ns'],['1704067200123','BTCUSDT','Sell','1','2','ms'],['1704067200.123456','BTCUSDT','Buy','1','2','us']]])
        self.publish(package); rows={r['trade_id']:r for r in self.read()}
        self.assertEqual(rows['ns']['timestamp_ns'],1704067200123456789)
        self.assertIsNone(rows['ms']['timestamp_ns']); self.assertEqual(rows['us']['timestamp_us'],1704067200123456)
        shared={key:self.shared_read(row) for key,row in rows.items()}
        self.assertEqual(shared['ns']['timestamp_ns'],'1704067200123456789')
        self.assertIsNone(shared['ms']['timestamp_ns'])
        self.assertEqual(shared['us']['timestamp_ns'],'1704067200123456000')
        self.assertEqual(shared['ns']['provenance'][0]['source_timestamp'],'1704067200.123456789')
        self.metrics[self.case]={'ns_exact':True,'ms_ns_null':True,'us_exact':True,'shared_read_valid':3}

    def test_genuine_conflict_is_not_published(self):
        package,_=self.bundle('conflict',[[['1704067200.123','BTCUSDT','Buy','2.00','1.0','conflict']], [['1704067200.123','BTCUSDT','Buy','2.0','1.01','conflict']]])
        self.assertEqual(len(package['conflicts']),1); self.assertFalse(package['storage_batches'])
        self.assertEqual(len(package['raw_record_versions']),2)
        self.assertEqual(len({self.models.normalize(item['storage_record'])['content_hash'] for item in package['records']}),2)
        self.assertFalse(self.read())
        self.metrics[self.case]={'conflicts':1,'publication_batches':0,'raw_versions':2}

    def test_terminal_pagination_is_not_complete_coverage(self):
        class FixtureAPI:
            def __init__(self): self.pages=0
            def server_time_ms(self): return 2000000000000
            def call(self,path,params):
                self.pages+=1
                rows=[['120000','1','1','1','1','1','1'],['60000','1','1','1','1','1','1']] if self.pages==1 else [['0','1','1','1','1','1','1']]
                return {'list':rows},'https://api.bybit.com/v5/market/kline'
        directory=self.directory/'pagination'; directory.mkdir()
        j=self.journal.Journal(directory/'journal.sqlite')
        try:
            result=self.rest.Backfill(FixtureAPI(),j).run('linear','BTCUSDT','kline','1',0,180000,limit=2)
            self.assertEqual(result['records'],3); j.export(directory/'handoff.json')
        finally: j.close()
        package=json.loads((directory/'handoff.json').read_text())
        pages=[p['pagination'] for p in package['evidence'].values() if p['kind']=='rest_response']
        self.assertEqual([p['response_count'] for p in pages],[2,1])
        self.assertEqual(pages[-1]['terminal_condition'],'EXHAUSTED')
        for page in pages:self.validator.validate_record('pagination',page)
        self.publish(package)
        rows=self.read(0,180000,'candles','1'); self.assertEqual(len(rows),3)
        for row in rows:self.shared_read(row)
        coverage=self.store.coverage('bybit','linear','BTCUSDT','candles','1',0,180000)
        self.assertFalse(coverage['complete']); self.assertEqual(coverage['percent'],0)
        self.metrics[self.case]={'response_counts':[2,1],'terminal_condition':'EXHAUSTED','coverage_complete':False,'verified_percent':0}

    def test_replay_and_journal_restart(self):
        package,directory=self.equivalent(); self.publish(package)
        before=self.counts()
        j=self.journal.Journal(directory/'journal.sqlite')
        try:j.export(directory/'reopened.json')
        finally:j.close()
        reopened=json.loads((directory/'reopened.json').read_text())
        self.assertEqual(self.handoff.canonical(package),self.handoff.canonical(reopened))
        self.store=self.store_module.Store(self.dsn,storage_root=self.directory)
        self.publish(reopened); self.assertEqual(self.counts(),before)
        self.assertEqual(len(self.read()),1); self.assertEqual(len(self.read()[0]['provenance']['observations']),2)
        self.shared_read(self.read()[0])
        write_json(self.root/'restart-state.json',{'database':self.dbname,'handoff':str(directory/'reopened.json'),'counts':before,'storage_root':str(self.directory)})
        self.metrics[self.case]={'journal_restart_lossless':True,'publication_replay_counts_unchanged':True,'economic_executions':1,'source_observations':2}


def child_main(root,verify_restart=False):
    psycopg,admin_dsn,modules,state=guard_child_context(root,verify_restart)
    if verify_restart:
        Store=modules['store_module'].Store
        dsn=psycopg.conninfo.make_conninfo(admin_dsn,dbname=state['database'])
        store=Store(dsn,storage_root=state['storage_root'])
        rows=store.read('trades','bybit','linear','BTCUSDT',1704067200123,1704067200124,limit=100)
        assert len(rows)==1 and len(rows[0]['provenance']['observations'])==2
        assert all(o['source_run_status']=='matched' and o['source_ingestion_run']==o['run_id'] for o in rows[0]['provenance']['observations'])
        package=json.loads(Path(state['handoff']).read_text())
        for batch in package['storage_batches']:
            store.ingest(batch['records'],batch['evidence'],batch['batch_id'],batch['checkpoint'],run_id=batch['ingestion_run'])
        from psycopg import sql
        with psycopg.connect(dsn) as conn:
            for name,count in state['counts'].items():
                assert conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier('tester_storage',name))).fetchone()[0]==count
        write_json(root/'server-restart.json',{'postgres_restart':True,'new_process_replay':True,'economic_executions':1,'source_observations':2,'all_schema_counts_unchanged':True,'source_run_status':'matched'})
        return
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(PinnedPairTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    write_json(root/'test-result.json',{'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped)})
    if not result.wasSuccessful(): raise SystemExit(1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-root',type=Path,default=Path('work/pinned-pair'))
    parser.add_argument('--pg-bin',type=Path)
    parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--verify-restart',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--simulate-start-error',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.child:
        child_main(Path(os.environ['PINNED_PAIR_ROOT']),args.verify_restart); return
    repo=Path(__file__).resolve().parents[2]
    root=args.work_root.resolve()/('run-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
    root.mkdir(parents=True); snapshot,pins=extract_snapshot(repo,root)
    pg=args.pg_bin or (Path(shutil.which('pg_ctl')).parent if shutil.which('pg_ctl') else Path('C:/Program Files/PostgreSQL/18/bin'))
    suffix='.exe' if os.name=='nt' else ''; ctl=pg/('pg_ctl'+suffix)
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    def command(argv,log=None):
        result=subprocess.run([str(a) for a in argv],capture_output=True,text=True,creationflags=flags)
        if log: (root/log).write_text(result.stdout+result.stderr,encoding='utf-8')
        if result.returncode: raise RuntimeError('Fixture command failed; inspect '+str(root/log) if log else 'Fixture command failed')
        return result.stdout
    command([pg/('initdb'+suffix),'-D',root/'pgdata','-A','trust','-U','ingest_pair_fixture','--no-locale','--encoding=UTF8'],'initdb.log')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    def start():
        with (root/'pg-start.log').open('a') as log:
            subprocess.run([str(a) for a in [ctl,'-D',root/'pgdata','-l',root/'server.log','-o',f'-h 127.0.0.1 -p {port}','-w','-t','30','start']],check=True,stdout=log,stderr=subprocess.STDOUT,creationflags=flags)
    def stop():command([ctl,'-D',root/'pgdata','-m','fast','-w','-t','30','stop'],'pg-stop.log')
    env=os.environ.copy(); env['PINNED_PAIR_ROOT']=str(root)
    env['PINNED_PAIR_DSN']=f'host=127.0.0.1 port={port} dbname=postgres user=ingest_pair_fixture'
    env['PINNED_PAIR_PINS_SHA256']=hashlib.sha256((root/'pins.json').read_bytes()).hexdigest()
    env['PYTHONPATH']=str(snapshot); env['TMP']=str(root); env['TEMP']=str(root)
    started=False
    try:
        start()
        if args.simulate_start_error:raise RuntimeError('Synthetic failure after PostgreSQL launched, before successful-start flag')
        started=True
        child=[sys.executable,'-m','tests.ingest.pinned_roundtrip','--child']
        first=subprocess.run(child,cwd=snapshot,env=env,capture_output=True,text=True,creationflags=flags)
        (root/'roundtrip-tests.log').write_text(first.stdout+first.stderr,encoding='utf-8')
        if first.returncode: raise RuntimeError('Pinned contract tests failed: '+str(root/'roundtrip-tests.log'))
        env['PINNED_PAIR_RESTART_SHA256']=hashlib.sha256((root/'restart-state.json').read_bytes()).hexdigest()
        stop(); started=False
        start(); started=True
        second=subprocess.run(child+['--verify-restart'],cwd=snapshot,env=env,capture_output=True,text=True,creationflags=flags)
        (root/'restart-tests.log').write_text(second.stdout+second.stderr,encoding='utf-8')
        if second.returncode: raise RuntimeError('PostgreSQL restart verification failed: '+str(root/'restart-tests.log'))
    finally:
        pending=sys.exc_info()[1]
        try:
            cleanup=cleanup_cluster(ctl,root/'pgdata',root,flags)
            write_json(root/'cleanup-result.json',cleanup)
        except Exception as error:
            write_json(root/'cleanup-result.json',{'stopped':False,'error':type(error).__name__})
            if pending is None:raise
            pending.add_note('Cleanup verification also failed; inspect '+str(root))
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'fixture_kind':'synthetic_immutable_A_to_B_postgresql_roundtrip','pins':{k:v for k,v in pins.items() if k!='production_files'},'tests':json.loads((root/'test-result.json').read_text()),'cases':json.loads((root/'case-metrics.json').read_text()),'restart':json.loads((root/'server-restart.json').read_text()),'postgres_version':command([pg/('postgres'+suffix),'--version']).strip(),'scratch_server_stopped':cleanup['stopped'],'network_market_acquisition':False,'full_pipeline_acceptance':False,'production_files':pins['production_files']}
    write_json(root/'report.json',report)
    print(json.dumps({'report':str(root/'report.json'),'pins':report['pins'],'tests':report['tests'],'postgres_restart':True,'scratch_server_stopped':True},indent=2))


if __name__=='__main__':main()
