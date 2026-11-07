"""Explicit projections; acquisition facts are never rewritten as completeness.

Shared contracts 9766c009 and storage 5154efab have different wire shapes.
The export keeps both projections plus the original producer envelope.
"""
import copy
import hashlib
import json
from decimal import Decimal,DecimalException
from urllib.parse import urlsplit

DATASETS={'kline':'candles','long_short_ratio':'ratio'}
CANDLES={'candles','mark','index','premium'}

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)

def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()

def evidence_closure(refs,evidence):
    """Close task-to-request references before persisting the B publication proof."""
    selected={}; queue=list(refs)
    def references(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in {'evidence_ref','evidence_refs','source_evidence_refs'}:
                    for ref in item if isinstance(item,list) else [item]:
                        if isinstance(ref,str) and ref in evidence: queue.append(ref)
                else: references(item)
        elif isinstance(value,list):
            for item in value: references(item)
    while queue:
        ref=queue.pop()
        if ref in selected: continue
        selected[ref]=evidence[ref]; references(evidence[ref])
    return selected

def record_key(record):
    fields=('venue','category','symbol','dataset','trade_id') if record.get('trade_id') else ('venue','category','symbol','dataset','interval','timestamp_ms')
    return canonical([record.get(k,'') for k in fields])

def semantic_hash(record):
    value={k:record.get(k) for k in ('schema_version','timestamp_ms','timestamp_ns','timestamp_us','timestamp_precision','trade_id','id_origin','payload')}
    if record.get('dataset')=='trades' and isinstance(value['payload'],dict):
        payload=value['payload']
        try:
            # Match the named economic fields handed to B, not CSV spelling or
            # ancillary raw columns. Raw envelopes/hashes are never rewritten.
            fields={}
            for name,raw in [('price',payload.get('price')),('size',payload.get('size',payload.get('volume')))]:
                if not isinstance(raw,str): raise ValueError('Invalid exact trade value')
                n=Decimal(raw)
                if not n.is_finite() or n<=0 or n.adjusted()>131071 or n.as_tuple().exponent < -16383: raise ValueError('Invalid trade value')
                text=format(n,'f')  # Decimal.normalize() can round under context.prec.
                fields[name]=text.rstrip('0').rstrip('.') if '.' in text else text
            value['payload']={**fields,'side':payload.get('side')}
        except (ValueError,DecimalException):
            pass  # Invalid/unknown values retain exact raw semantics and errors.
    return digest(value)

def official_kind(source):
    p=urlsplit(source.get('url',''))
    inferred={'api.bybit.com':'bybit_rest','public.bybit.com':'bybit_archive'}.get(p.netloc) if p.scheme=='https' else None
    declared=source.get('source_kind',inferred)
    return declared if inferred==declared else None

def raw_timestamp(record):
    source=record.get('source',{})
    if 'source_timestamp' in source: return source['source_timestamp']
    p=record.get('payload')
    if isinstance(p,list): return str(p[0]) if p else None
    if isinstance(p,dict):
        for k in ('timestamp','fundingRateTimestamp','start_at'):
            if k in p: return str(p[k])
    return None

def storage_record(raw,metadata):
    """B 5154efab input; no change to B's files or SQL schema."""
    if raw.get('schema_version')!='1.0': raise ValueError('Unsupported producer schema_version; raw retained without implicit migration')
    kind=DATASETS.get(raw['dataset'],raw['dataset'])
    original=raw['payload']; p=copy.deepcopy(original)
    if kind in CANDLES:
        if isinstance(p,list):
            p={name:value for name,value in zip(('open','high','low','close','volume','turnover'),p[1:])}
        else: p={k:p[k] for k in ('open','high','low','close','volume','turnover') if k in p}
        if not all(k in p for k in ('open','high','low','close')): raise ValueError('Missing named OHLC fields')
    elif kind=='trades':
        p={'price':p['price'],'size':p.get('size',p.get('volume')),'side':p.get('side')}
        if raw.get('id_origin')=='exchange': p['trade_id']=raw['trade_id']
        if p['size'] is None: raise ValueError('Missing trade quantity')
        if p['side'] not in ('Buy','Sell'): raise ValueError('B requires a known taker side')
    elif kind=='open_interest': p={'value':p.get('openInterest',p.get('value'))}
    elif kind=='funding': p={'value':p.get('fundingRate',p.get('value'))}
    elif kind=='ratio': p={'value':p.get('buyRatio',p.get('value'))}
    else: raise ValueError('Unsupported storage dataset')
    if not p or any(v is None for v in p.values()): raise ValueError('Missing source value')
    financial=('price','size') if kind=='trades' else ('open','high','low','close','volume','turnover') if kind in CANDLES else ('value',)
    for name in financial:
        if name not in p: continue
        if not isinstance(p[name],str) or not Decimal(p[name]).is_finite(): raise ValueError('Invalid exact financial value: '+name)
        value=Decimal(p[name])
        if kind=='trades' and value<=0 or kind not in {'funding','premium','trades'} and value<0: raise ValueError('Invalid financial bounds: '+name)
    if kind=='ratio' and Decimal(p['value'])>1: raise ValueError('Ratio exceeds one')
    if kind in CANDLES:
        o,h,l,c=(Decimal(p[k]) for k in ('open','high','low','close'))
        if not l<=min(o,c)<=max(o,c)<=h: raise ValueError('Invalid OHLC ordering')
    source=copy.deepcopy(raw.get('source',{}))
    if not official_kind(source) or not source.get('fetched_at'): raise ValueError('Missing/unknown source or acquisition time')
    if 'sha256' in source and (not isinstance(source['sha256'],str) or len(source['sha256'])!=64 or any(c not in '0123456789abcdef' for c in source['sha256'])): raise ValueError('Invalid source SHA256')
    source.update({'source_kind':{'bybit_rest':'rest','bybit_archive':'archive'}[metadata['provenance'][0]['source_kind']],'ingestion_run':metadata['ingestion_run'],'source_timestamp':raw_timestamp(raw),'evidence_refs':metadata['evidence_refs']})
    if raw.get('id_origin')!='exchange' and kind=='trades': source['record_key']=raw['trade_id']
    result={k:raw[k] for k in ('schema_version','venue','category','symbol','timestamp_ms')}
    result.update(dataset=kind,interval=raw.get('interval',''),payload=p,source=source,timestamp_precision=raw.get('timestamp_precision','ms'),timestamp_ns=raw.get('timestamp_ns'))
    if result['timestamp_precision']=='us':
        value=raw.get('timestamp_us')
        if value is None:
            stamp=raw_timestamp(raw)
            if stamp is None: raise ValueError('Microseconds require original timestamp')
            n=Decimal(stamp); value=int(n) if n>=Decimal('1e14') else int(n*1000000)
        result['timestamp_us']=value
        # B accepts ns only with precision=ns; shared us projection is separate.
        result['timestamp_ns']=None
    result.update({k:copy.deepcopy(metadata[k]) for k in ('availability','coverage','provenance','ingestion_run','evidence_refs')})
    return result

def contract_record(raw,storage,metadata):
    """Strict shared-v1 shape. Fail rather than fabricate missing market values."""
    result={k:raw[k] for k in ('schema_version','venue','category','symbol','timestamp_ms')}
    result.update({k:copy.deepcopy(metadata[k]) for k in ('availability','coverage','provenance')})
    kind=storage['dataset']; p=storage['payload']
    if kind=='trades':
        precision=storage['timestamp_precision']; ns=storage.get('timestamp_ns')
        if precision=='us': ns=storage['timestamp_us']*1000
        result.update(timestamp_precision=precision,timestamp_ns=str(ns) if ns is not None else None,trade_id=raw['trade_id'],id_origin='exchange' if raw['id_origin']=='exchange' else 'synthetic',price=p['price'],quantity=p['size'],taker_side=p['side'])
        if result['id_origin']=='synthetic': result['identity_method']='sha256-category-symbol-sorted-original-row-v1; identical no-ID trades cannot be distinguished'
        return 'trade',result
    if kind in CANDLES:
        from .rest import next_time
        if 'volume' not in p: raise ValueError('Shared candle requires volume; reference-price source supplies none')
        if any(Decimal(p[k])<=0 for k in ('open','high','low','close')): raise ValueError('Shared candle prohibits zero/signed OHLC; source values retained')
        if 'is_closed' not in raw: raise ValueError('Missing exchange-time candle closure evidence')
        result.update(start_ms=raw['timestamp_ms'],end_ms=next_time(raw['timestamp_ms'],raw['interval']),interval=raw['interval'],kind={'candles':'official_trade','mark':'official_mark','index':'official_index','premium':'official_premium'}[kind],is_closed=raw['is_closed'],**{k:p[k] for k in ('open','high','low','close','volume')})
        return 'candle',result
    units={'open_interest':'USD' if raw['category']=='inverse' else 'base_coin','funding':'fraction','ratio':'fraction'}
    result.update(kind={'open_interest':'oi','funding':'funding','ratio':'ratio'}[kind],value=p['value'],unit=units[kind],interval=raw.get('interval') or 'event')
    return 'observation',result

def project(raw,record_id,run_id,refs,start,end,conflict=False,acquisition=False):
    source=raw.get('source',{}); kind=official_kind(source); precision=raw.get('timestamp_precision','ms')
    availability=copy.deepcopy(raw.get('availability') or {'status':'AVAILABLE' if kind and acquisition else 'UNVERIFIED','reason':None if kind and acquisition else 'Acquisition availability not established by retained evidence'})
    if not kind or not acquisition:
        availability={'status':'UNVERIFIED','reason':'Missing/unknown source or unresolved acquisition evidence'}
    if conflict: availability={'status':'UNVERIFIED','reason':'Conflicting values for the same stable identity; retained for review'}
    coverage={'status':'PARTIAL' if acquisition and kind else 'UNKNOWN','start_ms':start,'end_ms':end,'evidence_refs':list(refs)}
    sha=source.get('sha256'); sha=sha if isinstance(sha,str) and len(sha)==64 else None
    provenance=[{'source_kind':kind,'source_ref':source.get('url'),'source_hash':sha,'ingestion_run':run_id,'timestamp_precision':precision,'source_timestamp':raw_timestamp(raw)}] if kind and acquisition else []
    meta={'record_id':record_id,'ingestion_run':run_id,'evidence_refs':list(refs),'availability':availability,'coverage':coverage,'provenance':provenance,'quarantined':conflict}
    result={**meta,'raw_record':copy.deepcopy(raw),'storage_record':None,'contract_record':None,'contract_kind':None,'projection_errors':[]}
    try:
        if not acquisition: raise ValueError('No resolvable acquisition proof; bookkeeping/import run is not authentic source provenance')
        result['storage_record']=storage_record(raw,meta)
        result['contract_kind'],result['contract_record']=contract_record(raw,result['storage_record'],meta)
    except (ValueError,KeyError,TypeError,DecimalException) as exc:
        result['projection_errors'].append(str(exc) or type(exc).__name__)
        if result['storage_record'] is None:
            result['availability']={'status':'UNVERIFIED','reason':'Invalid or missing source record fields; see projection_errors'}
    return result

def build_bundle(runs,tasks,evidence,versions,links):
    """Self-contained snapshot. Every ref is a key in the manifest, not a URL guess."""
    groups={}
    for version in versions: groups.setdefault(version['record_key'],[]).append(version)
    conflicts={key:[v['record_id'] for v in values] for key,values in groups.items() if len({semantic_hash(v['body']) for v in values})>1}
    items=[]; link_index={}
    for link in links: link_index.setdefault(link['record_id'],[]).append(link)
    for v in versions:
        r=v['body']; associated=link_index.get(v['record_id'],[])
        # A version may be observed in several runs; preserve each association.
        associations={}
        for link in associated:
            run=r.get('source',{}).get('ingestion_run') or link['run_id']
            if run not in runs: run=link['run_id']
            combined=associations.setdefault(run,{**link,'run_id':run,'evidence_refs':[]})
            combined['evidence_refs']=list(dict.fromkeys(combined['evidence_refs']+link['evidence_refs']))
            combined['detail']={**combined['detail'],**link['detail']}
        for link in associations.values():
            refs=link['evidence_refs']; detail=link['detail']
            a=detail.get('start_ms',r['timestamp_ms']); b=detail.get('end_ms',r['timestamp_ms']+1)
            if not a<=r['timestamp_ms']<b: a,b=r['timestamp_ms'],r['timestamp_ms']+1
            acquired=any(evidence[ref]['kind'] in {'rest_response','archive_validation','archive_download'} for ref in refs)
            items.append(project(r,v['record_id'],link['run_id'],refs,a,b,v['record_key'] in conflicts,acquired))
    batches=[]; batch_groups={}
    for item in items:
        s=item['storage_record']
        if s is None or item['quarantined']: continue
        key=tuple(s[k] for k in ('venue','category','symbol','dataset','interval'))+(item['ingestion_run'],)
        batch_groups.setdefault(key,[]).append(item)
    publication_groups=[]
    for scope,values in batch_groups.items():
        by_identity={}
        for item in values: by_identity.setdefault(record_key(item['raw_record']),[]).append(item)
        # B rejects duplicate identities within one batch. Publish equivalent
        # source versions in separate layers so every provenance observation
        # reaches B while its single economic record remains idempotent.
        for position in range(max(map(len,by_identity.values()))):
            publication_groups.append((scope,[items[position] for items in by_identity.values() if position<len(items)]))
    for scope,values in publication_groups:
        unique={record_key(v['raw_record']):v for v in values}; records=[v['storage_record'] for v in unique.values()]
        refs=sorted({ref for v in values for ref in v['evidence_refs']})
        a=min(v['coverage']['start_ms'] for v in values); b=max(v['coverage']['end_ms'] for v in values)
        batch_id='batch:'+digest([scope,sorted(v['record_id'] for v in values),refs])
        # This is a local publication page, NOT invented exchange pagination.
        # Actual cursor chains/counts remain in evidence[ref].pagination unchanged.
        e=dict(zip(('venue','category','symbol','dataset','interval'),scope[:5]))
        acquisition=evidence_closure(refs,evidence)
        run_ids={scope[-1]}|{v['ingestion_run'] for v in acquisition.values() if v.get('ingestion_run') in runs}
        rest_pages=[v['pagination'] for v in acquisition.values() if v['kind']=='rest_response']
        archive_rows={v['checked']['sha256']:v['checked']['rows'] for v in acquisition.values() if v['kind']=='archive_validation'}
        e.update(start_ms=a,end_ms=b,status='UNVERIFIED',pages=[{'params':{'kind':'local_publication_batch','source_evidence_refs':refs},'cursor':None,'next_cursor':None,'count':len(records)}],count_semantics='unique_published_rows',source_response_count=sum(p['response_count'] for p in rest_pages) if rest_pages else None,source_archive_row_counts=archive_rows,evidence_refs=refs)
        e['acquisition_manifest']={'runs':{run:runs[run] for run in run_ids},'evidence':acquisition}
        batches.append({'batch_id':batch_id,'ingestion_run':scope[-1],'records':records,'evidence':e,'checkpoint':{'sequence':1,'export_batch':batch_id}})
    published_refs={ref for batch in batches for ref in batch['evidence']['acquisition_manifest']['evidence']}
    latest={}
    for task in tasks: latest[(task['run_id'],task['task_key'])]=task
    for (run_id,task_key),task in latest.items():
        run=runs[run_id]; request=run.get('request',{})
        if run['kind']!='rest_backfill' or task['event_id'] in published_refs: continue
        scope={'venue':'bybit',**{k:request[k] for k in ('category','symbol','interval')}}
        scope['dataset']=DATASETS.get(request['dataset'],request['dataset'])
        acquisition=evidence_closure([task['event_id']],evidence)
        pages=[e['pagination'] for e in acquisition.values() if e['kind']=='rest_response']
        failed=any(e['kind']=='rest_failure' for e in acquisition.values())
        response_count=sum(p['response_count'] for p in pages) if pages else None
        status='EMPTY' if pages and response_count==0 and not failed else 'FAILED' if failed and not pages else 'UNVERIFIED'
        detail=task['detail']; a=detail.get('start_ms',request['start_ms']); b=detail.get('end_ms',request['end_ms'])
        batch_id='batch:'+digest([run_id,task_key,task['event_id'],'observation_only'])
        proof={**scope,'start_ms':a,'end_ms':b,'status':status,'pages':[{'params':{'kind':'local_publication_batch','source_evidence_refs':[task['event_id']]},'cursor':None,'next_cursor':None,'count':0}],'count_semantics':'unique_published_rows','source_response_count':response_count,'source_archive_row_counts':{},'evidence_refs':[task['event_id']],'acquisition_manifest':{'runs':{run_id:run},'evidence':acquisition}}
        batches.append({'batch_id':batch_id,'ingestion_run':run_id,'records':[],'evidence':proof,'checkpoint':{'sequence':1,'export_batch':batch_id}})
    return {'schema_version':'1.0','format':'bybit-ingest-handoff','runs':runs,'tasks':tasks,'evidence':evidence,'records':items,'conflicts':[{'record_key':key,'record_ids':ids} for key,ids in conflicts.items()],'storage_batches':batches,'raw_record_versions':versions}
