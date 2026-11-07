"""Opt-in real public API checks: python -m tests.ingest.live_smoke.

Writes to work/live-ingest-smoke. Re-run without deleting it to exercise restart.
"""
import json
from pathlib import Path
from src.ingest.archive import ArchiveLoader
from src.ingest.rest import API,Backfill
from src.ingest.journal import Journal,utcnow
from src.ingest.transport import Transport

def main():
    root=Path('work/live-ingest-smoke'); root.mkdir(parents=True,exist_ok=True)
    journal=Journal(root/'jobs.sqlite'); transport=Transport()
    report={'checked_at':utcnow(),'archives':[],'rest':[]}
    try:
        loader=ArchiveLoader(journal,transport)
        files=[{'url':'https://public.bybit.com/'+path} for path in [
            'trading/BTCUSD/BTCUSD2019-10-01.csv.gz',
            'trading/BTCUSDT/BTCUSDT2020-03-25.csv.gz',
            'spot/BTCUSDT/BTCUSDT_2023-01-01.csv.gz']]
        # Fresh downloads are actually concurrent under a shared request throttle.
        report['archives']=loader.many(files,root/'archives',workers=3)
        repeated=loader.many(files,root/'archives',workers=3)
        assert [x['sha256'] for x in repeated]==[x['sha256'] for x in report['archives']]
        backfill=Backfill(API(transport),journal)
        for category,symbol in [('spot','BTCUSDT'),('linear','BTCUSDT'),('inverse','BTCUSD')]:
            result=backfill.run(category,symbol,'kline','1',1704067200000,1704067500000,limit=2)
            assert result['status']=='observed' and result['records']==5,result
            repeated=backfill.run(category,symbol,'kline','1',1704067200000,1704067500000,limit=2)
            assert repeated['records']==5
            report['rest'].append({'category':category,**result,'restart_verified':True})
        journal.export(root/'records.jsonl')
    finally: journal.close()
    (root/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
