"""Real bounded source -> self-contained export -> pinned shared/B validators.

python -m tests.ingest.live_handoff --output-dir work/live-handoff --references work/handoff-reference
No PostgreSQL/user DB is contacted by this command. Use --postgres-dsn only with
an explicitly isolated scratch database if executing the consumer transaction.
"""
import argparse
import hashlib
import json
import inspect
from pathlib import Path
from src.ingest.archive import ArchiveLoader,iter_trade_records
from src.ingest.journal import Journal,utcnow
from src.ingest.rest import API,Backfill
from src.ingest.transport import Transport
from tests.ingest.verify_handoff import prepare_references,CONTRACT_SHA,STORAGE_SHA,STORAGE_RUN_SHA

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--references',type=Path,required=True)
    p.add_argument('--postgres-dsn')
    p.add_argument('--storage-sha',default=STORAGE_SHA,choices=[STORAGE_SHA,STORAGE_RUN_SHA])
    args=p.parse_args(); root=args.output_dir; root.mkdir(parents=True,exist_ok=True)
    prepare_references(args.references.resolve(),args.storage_sha)
    from contracts.v1.validate import validate_record
    from src.storage.models import normalize
    from src.storage.store import validate_evidence,Store
    store=None
    if args.postgres_dsn:
        options={'storage_root':root.resolve()} if 'storage_root' in inspect.signature(Store).parameters else {}
        store=Store(args.postgres_dsn,**options)
        if hasattr(store,'acquisition_status'):
            assert store.acquisition_status()['allowed'],'Scratch storage disk gate paused acquisition'
    j=Journal(root/'journal.sqlite'); transport=Transport()
    try:
        loader=ArchiveLoader(j,transport)
        url='https://public.bybit.com/trading/BTCUSDT/BTCUSDT2020-03-25.csv.gz'
        archive=loader.download(url,root/'BTCUSDT2020-03-25.csv.gz')
        parse_key='parse:'+archive['sha256']
        if not j.task(parse_key):
            j.save(parse_key,'observed',{'archive_ref':url},iter_trade_records(root/'BTCUSDT2020-03-25.csv.gz','linear','BTCUSDT',archive))
        api=API(transport); backfill=Backfill(api,j)
        result=backfill.run('linear','BTCUSDT','kline','1',1704067200000,1704067380000,limit=2)
        replay=backfill.run('linear','BTCUSDT','kline','1',1704067200000,1704067380000,limit=2)
        assert result['records']==replay['records']==3
        empty=backfill.run('linear','BTCUSDT','kline','1',1262304000000,1262304060000,limit=2)
        assert empty['records']==0 and empty['status']=='unverified'
        export=j.export(root/'handoff.json')
    finally: j.close()
    package=json.loads((root/'handoff.json').read_text(encoding='utf-8'))
    strict=storage=0
    for item in package['records']:
        assert item['ingestion_run'] in package['runs']
        assert item['evidence_refs'] and all(ref in package['evidence'] for ref in item['evidence_refs'])
        assert not item['projection_errors'],item['projection_errors']
        validate_record(item['contract_kind'],item['contract_record']); strict+=1
        normalize(item['storage_record']); storage+=1
    for e in package['evidence'].values():
        if e['kind']=='rest_response':
            validate_record('pagination',e['pagination'])
            if 'http_response' in e:
                import base64
                assert hashlib.sha256(base64.b64decode(e['http_response']['bytes_base64'])).hexdigest()==e['http_response']['sha256']
    import base64
    for sha,artifact in package['artifacts'].items():
        assert artifact['status']=='AVAILABLE'
        assert hashlib.sha256(base64.b64decode(artifact['bytes_base64'])).hexdigest()==sha
    pg=[]
    for batch in package['storage_batches']:
        _,count=validate_evidence(batch['evidence']); assert count==len(batch['records'])
        if store is not None:
            kwargs={'run_id':batch['ingestion_run']} if 'run_id' in inspect.signature(store.ingest).parameters else {}
            first=store.ingest(batch['records'],batch['evidence'],batch['batch_id'],batch['checkpoint'],**kwargs)
            second=store.ingest(batch['records'],batch['evidence'],batch['batch_id'],batch['checkpoint'],**kwargs)
            assert first['count']==second['count']==count and not first['verified'] and not second['verified']
            e=batch['evidence']
            read=store.read(e['dataset'],e['venue'],e['category'],e['symbol'],e['start_ms'],e['end_ms'],e['interval'],limit=10000)
            assert len(read)==count
            authentic=False
            if kwargs:
                assert all(row['provenance']['authentic'] and batch['ingestion_run'] in row['provenance']['run_ids'] for row in read)
                ledger=store.coverage_evidence(e['venue'],e['category'],e['symbol'],e['dataset'],e['interval'],e['start_ms'],e['end_ms'])
                assert any(batch['ingestion_run'] in row['run_ids'] for row in ledger)
                authentic=True
                for row in read:
                    for observation in row['provenance']['observations']:
                        saved=observation['evidence']['acquisition_manifest']
                        assert all(ref in saved['evidence'] for ref in observation['evidence']['evidence_refs'])
            pg.append({'dataset':e['dataset'],'count':count,'retry_count':second['count'],'verified':False,'read_count':len(read),'caller_supplied_run_verified':authentic})
    report={'checked_at':utcnow(),'contract_sha':CONTRACT_SHA,'storage_sha':args.storage_sha,'archive_sha256':archive['sha256'],'archive_rows':archive['rows'],'rest_records':result['records'],'restart_records':replay['records'],'empty_probe':{'records':empty['records'],'status':empty['status'],'reason':'Real prelaunch-window empty responses remain unknown history'},'strict_validated':strict,'B_normalized':storage,'export':export,'conflicts':len(package['conflicts']),'postgres_batches':pg,'coverage':'PARTIAL; no global/source COMPLETE claim'}
    (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
