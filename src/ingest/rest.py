"""Endpoint-aware bounded REST backfill. All time ranges are UTC [start,end)."""
import calendar
import hashlib
import json
import time
import base64
import uuid
from datetime import datetime,timezone
from urllib.parse import urlencode
from .transport import Transport
from .journal import utcnow

INTERVALS=('1','3','5','15','30','60','120','240','360','720','D','W','M')
PERIODS={'5min':300000,'15min':900000,'30min':1800000,'1h':3600000,'4h':14400000,'1d':86400000}
SPECS={
 'kline':('/kline',1000,None,'interval'),
 'mark':('/mark-price-kline',1000,None,'interval'),
 'index':('/index-price-kline',1000,None,'interval'),
 'premium':('/premium-index-price-kline',1000,None,'interval'),
 'open_interest':('/open-interest',200,'timestamp','intervalTime'),
 'funding':('/funding/history',200,'fundingRateTimestamp',None),
 'long_short_ratio':('/account-ratio',500,'timestamp','period'),
}

def next_time(ts,interval):
    if interval=='M':
        d=datetime.fromtimestamp(ts/1000,timezone.utc)
        year=d.year+(d.month==12); month=d.month%12+1
        return int(d.replace(year=year,month=month,day=min(d.day,calendar.monthrange(year,month)[1])).timestamp()*1000)
    return ts+({'D':86400000,'W':604800000}.get(interval) or PERIODS.get(interval) or int(interval)*60000)

def expected_times(start,end,interval):
    d=datetime.fromtimestamp(start/1000,timezone.utc)
    if interval=='M': ts=int(d.replace(day=1,hour=0,minute=0,second=0,microsecond=0).timestamp()*1000)
    elif interval=='W':
        ts=(start-345600000)//604800000*604800000+345600000 # Monday UTC
    else:
        step=next_time(0,interval); ts=start//step*step
    if ts<start: ts=next_time(ts,interval)
    while ts<end:
        yield ts; ts=next_time(ts,interval)

class API:
    def __init__(self,transport=None): self.transport=transport or Transport()
    def server_time_ms(self):
        result,_=self.call('/time',{})
        return int(result['timeSecond'])*1000
    def call(self,path,params):
        if not path.startswith('/') or '..' in path: raise ValueError('Invalid endpoint')
        url='https://api.bybit.com/v5/market'+path+'?'+urlencode(params)
        for attempt in range(6):
            with self.transport.get(url) as r:
                body=r.json(); code=body.get('retCode')
                if code==0:
                    self.last_response={'url':url,'bytes_base64':base64.b64encode(r.content).decode(),'sha256':hashlib.sha256(r.content).hexdigest(),'hash_scope':'original_http_body','status_code':r.status_code}
                    return body['result'],url
                if code not in {10000,10006,10016,10018}: raise ValueError(f'Bybit {code}: {body.get("retMsg")}')
                reset=r.headers.get('X-Bapi-Limit-Reset-Timestamp')
                delay=max(2**attempt,float(reset)/1000-time.time() if reset else 0)
            if attempt==5: raise RuntimeError(f'Bybit retry exhausted: {code}')
            self.transport.defer(delay)
        raise RuntimeError('Unreachable')

class Backfill:
    def __init__(self,api,journal): self.api=api; self.journal=journal

    def run(self,category,symbol,kind,interval,start,end,limit=None,window_ms=7*86400000,cancel=None):
        if category not in {'spot','linear','inverse'} or kind not in SPECS: raise ValueError('Unsupported category/dataset')
        if category=='spot' and kind!='kline' or kind=='premium' and category!='linear': raise ValueError('Dataset unavailable for category')
        if start<0 or end<=start or window_ms<=0: raise ValueError('Invalid range')
        path,maxlimit,field,periodkey=SPECS[kind]
        if periodkey=='interval' and interval not in INTERVALS: raise ValueError('Invalid candle interval')
        if periodkey in {'intervalTime','period'} and interval not in PERIODS: raise ValueError('Invalid metric period')
        limit=maxlimit if limit is None else limit
        if not 1<=limit<=maxlimit: raise ValueError('Invalid endpoint limit')
        if cancel and cancel(): return {'status':'paused','records':0,'observations':[]}
        run_id=self.journal.start_run('rest_backfill',{'category':category,'symbol':symbol,'dataset':kind,'interval':interval,'start_ms':start,'end_ms':end})
        try: server_ms=self.api.server_time_ms() if field is None else None
        except Exception:
            self.journal.finish_run(run_id,'FAILED'); raise
        observations=[]; count=0
        for lo in range(start,end,window_ms):
            hi=min(end,lo+window_ms)
            key='rest-v3:'+json.dumps([category,symbol,kind,interval,lo,hi])
            cached=self.journal.task(key)
            if cached and cached['status']=='observed' and cached.get('evidence_refs'):
                observations.append(cached); count+=cached['records']; continue
            if cancel and cancel():
                self.journal.finish_run(run_id,'PARTIAL')
                return {'status':'paused','records':count,'observations':observations}
            params={'category':category,'symbol':symbol,'limit':limit}
            if periodkey: params[periodkey]=interval
            sk,ek=('start','end') if field is None else ('startTime','endTime')
            rows=self.journal.window_records(category,symbol,kind,interval,lo,hi)
            if field is None: rows={ts:r for ts,r in rows.items() if next_time(ts,interval)<=server_ms}
            requests_made=[]; unresolved=[]; evidence_refs=[]
            def query(a,b,token=None):
                p={**params,sk:a,ek:b-1}
                if token: p['cursor']=token
                try: result,url=self.api.call(path,p)
                except Exception as exc:
                    ref=self.journal.add_evidence({'kind':'rest_failure','request_id':uuid.uuid4().hex,'ingestion_run':run_id,'endpoint':'https://api.bybit.com/v5/market'+path,'request_params':p,'start_ms':a,'end_ms':b,'cursor_in':token,'response_count':None,'terminal_condition':'FAILED','error_type':type(exc).__name__})
                    evidence_refs.append(ref); raise
                stamp=utcnow()
                response_count=len(result.get('list',[]))
                terminal='PARTIAL' if result.get('nextPageCursor') or response_count>=limit else 'EXHAUSTED' if response_count else 'EMPTY'
                pagination={'schema_version':'1.0','request_id':uuid.uuid4().hex,'endpoint':url,'request_params':p,'start_ms':a,'end_ms':b,'cursor_in':token,'cursor_out':result.get('nextPageCursor'),'response_count':response_count,'terminal_condition':terminal,'coverage_status':'PARTIAL' if response_count else 'UNKNOWN'}
                original=getattr(self.api,'last_response',None)
                proof={'kind':'rest_response','ingestion_run':run_id,'fetched_at':stamp,'pagination':pagination,'cursor_out_field_present':'nextPageCursor' in result,'raw_response':result,'raw_hash':hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'hash_scope':'canonical_result_json'}
                if isinstance(original,dict): proof['http_response']=original
                ref=self.journal.add_evidence(proof); evidence_refs.append(ref)
                requests_made.append({'url':url,'fetched_at':stamp,'params':p,'cursor':token,'next_cursor':result.get('nextPageCursor'),'start_ms':a,'end_ms':b,'returned':response_count,'response_count':response_count,'pagination_status':terminal,'evidence_ref':ref})
                accepted=[]; published=[]
                for row in result.get('list',[]):
                    ts=int(row[0] if field is None else row[field])
                    if not a<=ts<b: raise ValueError('Endpoint returned rows outside requested window')
                    accepted.append(ts)
                    if field is None and next_time(ts,interval)>server_ms: continue
                    record={'schema_version':'1.0','venue':'bybit','category':category,'symbol':symbol,'dataset':kind,'interval':interval,'timestamp_ms':ts,'timestamp_ns':None,'timestamp_precision':'ms','payload':row,'source':{'url':url,'fetched_at':stamp,'sha256':hashlib.sha256(json.dumps(row,separators=(',',':')).encode()).hexdigest(),'hash_scope':'canonical_row_json','source_kind':'bybit_rest','source_timestamp':str(row[0] if field is None else row[field]),'ingestion_run':run_id,'evidence_refs':[ref]}}
                    if field is None: record.update(is_closed=True,closure_server_ms=server_ms)
                    rows[ts]=record; published.append(record)
                self.journal.save(key,'running',{'start_ms':lo,'end_ms':hi,'requests':[requests_made[-1]],'evidence_refs':[ref],'unique_published_rows':len({r['timestamp_ms'] for r in published})},published,run_id=run_id)
                return result,accepted
            class Paused(Exception): pass
            def paginate(a,b,probe=True):
                cursor_end=b; token=None; seen=set()
                while cursor_end>a:
                    if cancel and cancel(): raise Paused()
                    result,times=query(a,cursor_end,token)
                    newtoken=result.get('nextPageCursor')
                    if newtoken:
                        if newtoken in seen: raise ValueError('Repeated pagination cursor')
                        seen.add(newtoken); token=newtoken; continue
                    token=None
                    if not times:
                        if probe:
                            # Each follow-up goes through full pagination, even if capped.
                            paginate(a,cursor_end,False)
                            mid=(a+cursor_end)//2
                            if mid>a:
                                paginate(a,mid,False); paginate(mid,cursor_end,False)
                        # No cadence or listing boundary proves a funding range empty.
                        if not probe:
                            unresolved.append({'start_ms':a,'end_ms':cursor_end})
                        break
                    oldest=min(times)
                    if oldest>=cursor_end: raise ValueError('Pagination failed to progress')
                    cursor_end=oldest
                    if len(times)<limit: break
            try:
                paginate(lo,hi)
                gaps=[]
                if periodkey:
                    gaps=[t for t in expected_times(lo,hi,interval) if t not in rows]
                    # Bounded per-point repair; persistent unverified windows are retried next run.
                    for ts in gaps[:100]:
                        if cancel and cancel(): raise Paused()
                        query(ts,min(hi,next_time(ts,interval)))
                    gaps=[t for t in expected_times(lo,hi,interval) if t not in rows]
                status='observed' if rows and not gaps and (periodkey or not unresolved) else 'unverified'
                detail={'start_ms':lo,'end_ms':hi,'records':len(rows),'unique_published_rows':len(rows),'response_count':sum(p['response_count'] for p in requests_made),'first_observed_ms':min(rows) if rows else None,'last_observed_ms':max(rows) if rows else None,'missing_count':len(gaps),'missing_sample':gaps[:100],'unknown_ranges':unresolved,'requests':requests_made,'evidence_refs':evidence_refs,'status':status,'pagination_status':'UNVERIFIED','availability':{'status':'AVAILABLE' if rows else 'UNVERIFIED','reason':None if rows else 'Empty responses do not establish history availability'},'coverage':{'status':'PARTIAL' if rows else 'UNKNOWN','start_ms':lo,'end_ms':hi,'evidence_refs':evidence_refs}}
                self.journal.save(key,status,detail,rows.values(),run_id=run_id)
                observations.append(detail); count+=len(rows)
            except Paused:
                self.journal.save(key,'paused',{'start_ms':lo,'end_ms':hi,'records':len(rows),'requests':requests_made,'evidence_refs':evidence_refs},rows.values(),run_id=run_id)
                self.journal.finish_run(run_id,'PARTIAL')
                return {'status':'paused','records':count+len(rows),'observations':observations}
            except Exception as exc:
                self.journal.save(key,'failed',{'start_ms':lo,'end_ms':hi,'error':str(exc),'requests':requests_made,'pagination_status':'FAILED','evidence_refs':evidence_refs},rows.values(),run_id=run_id)
                self.journal.finish_run(run_id,'FAILED'); raise
        status='observed' if all(x['status']=='observed' for x in observations) else 'unverified'
        self.journal.finish_run(run_id,'PARTIAL' if count else 'UNVERIFIED')
        return {'status':status,'records':count,'observations':observations,'ingestion_run':run_id}
