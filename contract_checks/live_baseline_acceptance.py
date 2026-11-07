"""Opt-in public-data smoke test on an exclusively disposable PG cluster.

This tests the coordinator's legacy baseline, not A–E v1 integration.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

if os.getenv('BYBIT_TEST_LIVE')!='1' or any(os.getenv(k)!=v for k,v in
    dict(PGHOST='127.0.0.1',PGPORT='55449',PGDATABASE='bybit_master_live_acceptance',PGUSER='acceptance_admin').items()):
    raise SystemExit('Refusing live smoke test outside explicit disposable identity')

import requests
import db
import engine
from bybit import Bybit


class BoundedClient(Bybit):
    def api(self,endpoint,params=None):
        if not endpoint.startswith('/v5/market/'):raise ValueError('Public market endpoints only')
        time.sleep(0.21)
        r=self.s.get('https://api.bybit.com'+endpoint,params=params,timeout=(5,15))
        r.raise_for_status();body=r.json()
        if body.get('retCode')!=0:raise ValueError(str(body.get('retMsg')))
        return body['result']


output=Path(__file__).resolve().parents[1]/'docs/coordination/evidence/live-baseline-postgres.json'
result=dict(classification='LIVE_PUBLIC_DATA_LEGACY_BASELINE_NOT_V1_E2E',started_at_ms=engine.utc_ms(),
            instruments=[],archive={},status='FAIL',limitations=['Not A/B/C/D/E integrated contract acceptance','No process-kill restart test','No UI/physical Windows deployment'])
try:
    db.initialize();client=BoundedClient()
    page=client.api('/v5/market/instruments-info',{'category':'linear','limit':1000})
    # This bounded smoke selection does not assert whole-catalog completeness.
    symbols=sorted({r['symbol'] for r in page['list']})[:10]
    result['catalog_evidence']=dict(endpoint='/v5/market/instruments-info',request_params={'category':'linear','limit':1000},
        response_count=len(page['list']),cursor_in=None,cursor_out=page.get('nextPageCursor') or None,
        terminal_condition='PARTIAL' if page.get('nextPageCursor') else 'EXHAUSTED',coverage_status='UNKNOWN')
    end=engine.utc_ms()//60000*60000;start=end-10*60000
    result.update(start_ms=start,end_ms=end)
    for symbol in symbols:
        engine.download_candles(client,'linear',symbol,'trade','1',start,end)
        with db.connect() as c:
            count=c.execute('SELECT count(*) FROM candles WHERE category=%s AND symbol=%s AND ts>=%s AND ts<%s',('linear',symbol,start,end)).fetchone()[0]
        state=db.coverage_state('linear',symbol,'trade','1',start,end)
        engine.download_candles(client,'linear',symbol,'trade','1',start,end)
        with db.connect() as c:
            after=c.execute('SELECT count(*) FROM candles WHERE category=%s AND symbol=%s AND ts>=%s AND ts<%s',('linear',symbol,start,end)).fetchone()[0]
        result['instruments'].append(dict(category='linear',symbol=symbol,rows=count,rows_after_retry=after,coverage=state))
    engine.download_archives(client,'linear','XRPUSDT',[date(2023,1,1)])
    archive_start=1672531200000
    state=db.coverage_state('linear','XRPUSDT','archive','daily',archive_start,archive_start+engine.DAY)
    with db.connect() as c:
        row=c.execute("SELECT row_count,sha256 FROM trade_files WHERE category='linear' AND symbol='XRPUSDT' AND day='2023-01-01'").fetchone()
        count=c.execute("SELECT count(*) FROM trades WHERE category='linear' AND symbol='XRPUSDT'").fetchone()[0]
    engine.build_clusters('linear','XRPUSDT',['1'])
    with db.connect() as c:
        clusters=c.execute("SELECT count(*),sum(trade_count) FROM clusters WHERE category='linear' AND symbol='XRPUSDT' AND timeframe='1'").fetchone()
    engine.download_archives(client,'linear','XRPUSDT',[date(2023,1,1)])
    with db.connect() as c:
        after=c.execute("SELECT count(*) FROM trades WHERE category='linear' AND symbol='XRPUSDT'").fetchone()[0]
    result['archive']=dict(symbol='XRPUSDT',day='2023-01-01',coverage=state,manifest_rows=row[0] if row else None,
        sha256=row[1] if row else None,stored_rows=count,rows_after_retry=after,
        cluster_levels=clusters[0],cluster_trade_count=int(clusters[1]) if clusters[1] is not None else None)
    assert len(symbols)==10 and all(r['rows']==r['rows_after_retry']==10 and r['coverage']=='complete' for r in result['instruments'])
    assert state=='complete' and row and row[0]==count==after==clusters[1]==43210
    result['status']='PASS'
except Exception as error:
    result['error']=type(error).__name__+': '+str(error)
finally:
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))
raise SystemExit(0 if result['status']=='PASS' else 1)

