"""Bounded, fail-closed read page and range proof from one PostgreSQL snapshot.

Legacy Store.read/provenance/coverage methods are intentionally unchanged.
No JSON aggregation or unbounded client fetchall; one bounded document per FETCH.
"""
import hashlib
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

import psycopg
from .coverage import coverage_report
from .models import DATASETS, CANDLES, canonical, digest, integer, normalize
from .provenance import source_run_status
from .knowledge_time import project_knowledge
from src.quality.candles import next_boundary


class Unavailable(Exception):
    def __init__(self,reason,outcome='RESOURCE_EXHAUSTED'):
        self.reason,self.outcome=reason,outcome


class Budget:
    def __init__(self,limits):
        self.knowledge_facts=[]
        self.limits=limits; self.deadline=time.monotonic()+limits['statement_timeout_ms']/1000
        self.usage=dict(rows=0,observations=0,run_links=0,evidence=0,intervals=0,findings=0,archives=0,
                        bytes=0,fetched_bytes=0,archive_bytes=0)

    def timeout(self,c):
        remaining=int((self.deadline-time.monotonic())*1000)
        if remaining<=0: raise Unavailable('deadline','UNAVAILABLE')
        c.execute("SELECT set_config('statement_timeout',%s,true)",(str(max(1,remaining)),))

    def docs(self,c,query,params,cap,label):
        self.timeout(c)
        remaining=self.limits['max_bytes']-4096-self.usage['bytes']
        # The overflow sentinel carries only ordinal/size, never a full extra document.
        sql='''WITH selected AS MATERIALIZED ('''+query+''' LIMIT %s), numbered AS
          (SELECT doc,sort_key,row_number() OVER(ORDER BY sort_key) AS n FROM selected)
          SELECT n,octet_length(convert_to(doc::text,'UTF8')) AS size,
          CASE WHEN n>%s OR octet_length(convert_to(doc::text,'UTF8'))>%s
            THEN NULL ELSE doc::text END AS encoded FROM numbered ORDER BY n'''
        with c.cursor(name='bounded_'+uuid.uuid4().hex) as cursor:
            cursor.itersize=1
            cursor.execute(sql,(*params,cap+1,cap,max(0,remaining)))
            while True:
                self.timeout(c)
                row=cursor.fetchone()
                if row is None: break
                if row['n']>cap:
                    if label=='rows': break  # ordinary page continuation, checked below
                    raise Unavailable(label+'_limit')
                if row['encoded'] is None: raise Unavailable('bytes_limit')
                self.usage['fetched_bytes']+=row['size']
                # Retained documents <=max_bytes; one transient FETCH is independently
                # bounded by the initial remaining budget, so fetched bytes <=2*max_bytes.
                if self.usage['bytes']+row['size']>self.limits['max_bytes']-4096:
                    raise Unavailable('bytes_limit')
                self.usage['bytes']+=row['size']; self.usage[label]+=1
                try: decoded=json.loads(row['encoded'])
                except (ValueError,TypeError,RecursionError): raise Unavailable('invalid_document','UNAVAILABLE')
                timing={k:v for k,v in decoded.items() if k.endswith(('_at','_recorded_at','_created_at'))}
                identity={k:decoded[k] for k in ('record_key','source_id','observation_id','run_id','evidence_id','archive_id','id') if k in decoded}
                fact=dict(kind=label,identity=identity,timestamps=timing)
                fact_bytes=len(canonical(fact).encode('utf-8'))
                if self.usage['bytes']+fact_bytes>self.limits['max_bytes']-4096:
                    raise Unavailable('bytes_limit')
                self.usage['bytes']+=fact_bytes
                self.knowledge_facts.append(fact)
                yield decoded


def record_doc(alias='r'):
    fields=('record_key','venue','category','symbol','dataset','interval','schema_version','timestamp_ms',
            'timestamp_ns','timestamp_us','timestamp_precision','trade_id','id_origin','content_hash','payload','inserted_at')
    return 'jsonb_build_object('+','.join("'%s',%s.%s"%(k,alias,k) for k in fields)+')'


def window_complete(e,records):
    """Recheck actual bounded support; historical checks flags are not sufficient."""
    if not records: return False
    if e['dataset']=='trades': return all(r['id_origin']=='exchange' for r in records)
    times=sorted(r['timestamp_ms'] for r in records)
    if e['dataset'] in CANDLES:
        return (times[0]==e['start_ms'] and next_boundary(times[-1],e['interval'])[0]==e['end_ms']
                and all(next_boundary(t,e['interval'])[1] for t in times)
                and all(next_boundary(a,e['interval'])[0]==b for a,b in zip(times,times[1:])))
    if e['dataset'] in ('open_interest','ratio'):
        step={'5min':300000,'15min':900000,'30min':1800000,'1h':3600000,'4h':14400000,'1d':86400000}.get(e['interval'])
        return bool(step and times[0]==e['start_ms'] and times[-1]+step==e['end_ms']
                    and all(t%step==0 for t in times) and all(b-a==step for a,b in zip(times,times[1:])))
    expected=e['available_range'].get('expected_timestamps')
    return bool(e['dataset']=='funding' and isinstance(expected,list) and len(expected)==len(times)
                and all(type(t) is int for t in expected) and sorted(set(expected))==times)


def within_horizon(start,end,clock_ms):
    """Half-open history must have elapsed at the single captured database cutoff."""
    return all(type(v) is int for v in (start,end,clock_ms)) and 0<=start<end<=clock_ms


def horizon_rejections(p,clock_ms):
    reasons=[]; body=p['evidence']
    pairs=[(p,'candidate_range')]
    if isinstance(body,dict):
        pairs.append((body,'candidate_range'))
        if body.get('available_range') is not None:
            pairs.append((body['available_range'],'availability_range'))
    else: reasons.append('candidate_range_invalid')
    for value,label in pairs:
        if not isinstance(value,dict): reasons.append(label+'_invalid'); continue
        start,end=value.get('start_ms'),value.get('end_ms')
        if not within_horizon(start,end,clock_ms):
            future=any(type(v) is int and v>clock_ms for v in (start,end))
            reasons.append(label+('_in_future' if future else '_invalid'))
    return sorted(set(reasons))


def read_page(store,dataset,venue,category,symbol,start_ms,end_ms,interval='',limit=1000,after=None,*,
              max_observations=4096,max_run_links=4096,max_evidence=256,max_intervals=512,max_findings=256,
              max_bytes=4*1024*1024,max_archive_bytes=16*1024*1024,statement_timeout_ms=3000):
    from .store import SCOPE, VALIDATOR, scope_key, validate_evidence
    if dataset not in DATASETS: raise ValueError('unsupported dataset')
    scope=dict(venue=venue,category=category,symbol=symbol,dataset=dataset,interval=interval)
    for k,v in scope.items():
        if not isinstance(v,str) or len(v)>200 or (k!='interval' and not v): raise ValueError('invalid scope')
    if dataset=='trades' and interval!='': raise ValueError('trades have no interval')
    integer(start_ms,'start_ms'); integer(end_ms,'end_ms')
    if end_ms<=start_ms: raise ValueError('empty requested range')
    limits=dict(limit=limit,max_observations=max_observations,max_run_links=max_run_links,max_evidence=max_evidence,max_intervals=max_intervals,
                max_findings=max_findings,max_bytes=max_bytes,max_archive_bytes=max_archive_bytes,statement_timeout_ms=statement_timeout_ms)
    ranges={'limit':(1,1000),'max_observations':(1,10000),'max_run_links':(1,10000),'max_evidence':(1,512),'max_intervals':(1,2048),
            'max_findings':(1,512),'max_bytes':(8192,16*1024*1024),'max_archive_bytes':(0,1024**3),'statement_timeout_ms':(100,10000)}
    for k,(lo,hi) in ranges.items():
        if type(limits[k]) is not int or not lo<=limits[k]<=hi: raise ValueError('invalid '+k)
    if after is not None:
        if not isinstance(after,(list,tuple)) or len(after)!=2: raise ValueError('invalid keyset cursor')
        integer(after[0],'cursor timestamp')
        if not start_ms<=after[0]<end_ms or not isinstance(after[1],str) or len(after[1])!=64 or any(x not in '0123456789abcdef' for x in after[1]):
            raise ValueError('invalid keyset cursor')
    budget=Budget(limits); key=scope_key(scope); snapshot=None
    response=dict(outcome='OK',scope=scope,rows=[],observations=[],run_links=[],evidence=[],next_cursor=None,page_exhausted=False,
                  coverage=dict(start_ms=start_ms,end_ms=end_ms,complete=False,state='UNKNOWN',proof_refs=[],reasons=[]),
                  snapshot=None,limits=limits|dict(max_fetched_bytes=2*max_bytes),usage=budget.usage)
    raw_where='r.venue=%s AND r.category=%s AND r.symbol=%s AND r.dataset=%s AND r.interval=%s'
    raw_args=tuple(scope[k] for k in SCOPE)
    range_where=raw_where+' AND r.timestamp_ms>=%s AND r.timestamp_ms<%s'
    range_args=(*raw_args,start_ms,end_ms)
    try:
        with store.connect() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            c.execute("SET LOCAL work_mem='4MB'")
            budget.timeout(c)
            meta=c.execute("SELECT current_setting('transaction_isolation') AS isolation,transaction_timestamp()::text AS started_at,pg_snapshot_xmin(pg_current_snapshot())::text AS xmin,floor(extract(epoch FROM transaction_timestamp())*1000)::bigint AS clock_ms").fetchone()
            snapshot=dict(meta); response['snapshot']=snapshot
            now_ms=snapshot['clock_ms']
            requested_elapsed=within_horizon(start_ms,end_ms,now_ms)
            where=range_where; args=range_args
            if after is not None: where+=' AND (r.timestamp_ms,r.record_key)>(%s,%s)'; args=(*args,*after)
            page_query='SELECT '+record_doc()+" AS doc,jsonb_build_array(r.timestamp_ms,r.record_key) AS sort_key FROM tester_storage.raw_records r WHERE "+where+' ORDER BY sort_key'
            rows=list(budget.docs(c,page_query,args,limit,'rows')); response['rows']=rows
            budget.timeout(c)
            if rows:
                last=rows[-1]
                more=c.execute('SELECT EXISTS(SELECT 1 FROM tester_storage.raw_records r WHERE '+range_where+' AND (r.timestamp_ms,r.record_key)>(%s,%s)) AS more',(*range_args,last['timestamp_ms'],last['record_key'])).fetchone()['more']
                response['page_exhausted']=not more
                if more: response['next_cursor']=[last['timestamp_ms'],last['record_key']]
            else: response['page_exhausted']=True

            interval_query='''SELECT jsonb_build_object('id',i.coverage_id,'start',i.start_ms,'end',i.end_ms,'state',i.state,
                'evidence_id',i.evidence_id,'created_at',i.created_at,'resolution',x.evidence_id,'resolved_at',x.resolved_at) AS doc,jsonb_build_array(i.coverage_id) AS sort_key
                FROM tester_storage.coverage_intervals i LEFT JOIN tester_storage.coverage_resolutions x USING(coverage_id)
                WHERE i.scope_key=%s AND i.start_ms<%s AND i.end_ms>%s ORDER BY sort_key'''
            intervals=list(budget.docs(c,interval_query,(key,end_ms,start_ms),max_intervals,'intervals'))
            finding_query='''SELECT jsonb_build_object('id',f.finding_id,'start',f.start_ms,'end',f.end_ms,
                'state',coalesce(f.detail->>'state','corrupt'),'created_at',f.created_at,'resolution',x.evidence_id,'resolved_at',x.resolved_at) AS doc,jsonb_build_array(f.finding_id) AS sort_key
                FROM tester_storage.quality_findings f LEFT JOIN tester_storage.finding_resolutions x USING(finding_id)
                WHERE f.scope_key=%s AND f.start_ms<%s AND f.end_ms>%s ORDER BY sort_key'''
            findings=list(budget.docs(c,finding_query,(key,end_ms,start_ms),max_findings,'findings'))
            proof_ids={i['evidence_id'] for i in intervals if i['state'] in ('available','verified') and i['evidence_id']}
            proof_ids.update(i['resolution'] for i in intervals+findings if i['resolution'])
            evidence_ids=proof_ids|{i['evidence_id'] for i in intervals if i['evidence_id']}
            if len(evidence_ids)>max_evidence: raise Unavailable('evidence_limit')

            observation_query='''SELECT jsonb_build_object('record','''+record_doc()+''','source_id',s.source_id,'source',s.source,
                'raw_envelope',s.raw_envelope,'source_recorded_at',s.recorded_at,
                'observation_recorded_at',o.recorded_at,'raw_inserted_at',r.inserted_at,'observation_id',o.observation_id,'run_id',o.run_id,'job_key',o.job_key,
                'evidence_id',o.evidence_id,'source_kind',o.source_kind,'timestamp_precision',o.timestamp_precision,'run_origin',u.run_origin,'run_created_at',u.created_at) AS doc,
                jsonb_build_array(r.timestamp_ms,r.record_key,coalesce(s.source_id,''),coalesce(o.observation_id,'')) AS sort_key
                FROM tester_storage.raw_records r LEFT JOIN tester_storage.record_sources s USING(record_key)
                LEFT JOIN tester_storage.source_observations o USING(source_id) LEFT JOIN tester_storage.ingestion_runs u USING(run_id) WHERE '''
            observations=list(budget.docs(c,observation_query+range_where+' ORDER BY sort_key',range_args,max_observations,'observations'))
            # Proofs may cover a larger interval than the requested page range. Include
            # their outside-range support too, under the SAME total observation/byte cap.
            for eid in sorted(proof_ids):
                remaining=max_observations-budget.usage['observations']
                q=observation_query+'o.evidence_id=%s AND NOT ('+range_where+') ORDER BY sort_key'
                observations.extend(budget.docs(c,q,(eid,*range_args),remaining,'observations'))
            evidence_ids.update(o['evidence_id'] for o in observations if o['evidence_id'])
            if len(evidence_ids)>max_evidence: raise Unavailable('evidence_limit')
            evidence_query='''SELECT jsonb_build_object('evidence_id',p.evidence_id,'job_key',p.job_key,'scope_key',p.scope_key,
                'start_ms',p.start_ms,'end_ms',p.end_ms,'status',p.status,'record_count',p.record_count,'verified',p.verified,
                'validator_version',p.validator_version,'created_at',p.created_at,'evidence',p.evidence) AS doc,jsonb_build_array(p.evidence_id) AS sort_key
                FROM tester_storage.pagination_evidence p WHERE p.evidence_id=ANY(%s) ORDER BY sort_key'''
            evidences=list(budget.docs(c,evidence_query,(sorted(evidence_ids),),max_evidence,'evidence'))
            by_evidence={e['evidence_id']:e for e in evidences}
            for p in evidences:
                p['horizon_rejections']=horizon_rejections(p,now_ms)
                p['within_read_horizon']=not p['horizon_rejections']
            support={eid:set() for eid in evidence_ids}; invalid=set(); conflicts=[]
            run_query='''SELECT jsonb_build_object('run_id',v.run_id,'job_key',v.job_key,'evidence_id',v.evidence_id,'run_origin',u.run_origin,'run_created_at',u.created_at,
                'link_created_at',NULL,'run_job_link_created_at',NULL) AS doc,
                jsonb_build_array(v.run_id,v.job_key,v.evidence_id) AS sort_key FROM tester_storage.ingestion_run_evidence v
                JOIN tester_storage.ingestion_runs u USING(run_id) WHERE v.evidence_id=ANY(%s) ORDER BY sort_key'''
            run_links=list(budget.docs(c,run_query,(sorted(evidence_ids),),max_run_links,'run_links'))
            response['run_links']=run_links
            for link in run_links:
                p=by_evidence.get(link['evidence_id'])
                link['evidence_run_status']=source_run_status(p['evidence'],link['run_id']) if p else 'invalid'
                if link['run_origin']!='caller' or link['evidence_run_status'] in ('invalid','mismatched'):
                    invalid.add(link['evidence_id'])
            record_keys=set(); source_ids=set(); records_seen={}
            for o in observations:
                r=o.pop('record'); rk=r['record_key']; record_keys.add(rk)
                records_seen[rk]=r
                o['record_key']=rk
                if o['source_id']: source_ids.add(o['source_id'])
                status=source_run_status(o['source'],o['run_id'])
                o['source_run_status']=status
                authentic=o['run_origin']=='caller' and status=='matched' and o['source_kind'] in ('rest','archive','import')
                try:
                    n=normalize(o['raw_envelope'])
                    semantic=all(n.get(k)==r.get(k) for k in r if k not in ('schema_version','inserted_at')) and n['schema_version']==r['schema_version']
                    semantic &= n['source']==o['source'] and digest([rk,n['source'],o['raw_envelope']])==o['source_id']
                    semantic &= o['source_kind']==n['source'].get('source_kind',o['raw_envelope'].get('source_kind','unknown'))
                    semantic &= o['timestamp_precision']==r['timestamp_precision'] and r['timestamp_ms']<=now_ms+2000
                    p=by_evidence.get(o['evidence_id'])
                    semantic &= bool(p and p['scope_key']==scope_key(n) and p['start_ms']<=r['timestamp_ms']<p['end_ms'])
                    evidence_status=source_run_status(p['evidence'],o['run_id']) if p else 'invalid'
                    semantic &= evidence_status in ('matched','undeclared')
                    if dataset in CANDLES: semantic &= next_boundary(r['timestamp_ms'],interval)[0]+2000<=now_ms
                except (ValueError,TypeError,KeyError,OverflowError,RecursionError): semantic=False
                o['authentic_run']=bool(authentic and semantic)
                eid=o['evidence_id']
                o['evidence_run_status']=source_run_status(by_evidence[eid]['evidence'],o['run_id']) if eid in by_evidence else 'invalid'
                if eid:
                    support.setdefault(eid,set()).add(rk)
                    if not o['authentic_run']: invalid.add(eid)
                if status in ('invalid','mismatched') or (o['source_id'] and not semantic): conflicts.append('source_conflict')
                if not o['source_id']: conflicts.append('missing_source')
            response['observations']=observations; response['evidence']=evidences

            relevant_ids=list(record_keys|source_ids|evidence_ids)
            quarantine_query='''SELECT jsonb_build_object('id',object_id,'reason',reason,'recorded_at',NULL) AS doc,jsonb_build_array(object_id,scan_id) AS sort_key
                FROM tester_storage.lifecycle_quarantine WHERE object_id=ANY(%s) ORDER BY sort_key'''
            quarantine=list(budget.docs(c,quarantine_query,(relevant_ids,),max_findings-budget.usage['findings'],'findings'))
            quarantined={q['id'] for q in quarantine}
            if quarantine: conflicts.append('quarantined_source_or_evidence')

            valid=set(); checked_archives=set()
            for eid in sorted(proof_ids):
                p=by_evidence.get(eid)
                if not p or not p['within_read_horizon'] or eid in invalid or eid in quarantined: continue
                try:
                    e,count=validate_evidence(p['evidence'])
                    checks=e.get('checks',{})
                    required=('values','identity','utc_bounds','closed_candles','contiguous','archive_complete')
                    if not (p['verified'] and p['status']==e['status']=='EXHAUSTED' and p['validator_version']==VALIDATOR and count>0
                            and p['record_count']==count==len(support.get(eid,set())) and p['scope_key']==key==scope_key(e)
                            and all(e.get(k)==v for k,v in scope.items()) and p['start_ms']==e['start_ms'] and p['end_ms']==e['end_ms']
                            and e.get('available_range') and all(checks.get(k) is True for k in required)): continue
                    if not window_complete(e,[records_seen[rk] for rk in support[eid]]): continue
                    # A prior verified proof becomes stale if additional confirmed records
                    # now exist in its window but were not part of that evidence's batch.
                    budget.timeout(c)
                    extra=c.execute('''SELECT EXISTS(SELECT 1 FROM tester_storage.raw_records r WHERE '''+raw_where+'''
                        AND r.timestamp_ms>=%s AND r.timestamp_ms<%s AND NOT EXISTS
                        (SELECT 1 FROM tester_storage.record_sources s JOIN tester_storage.source_observations o USING(source_id)
                         WHERE s.record_key=r.record_key AND o.evidence_id=%s)) AS extra''',(*raw_args,e['start_ms'],e['end_ms'],eid)).fetchone()['extra']
                    if extra: continue
                    available=e['available_range']
                    if available['method']=='archive_manifest':
                        archive_query='''SELECT jsonb_build_object('archive_id',archive_id,'start_ms',start_ms,'end_ms',end_ms,'row_count',row_count,
                            'url',url,'storage_uri',storage_uri,'sha256',sha256,'bytes',bytes,'created_at',created_at) AS doc,jsonb_build_array(archive_id) AS sort_key
                            FROM tester_storage.source_archives WHERE scope_key=%s AND sha256=%s AND start_ms=%s AND end_ms=%s ORDER BY sort_key'''
                        archives=list(budget.docs(c,archive_query,(key,available['sha256'],e['start_ms'],e['end_ms']),max_evidence-budget.usage['archives'],'archives'))
                        new_archive_ids={a['archive_id'] for a in archives}-checked_archives
                        if new_archive_ids:
                            archive_quarantine=list(budget.docs(c,quarantine_query,(sorted(new_archive_ids),),
                                max_findings-budget.usage['findings'],'findings'))
                            checked_archives.update(new_archive_ids)
                            quarantined.update(q['id'] for q in archive_quarantine)
                            if archive_quarantine: conflicts.append('quarantined_archive')
                        archives=[a for a in archives if a['archive_id'] not in quarantined]
                        matches=[a for a in archives if a['row_count']==count and a['url']==available['source_url']]
                        if not matches or not any(_archive_ok(a,budget,c) for a in matches): continue
                        if any(o['source'].get('sha256')!=available['sha256'] for o in observations if o['evidence_id']==eid): continue
                    valid.add(eid)
                except (ValueError,TypeError,KeyError,OverflowError,RecursionError): continue

            def resolves(row):
                p=by_evidence.get(row['resolution'])
                try:
                    chronology=bool(p and datetime.fromisoformat(row['created_at'])<=datetime.fromisoformat(p['created_at'])<=datetime.fromisoformat(row['resolved_at']))
                except (ValueError,TypeError,KeyError): chronology=False
                return bool(p and chronology and p['evidence_id'] in valid and p['start_ms']<=row['start']<row['end']<=p['end_ms'])
            active=[]; active_proofs=set()
            for i in intervals:
                if resolves(i): continue
                if i['state'] in ('available','verified'):
                    p=by_evidence.get(i['evidence_id'])
                    if not p or i['evidence_id'] not in valid or not p['start_ms']<=i['start']<i['end']<=p['end_ms']: continue
                    active_proofs.add(i['evidence_id'])
                active.append(i)
            for f in findings:
                if not resolves(f): active.append(dict(start=f['start'],end=f['end'],state=f['state'] if f['state'] in ('unknown','unavailable','corrupt') else 'corrupt'))
            report=coverage_report(start_ms,end_ms,active)
            interval_complete=report.pop('complete')  # only the outer authenticated flag is authoritative
            complete=bool(requested_elapsed and rows and interval_complete and not conflicts)
            reasons=sorted(set(conflicts))
            if not requested_elapsed: reasons.append('requested_range_in_future')
            if not rows: reasons.append('no_page_records')
            if not interval_complete: reasons.append('range_not_actively_verified')
            if not complete:
                reasons.extend(reason for p in evidences if p['evidence_id'] in proof_ids for reason in p['horizon_rejections'])
                reasons=sorted(set(reasons))
            response['coverage']=dict(start_ms=start_ms,end_ms=end_ms,complete=complete,state='COMPLETE' if complete else 'UNKNOWN',
                proof_refs=sorted(active_proofs) if complete else [],reasons=reasons,intervals=report)
            for p in evidences: p['active_proof']=complete and p['evidence_id'] in active_proofs
            budget.timeout(c)
            response['knowledge_time']=project_knowledge(budget.knowledge_facts)
            # Final serialized response ceiling includes duplicated refs/envelope overhead.
            if len(canonical(response).encode('utf-8'))>max_bytes: raise Unavailable('bytes_limit')
        return response
    except Unavailable as exc:
        outcome,reason=exc.outcome,exc.reason
    except psycopg.errors.QueryCanceled:
        outcome,reason='UNAVAILABLE','statement_timeout'
    except psycopg.Error:
        outcome,reason='UNAVAILABLE','database_read_failed'
    except RecursionError:
        outcome,reason='UNAVAILABLE','invalid_document'
    return dict(outcome=outcome,scope=scope,rows=[],observations=[],run_links=[],evidence=[],next_cursor=None,page_exhausted=False,
        coverage=dict(start_ms=start_ms,end_ms=end_ms,complete=False,state='UNKNOWN',proof_refs=[],reasons=[reason]),
        snapshot=snapshot,limits=response['limits'],usage=budget.usage,knowledge_time=project_knowledge([],reason))


def _archive_ok(manifest,budget,c):
    path=Path(manifest['storage_uri'])
    if str(path).startswith(('\\\\','//')): return False  # no external share acquisition
    if manifest['bytes']>budget.limits['max_archive_bytes']-budget.usage['archive_bytes']:
        raise Unavailable('archive_bytes_limit')
    try:
        if path.is_symlink() or not path.is_file(): return False
        before=path.stat()
        if before.st_size!=manifest['bytes']: return False
        h=hashlib.sha256()
        remaining=manifest['bytes']
        with path.open('rb') as stream:
            while remaining:
                if time.monotonic()>budget.deadline: raise Unavailable('deadline','UNAVAILABLE')
                chunk=stream.read(min(65536,remaining))
                if not chunk: return False
                budget.usage['archive_bytes']+=len(chunk)
                remaining-=len(chunk)
                h.update(chunk)
        if time.monotonic()>budget.deadline: raise Unavailable('deadline','UNAVAILABLE')
        after=path.stat()
        return (before.st_size,before.st_mtime_ns,before.st_ino)==(after.st_size,after.st_mtime_ns,after.st_ino) and h.hexdigest()==manifest['sha256']
    except OSError: return False
