"""Opt-in current instrument snapshots; never historical listing completeness.

No network, filesystem or DB work occurs at import. B publication is a separate
consumer decision: storage_metadata entries are save_instrument keyword args.
"""
import base64
import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from .handoff import canonical,digest
from .journal import utcnow

CATEGORIES={'spot','linear','inverse'}
DECIMAL=re.compile(r'(?:0\.[0-9]*[1-9][0-9]*|[1-9][0-9]*(?:\.[0-9]+)?)\Z')
SYMBOL=re.compile(r'[A-Z0-9-]{2,50}\Z')


def write_handoff(bundle,path):
    """Stable atomic re-export: never assign a new acquisition run or time."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as out:
            out.write(canonical(bundle)+'\n');out.flush();os.fsync(out.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def _milliseconds(stamp):
    value=datetime.fromisoformat(stamp).astimezone(timezone.utc)
    epoch=datetime(1970,1,1,tzinfo=timezone.utc)
    delta=value-epoch
    return (delta.days*86400+delta.seconds)*1000+delta.microseconds//1000


def _verified_response(api,result,url,params):
    response=copy.deepcopy(getattr(api,'last_response',None))
    if not isinstance(response,dict):raise ValueError('Original HTTP response required')
    raw=base64.b64decode(response['bytes_base64'],validate=True)
    if response.get('sha256')!=hashlib.sha256(raw).hexdigest() or response.get('hash_scope')!='original_http_body':
        raise ValueError('Original HTTP response hash mismatch')
    p=urlsplit(url)
    if p.scheme!='https' or p.netloc!='api.bybit.com' or p.path!='/v5/market/instruments-info':
        raise ValueError('Unexpected instrument source URL')
    if parse_qs(p.query)!={k:[str(v)] for k,v in params.items()} or response.get('url')!=url or response.get('status_code')!=200:
        raise ValueError('Response request scope mismatch')
    body=json.loads(raw)
    if body.get('retCode')!=0 or body.get('result')!=result:raise ValueError('Response/result mismatch')
    ts=body.get('time')
    if type(ts) is not int or not 0<=ts<=9007199254740991:raise ValueError('Missing source timestamp precision')
    return response,str(ts)


def _row_fields(row,category):
    if not isinstance(row,dict):raise ValueError('Instrument row must be an object')
    if not isinstance(row.get('symbol'),str) or not SYMBOL.fullmatch(row['symbol']):raise ValueError('Missing/invalid symbol')
    if not isinstance(row.get('status'),str) or not row['status'].strip():raise ValueError('Missing instrument status')
    tick=row.get('priceFilter',{}).get('tickSize')
    # Spot basePrecision is not documented as qtyStep. Even an unexpected spot
    # qtyStep stays raw until the official field semantics establish that map.
    step=row.get('lotSizeFilter',{}).get('qtyStep') if category!='spot' else None
    for name,value in [('tick_size',tick),('qty_step',step)]:
        if not isinstance(value,str) or not DECIMAL.fullmatch(value):raise ValueError('Missing/invalid documented '+name)
    return {'symbol':row['symbol'],'status':row['status'],'tick_size':tick,'qty_step':step}


def acquire_instruments(api,journal,category,*,max_pages,limit=1000,max_rows=10000,status=None,symbol=None):
    """Explicit bounded acquisition using existing read-only API retry policy.

    A page cap/empty response/terminal cursor is observational evidence only.
    Each invocation is a new acquisition; replay the returned artifact to retain
    original run/time. Conflicting same-symbol rows within a run are quarantined.
    """
    if category not in CATEGORIES:raise ValueError('Explicit supported category required')
    for value,maximum in [(max_pages,100),(limit,1000),(max_rows,100000)]:
        if type(value) is not int or not 1<=value<=maximum:raise ValueError('Invalid acquisition bound')
    if symbol is not None and (not isinstance(symbol,str) or not SYMBOL.fullmatch(symbol)):raise ValueError('Invalid symbol filter')
    if status is not None and (not isinstance(status,str) or not status.strip()):raise ValueError('Invalid status filter')
    params={'category':category}
    if category!='spot':params['limit']=limit
    if status:params['status']=status
    if symbol:params['symbol']=symbol
    scope={'venue':'bybit','category':category,'endpoint':'/v5/market/instruments-info','filters':copy.deepcopy(params),'snapshot_kind':'current_observation','historical_listing_completeness':'UNVERIFIED'}
    run_id=journal.start_run('instrument_snapshot',scope)
    pages={};attempts=[];observations=[];rejections=[];groups={};raw_groups={};seen=set();terminal='PAGE_CAP'
    for number in range(max_pages):
        request=copy.deepcopy(params);attempt={'page_index':number,'request':request,'started_at':utcnow()}
        attempts.append(attempt)
        try:
            api.last_response=None
            result,url=api.call('/instruments-info',request)
            fetched=utcnow();response,source_ts=_verified_response(api,result,url,request)
            if not isinstance(result,dict):raise ValueError('Instrument result must be an object')
        except Exception as error:
            attempt.update(status='FAILED',finished_at=utcnow(),error={'type':type(error).__name__,'message':str(error)},response=copy.deepcopy(getattr(api,'last_response',None)))
            attempt['evidence_ref']=journal.add_evidence({'kind':'instrument_request_failure','ingestion_run':run_id,**attempt})
            terminal='REQUEST_FAILED';break
        rows=result.get('list');cursor=result.get('nextPageCursor','')
        valid_scope=result.get('category')==category
        valid_shape=isinstance(rows,list) and isinstance(cursor,str)
        condition='CONTINUE'
        if not valid_scope:condition='CATEGORY_MISMATCH'
        elif not valid_shape:condition='INVALID_RESPONSE'
        elif category=='spot':condition='SPOT_NO_PAGINATION' if not cursor else 'UNEXPECTED_SPOT_CURSOR'
        elif cursor in seen and cursor:condition='REPEATED_CURSOR'
        elif not cursor:condition='EXHAUSTED'
        elif number+1==max_pages:condition='PAGE_CAP'
        if valid_shape and len(observations)+len(rows)>max_rows:condition='ROW_CAP'
        evidence={'kind':'instrument_response','ingestion_run':run_id,'scope':scope,'request':request,'url':url,'fetched_at':fetched,'response':response,'source_timestamp':source_ts,'timestamp_precision':'ms','pagination':{'page_index':number,'cursor_in':request.get('cursor'),'cursor_out':cursor,'response_count':len(rows) if isinstance(rows,list) else None,'terminal_condition':condition,'does_not_prove_history_complete':True}}
        ref=journal.add_evidence(evidence);pages[ref]=evidence
        attempt.update(status='OBSERVED',finished_at=fetched,evidence_ref=ref)
        if isinstance(rows,list):
            for index,row in enumerate(rows[:max(0,max_rows-len(observations))]):
                raw={'page_ref':ref,'row_index':index,'row':copy.deepcopy(row)};observations.append(raw)
                if isinstance(row,dict) and isinstance(row.get('symbol'),str):raw_groups.setdefault(row['symbol'],[]).append(raw)
                try:
                    if not valid_scope:raise ValueError('Missing/mismatched response category')
                    fields=_row_fields(row,category)
                    if symbol and fields['symbol']!=symbol:raise ValueError('Symbol outside request filter')
                    if status and fields['status']!=status:raise ValueError('Status outside request filter')
                    groups.setdefault(fields['symbol'],[]).append((raw,fields))
                except (ValueError,TypeError,AttributeError) as error:
                    rejections.append({'page_ref':ref,'row_index':index,'reason':str(error)})
        terminal=condition
        if condition!='CONTINUE':break
        seen.add(cursor);params['cursor']=cursor
    instruments=[];storage=[];conflicts=[]
    # A row cap can conceal conflicting versions later in the received page.
    # Keep the full HTTP body but publish no projection from this acquisition.
    for name,versions in sorted(groups.items()) if terminal!='ROW_CAP' else []:
        hashes={digest(raw['row']) for raw in raw_groups[name]}
        if len(hashes)>1:
            conflicts.append({'category':category,'symbol':name,'reason':'Conflicting same-symbol metadata in current acquisition','raw_versions':raw_groups[name]});continue
        refs=list(dict.fromkeys(raw['page_ref'] for raw,_ in versions));first=pages[refs[0]]
        effective=_milliseconds(first['fetched_at'])
        provenance=[{'source_kind':'bybit_rest','source_ref':pages[ref]['url'],'source_hash':pages[ref]['response']['sha256'],'ingestion_run':run_id,'timestamp_precision':'ms','source_timestamp':pages[ref]['source_timestamp']} for ref in refs]
        record={'schema_version':'1.0','venue':'bybit','category':category,**versions[0][1],'timestamp_ms':effective,'provenance':provenance,'availability':{'status':'PARTIAL','reason':'Observed current snapshot only; historical catalog and effective rule history unverified'},'coverage':{'status':'UNKNOWN','start_ms':effective,'end_ms':effective+1,'evidence_refs':refs}}
        instruments.append(record)
        metadata={'schema_version':'1.0','snapshot_kind':'current_observation','effective_time_basis':'local_acquisition_not_exchange_rule_activation','instrument':record,'raw_versions':[raw for raw,_ in versions],'field_mapping':{'tick_size':'priceFilter.tickSize','qty_step':'lotSizeFilter.qtyStep'},'historical_listing_completeness':'UNVERIFIED'}
        source={'url':first['url'],'fetched_at':first['fetched_at'],'ingestion_run':run_id,'sha256':first['response']['sha256'],'hash_scope':'original_http_body','timestamp_precision':'ms','source_timestamp':first['source_timestamp'],'scope':scope,'evidence_refs':refs}
        storage.append({'venue':'bybit','category':category,'symbol':name,'effective_ms':effective,'metadata':metadata,'source':source})
    failed=terminal in {'REQUEST_FAILED','CATEGORY_MISMATCH','INVALID_RESPONSE','REPEATED_CURSOR','UNEXPECTED_SPOT_CURSOR'}
    journal.finish_run(run_id,'FAILED' if failed else 'PARTIAL')
    with journal.lock:run=json.loads(journal.db.execute('SELECT body FROM runs WHERE run_id=?',(run_id,)).fetchone()[0])
    # All pages/attempts survive in existing acquisition journal; no metadata DB.
    journal.save('instrument-snapshot:'+run_id,'failed' if failed else 'partial',{'scope':scope,'terminal_condition':terminal,'evidence_refs':list(pages),'attempts':attempts},run_id=run_id)
    for artifact in storage:
        artifact['source']['acquisition_manifest']={'runs':{run_id:run},'evidence':copy.deepcopy(pages),'attempts':copy.deepcopy(attempts),'terminal_condition':terminal}
    return {'schema_version':'1.0','artifact_kind':'bybit_instrument_handoff','run':run,'scope':scope,'terminal_condition':terminal,'coverage':{'status':'UNKNOWN','reason':'Pagination and current snapshot cannot prove historical listing completeness'},'attempts':attempts,'pages':pages,'raw_observations':observations,'rejections':rejections,'conflicts':conflicts,'instruments':instruments,'storage_metadata':storage}
