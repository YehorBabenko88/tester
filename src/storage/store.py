"""Atomic publication, immutable raw data, evidence ledger and bounded keyset reads."""
import copy
import hashlib
import os
import time
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src.quality.candles import inspect, next_boundary
from .coverage import coverage_report
from .models import CANDLES, DATASETS, canonical, digest, integer, normalize
from .archives import verify_archive

SCOPE=('venue','category','symbol','dataset','interval')
VALIDATOR='storage-1.0'


class DataConflict(ValueError):
    """Same exchange identity, different semantic data. Original data is preserved."""


def scope_key(record):
    return digest([record.get(k,'') for k in SCOPE])


def validate_evidence(evidence):
    e=copy.deepcopy(evidence)
    for k in SCOPE:
        if k=='interval': e.setdefault(k,'')
        if not isinstance(e.get(k),str) or (k!='interval' and not e[k]): raise ValueError('missing evidence scope')
    if e['dataset'] not in DATASETS: raise ValueError('unsupported evidence dataset')
    if e['dataset']=='trades' and e['interval']!='': raise ValueError('trades have no interval')
    a=integer(e.get('start_ms'),'start_ms'); b=integer(e.get('end_ms'),'end_ms')
    if a>=b: raise ValueError('invalid evidence range')
    if e.get('status') not in ('EXHAUSTED','EMPTY','PARTIAL','FAILED','UNVERIFIED'): raise ValueError('invalid pagination status')
    pages=e.get('pages')
    if not isinstance(pages,list) or not pages: raise ValueError('page evidence required')
    previous=None; cursors=set()
    for i,p in enumerate(pages):
        if not isinstance(p.get('params'),dict): raise ValueError('request parameters required')
        integer(p.get('count'),'page count')
        if p.get('cursor')!=previous: raise ValueError('broken page cursor chain')
        cursor=p.get('next_cursor')
        if cursor is not None and (not isinstance(cursor,str) or not cursor or cursor in cursors): raise ValueError('repeated/invalid cursor')
        if cursor is not None: cursors.add(cursor)
        if i<len(pages)-1 and cursor is None: raise ValueError('page chain continues after terminal page')
        previous=cursor
    if e['status'] in ('EXHAUSTED','EMPTY') and previous is not None: raise ValueError('pagination not exhausted')
    count=sum(p['count'] for p in pages)
    if e['status']=='EMPTY' and count: raise ValueError('nonempty EMPTY evidence')
    available=e.get('available_range')
    if available is not None:
        if available.get('method') not in ('archive_manifest','exchange_metadata','operator_attestation'): raise ValueError('unsupported availability evidence')
        if not integer(available.get('start_ms'),'available start')<=a<b<=integer(available.get('end_ms'),'available end'): raise ValueError('availability does not cover request')
        sha=available.get('sha256','')
        if not available.get('source_url') or not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha): raise ValueError('availability provenance required')
    canonical(e)
    return e,count


class Store:
    def __init__(self,dsn=None):
        self._dsn=dsn if dsn is not None else os.getenv('TESTER_STORAGE_DSN','')

    def connect(self):
        return psycopg.connect(self._dsn,connect_timeout=8,options='-c timezone=UTC',row_factory=dict_row)

    @staticmethod
    def _lock(c,key):
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tester_storage:'+key,))

    def ingest(self,records,evidence,job_key,checkpoint=None):
        """Stream one atomic batch. A checkpoint is published only with all records/evidence.

        Successful replay is a no-op for raw data. Exceptions roll back the entire batch.
        Failed semantic replay writes a finding in a separate transaction AFTER rollback.
        """
        e,expected_count=validate_evidence(evidence); scope=scope_key(e)
        if not isinstance(job_key,str) or not job_key: raise ValueError('job_key required')
        try:
            with self.connect() as c:
                self._lock(c,scope)
                available=e.get('available_range')
                archive_sha=None; archive_complete=True
                if available and available['method']=='archive_manifest':
                    archive=c.execute('''SELECT * FROM tester_storage.source_archives WHERE scope_key=%s AND sha256=%s
                        AND start_ms<=%s AND end_ms>=%s''',(scope,available['sha256'],e['start_ms'],e['end_ms'])).fetchone()
                    if not archive or not verify_archive(archive): raise ValueError('verified source archive is unavailable')
                    archive_sha=available['sha256']
                    archive_complete=(archive['row_count']==expected_count and archive['start_ms']==e['start_ms'] and archive['end_ms']==e['end_ms'])
                request={k:e[k] for k in (*SCOPE,'start_ms','end_ms')}
                old=c.execute('SELECT request FROM tester_storage.download_jobs WHERE job_key=%s FOR UPDATE',(job_key,)).fetchone()
                if old and old['request']!=request: raise ValueError('job key reused for different request')
                previous=c.execute('SELECT checkpoint FROM tester_storage.ingestion_checkpoints WHERE job_key=%s',(job_key,)).fetchone()
                previous=previous['checkpoint'] if previous else {}
                next_checkpoint=copy.deepcopy(previous if checkpoint is None else checkpoint)
                if not isinstance(next_checkpoint,dict): raise ValueError('checkpoint must be an object')
                canonical(next_checkpoint)
                for position in ('sequence','offset'):
                    if position in next_checkpoint: integer(next_checkpoint[position],'checkpoint '+position)
                if previous and next_checkpoint!=previous:
                    position='sequence' if 'sequence' in previous else 'offset' if 'offset' in previous else None
                    if position is None:
                        if 'sequence' not in next_checkpoint: raise ValueError('changed opaque checkpoint requires sequence')
                    elif position not in next_checkpoint or next_checkpoint[position]<previous[position]:
                        raise ValueError('checkpoint cannot regress')
                    elif next_checkpoint[position]==previous[position]:
                        raise ValueError('same checkpoint position has different cursor/state')
                c.execute('''INSERT INTO tester_storage.download_jobs(job_key,scope_key,symbol,request,state)
                    VALUES(%s,%s,%s,%s,'running') ON CONFLICT(job_key) DO UPDATE SET state='running',updated_at=now()''',
                    (job_key,scope,e['symbol'],Jsonb(request)))
                count=0; composite=False; candle_rows=[]; observation_times=[]; rolling=hashlib.sha256(); seen=set()
                for raw in records:
                    r=normalize(raw)
                    if any(r[k]!=e[k] for k in SCOPE): raise ValueError('record/evidence scope mismatch')
                    if not e['start_ms']<=r['timestamp_ms']<e['end_ms']: raise ValueError('record outside requested range')
                    if r['timestamp_ms']>time.time_ns()//1000000+2000: raise ValueError('future source timestamp')
                    if archive_sha and r['source'].get('sha256')!=archive_sha: raise ValueError('record/archive provenance mismatch')
                    if r['record_key'] in seen: raise ValueError('duplicate record identity in batch')
                    seen.add(r['record_key'])
                    composite |= r['id_origin']=='composite_v1'
                    if r['dataset'] in CANDLES:
                        p=r['payload']; candle_rows.append([r['timestamp_ms'],*[p[x] for x in ('open','high','low','close')],p.get('volume') or '0'])
                    elif r['dataset']!='trades': observation_times.append(r['timestamp_ms'])
                    c.execute('INSERT INTO tester_storage.instruments(venue,category,symbol) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                              (r['venue'],r['category'],r['symbol']))
                    old=c.execute('SELECT content_hash FROM tester_storage.raw_records WHERE record_key=%s',(r['record_key'],)).fetchone()
                    if old and old['content_hash']!=r['content_hash']: raise DataConflict('identity conflicts with confirmed raw record')
                    if not old:
                        columns=('record_key',*SCOPE,'schema_version','timestamp_ms','timestamp_ns','timestamp_us','timestamp_precision','trade_id','id_origin','content_hash')
                        vals=[r.get(k) for k in columns]
                        c.execute('INSERT INTO tester_storage.raw_records('+','.join(columns)+',payload) VALUES('+','.join(['%s']*(len(vals)+1))+')',
                                  (*vals,Jsonb(r['payload'])))
                    source_id=digest([r['record_key'],r['source'],raw])
                    c.execute('''INSERT INTO tester_storage.record_sources(source_id,record_key,symbol,source,raw_envelope)
                        VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                        (source_id,r['record_key'],r['symbol'],Jsonb(r['source']),Jsonb(raw)))
                    rolling.update(r['content_hash'].encode('ascii')); count+=1
                if count!=expected_count: raise ValueError('page record count mismatch')
                checks={'values':True,'identity':True,'utc_bounds':True,'closed_candles':True,'contiguous':True}
                if candle_rows:
                    quality=inspect(candle_rows,e['interval'],time.time_ns()//1000000,allow_negative=e['dataset']=='premium')
                    if quality['errors']: raise ValueError('candle quality validation failed')
                    times=sorted(row[0] for row in candle_rows)
                    checks['contiguous']=not quality['gaps'] and times[0]==e['start_ms'] and next_boundary(times[-1],e['interval'])[0]==e['end_ms']
                if e['dataset'] in ('open_interest','ratio'):
                    step={'5min':300000,'15min':900000,'30min':1800000,'1h':3600000,'4h':14400000,'1d':86400000}.get(e['interval'])
                    times=sorted(observation_times)
                    checks['contiguous']=bool(step and times and times[0]==e['start_ms'] and times[-1]+step==e['end_ms']
                        and all(t%step==0 for t in times) and all(v-u==step for u,v in zip(times,times[1:])))
                if e['dataset']=='funding':
                    # Funding schedules can change; do not assume a universal eight-hour cadence.
                    inventory=(available or {}).get('expected_timestamps')
                    checks['contiguous']=bool(isinstance(inventory,list) and inventory and all(type(t) is int for t in inventory)
                        and sorted(set(inventory))==sorted(observation_times) and len(inventory)==len(observation_times))
                checks['archive_complete']=archive_complete
                verified=bool(count and e['status']=='EXHAUSTED' and e.get('available_range') and not composite and all(checks.values()))
                e['checks']=checks; e['source_fingerprint']=rolling.hexdigest()
                evidence_id=digest([job_key,e])
                c.execute('''INSERT INTO tester_storage.pagination_evidence(evidence_id,job_key,scope_key,symbol,start_ms,end_ms,status,record_count,evidence,validator_version,verified)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (evidence_id,job_key,scope,e['symbol'],e['start_ms'],e['end_ms'],e['status'],count,Jsonb(e),VALIDATOR,verified))
                for state in ['requested']+(['downloaded'] if count else ['unknown'])+(['available','verified'] if verified else []):
                    self._coverage(c,e,state,evidence_id,'ingest '+e['status'])
                if verified:
                    # Successful retry supersedes earlier empty acquisition evidence only.
                    # Findings and explicit damage require the separately audited resolves list.
                    c.execute('''INSERT INTO tester_storage.coverage_resolutions(coverage_id,evidence_id)
                        SELECT coverage_id,%s FROM tester_storage.coverage_intervals WHERE scope_key=%s
                        AND state='unknown' AND reason LIKE 'ingest %%' AND start_ms>=%s AND end_ms<=%s
                        ON CONFLICT DO NOTHING''',(evidence_id,scope,e['start_ms'],e['end_ms']))
                for finding_id in e.get('resolves',[]):
                    if not verified: raise ValueError('resolution requires fresh verified evidence')
                    finding=c.execute('SELECT * FROM tester_storage.quality_findings WHERE finding_id=%s',(finding_id,)).fetchone()
                    if not finding or finding['scope_key']!=scope or not e['start_ms']<=finding['start_ms']<finding['end_ms']<=e['end_ms']: raise ValueError('resolution scope/range mismatch')
                    c.execute('INSERT INTO tester_storage.finding_resolutions(finding_id,evidence_id) VALUES(%s,%s) ON CONFLICT DO NOTHING',(finding_id,evidence_id))
                    c.execute('''INSERT INTO tester_storage.coverage_resolutions(coverage_id,evidence_id)
                        SELECT coverage_id,%s FROM tester_storage.coverage_intervals WHERE coverage_id=%s ON CONFLICT DO NOTHING''',(evidence_id,finding_id))
                c.execute('''INSERT INTO tester_storage.ingestion_checkpoints(job_key,checkpoint,commit_hash) VALUES(%s,%s,%s)
                    ON CONFLICT(job_key) DO UPDATE SET checkpoint=EXCLUDED.checkpoint,commit_hash=EXCLUDED.commit_hash,updated_at=now()''',
                    (job_key,Jsonb(next_checkpoint),evidence_id))
                c.execute("UPDATE tester_storage.download_jobs SET state='committed',updated_at=now() WHERE job_key=%s",(job_key,))
                self._audit(c,e,'ingest',dict(job_key=job_key,evidence_id=evidence_id,count=count,verified=verified))
            return dict(count=count,verified=verified,evidence_id=evidence_id)
        except DataConflict:
            self.mark_range(e,'corrupt','conflicting source identity',code='identity_conflict')
            raise

    @staticmethod
    def _audit(c,e,action,detail):
        c.execute('INSERT INTO tester_storage.audit_events(event_id,scope_key,symbol,action,detail) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                  (digest([scope_key(e),action,detail]),scope_key(e),e['symbol'],action,Jsonb(detail)))

    @staticmethod
    def _coverage(c,e,state,evidence_id,reason,coverage_id=None):
        key=coverage_id or digest([scope_key(e),e['start_ms'],e['end_ms'],state,evidence_id,reason])
        c.execute('''INSERT INTO tester_storage.coverage_intervals(coverage_id,scope_key,symbol,start_ms,end_ms,state,evidence_id,reason)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (key,scope_key(e),e['symbol'],e['start_ms'],e['end_ms'],state,evidence_id,reason))
        return key

    def mark_range(self,evidence,state,reason,code='range_damage'):
        if state not in ('corrupt','unknown','unavailable'): raise ValueError('invalid finding state')
        e,_=validate_evidence(evidence); scope=scope_key(e)
        # Each new incident is distinct; repeated calls for the same incident can pass incident_id.
        finding_id=digest([scope,e['start_ms'],e['end_ms'],state,reason,e.get('incident_id',digest(e))])
        with self.connect() as c:
            self._lock(c,scope)
            self._coverage(c,e,state,None,reason,finding_id)
            c.execute('''INSERT INTO tester_storage.quality_findings(finding_id,scope_key,symbol,start_ms,end_ms,code,detail)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (finding_id,scope,e['symbol'],e['start_ms'],e['end_ms'],code,Jsonb(dict(reason=reason,state=state))))
            self._audit(c,e,'finding',dict(finding_id=finding_id,code=code))
        return finding_id

    def checkpoint(self,job_key):
        with self.connect() as c:
            row=c.execute('SELECT checkpoint FROM tester_storage.ingestion_checkpoints WHERE job_key=%s',(job_key,)).fetchone()
            return row['checkpoint'] if row else None

    def read(self,dataset,venue,category,symbol,start_ms,end_ms,interval='',limit=1000,after=None):
        if dataset not in DATASETS or type(limit) is not int or not 1<=limit<=10000: raise ValueError('invalid dataset/limit')
        integer(start_ms,'start_ms'); integer(end_ms,'end_ms')
        if end_ms<=start_ms: raise ValueError('empty read range')
        params=[venue,category,symbol,dataset,interval,start_ms,end_ms]
        query='''SELECT r.*, (SELECT source FROM tester_storage.record_sources s WHERE s.record_key=r.record_key ORDER BY source_id LIMIT 1) AS source
            FROM tester_storage.raw_records r WHERE venue=%s AND category=%s AND symbol=%s AND dataset=%s AND interval=%s
            AND timestamp_ms>=%s AND timestamp_ms<%s'''
        if after is not None:
            if not isinstance(after,(list,tuple)) or len(after)!=2: raise ValueError('cursor requires (timestamp_ms,record_key)')
            query+=' AND (timestamp_ms,record_key)>(%s,%s)'; params.extend(after)
        query+=' ORDER BY timestamp_ms,record_key LIMIT %s'; params.append(limit)
        with self.connect() as c:
            rows=c.execute(query,params).fetchall()
        for row in rows:
            row.pop('inserted_at',None)
            for k,v in row.items():
                if isinstance(v,Decimal): row[k]=str(v)
        return rows

    def coverage(self,venue,category,symbol,dataset,interval,start_ms,end_ms):
        e=dict(venue=venue,category=category,symbol=symbol,dataset=dataset,interval=interval); key=scope_key(e)
        with self.connect() as c:
            rows=c.execute('''SELECT start_ms AS start,end_ms AS end,state FROM tester_storage.coverage_intervals i
                WHERE scope_key=%s AND start_ms<%s AND end_ms>%s AND NOT EXISTS
                (SELECT 1 FROM tester_storage.coverage_resolutions r WHERE r.coverage_id=i.coverage_id)''',(key,end_ms,start_ms)).fetchall()
            findings=c.execute('''SELECT start_ms AS start,end_ms AS end,'corrupt' AS state FROM tester_storage.quality_findings f
                WHERE scope_key=%s AND start_ms<%s AND end_ms>%s AND NOT EXISTS
                (SELECT 1 FROM tester_storage.finding_resolutions r WHERE r.finding_id=f.finding_id)''',(key,end_ms,start_ms)).fetchall()
            archives=c.execute('''SELECT a.* FROM tester_storage.source_archives a WHERE scope_key=%s AND start_ms<%s AND end_ms>%s
                AND EXISTS (SELECT 1 FROM tester_storage.pagination_evidence p WHERE p.scope_key=a.scope_key AND p.verified
                    AND p.evidence->'available_range'->>'sha256'=a.sha256)''',(key,end_ms,start_ms)).fetchall()
            for archive in archives:
                if not verify_archive(archive): findings.append(dict(start=archive['start_ms'],end=archive['end_ms'],state='corrupt'))
        return coverage_report(start_ms,end_ms,rows+findings)

    def recovery_jobs(self,venue,category,symbol,dataset,interval=''):
        e=dict(venue=venue,category=category,symbol=symbol,dataset=dataset,interval=interval); scope=scope_key(e)
        with self.connect() as c:
            self._lock(c,scope)
            findings=c.execute('''SELECT * FROM tester_storage.quality_findings f WHERE scope_key=%s AND NOT EXISTS
                (SELECT 1 FROM tester_storage.finding_resolutions r WHERE r.finding_id=f.finding_id) ORDER BY start_ms''',(scope,)).fetchall()
            result=[]
            for f in findings:
                if f['detail'].get('state')=='unavailable': continue
                request=e|dict(start_ms=f['start_ms'],end_ms=f['end_ms']); key='recovery:'+f['finding_id']
                c.execute('''INSERT INTO tester_storage.download_jobs(job_key,scope_key,symbol,request,state)
                    VALUES(%s,%s,%s,%s,'retry') ON CONFLICT DO NOTHING''',(key,scope,symbol,Jsonb(request)))
                result.append(dict(job_key=key,request=request,resolves=[f['finding_id']]))
            return result

    def save_instrument(self,venue,category,symbol,effective_ms,metadata,source):
        from .models import utc
        integer(effective_ms,'effective_ms'); canonical(metadata); source=copy.deepcopy(source)
        source['fetched_at']=utc(source.get('fetched_at'))
        if not source.get('url'): raise ValueError('metadata provenance URL required')
        revision=digest([effective_ms,metadata,source])
        with self.connect() as c:
            self._lock(c,digest([venue,category,symbol,'metadata']))
            c.execute('INSERT INTO tester_storage.instruments(venue,category,symbol) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(venue,category,symbol))
            c.execute('''INSERT INTO tester_storage.instrument_metadata(venue,category,symbol,revision,effective_ms,metadata,provenance)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',(venue,category,symbol,revision,effective_ms,Jsonb(metadata),Jsonb(source)))
        return revision

    def register_archive(self,evidence,manifest):
        e,_=validate_evidence(evidence)
        if not verify_archive(manifest): raise ValueError('archive integrity verification failed')
        integer(manifest.get('row_count'),'row_count')
        if not manifest.get('url'): raise ValueError('archive source URL required')
        archive_id=digest([scope_key(e),e['start_ms'],e['end_ms'],manifest])
        with self.connect() as c:
            self._lock(c,scope_key(e))
            c.execute('''INSERT INTO tester_storage.source_archives(archive_id,scope_key,symbol,start_ms,end_ms,url,sha256,bytes,row_count,format,storage_uri,manifest)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (archive_id,scope_key(e),e['symbol'],e['start_ms'],e['end_ms'],manifest['url'],manifest['sha256'],manifest['bytes'],
                 manifest['row_count'],manifest['format'],manifest['storage_uri'],Jsonb(manifest)))
            self._audit(c,e,'archive',dict(archive_id=archive_id,sha256=manifest['sha256']))
        return archive_id

    def archive_overlaps(self,evidence):
        e,_=validate_evidence(evidence)
        with self.connect() as c:
            return c.execute('''SELECT archive_id,start_ms,end_ms,sha256,url FROM tester_storage.source_archives
                WHERE scope_key=%s AND start_ms<%s AND end_ms>%s ORDER BY start_ms,archive_id''',
                (scope_key(e),e['end_ms'],e['start_ms'])).fetchall()

    @staticmethod
    def _raw_summary(c,e):
        if e['dataset']!='trades': raise ValueError('aggregate verification currently supports trade-derived OHLCV')
        args=[e['venue'],e['category'],e['symbol'],e['start_ms'],e['end_ms']]
        where="venue=%s AND category=%s AND symbol=%s AND dataset='trades' AND timestamp_ms>=%s AND timestamp_ms<%s"
        rows=c.execute('SELECT record_key,content_hash FROM tester_storage.raw_records WHERE '+where+' ORDER BY record_key',args)
        h=hashlib.sha256()
        for r in rows: h.update((r['record_key']+r['content_hash']).encode('ascii'))
        summary=c.execute('''SELECT count(*) AS trade_count,coalesce(sum(size),0) AS volume,max(price) AS high,min(price) AS low
            FROM tester_storage.raw_records WHERE '''+where,args).fetchone()
        order='timestamp_ms,coalesce(mod(timestamp_ns,1000000),mod(timestamp_us,1000)*1000,0),trade_id'
        ordered=c.execute('SELECT price FROM tester_storage.raw_records WHERE '+where+' ORDER BY '+order,args).fetchall()
        summary['open']=ordered[0]['price'] if ordered else None
        summary['close']=ordered[-1]['price'] if ordered else None
        return h.hexdigest(),{k:str(v) if isinstance(v,Decimal) else v for k,v in summary.items()}

    def save_aggregate(self,evidence,payload,algorithm_version):
        e,_=validate_evidence(evidence); canonical(payload)
        if not isinstance(algorithm_version,str) or not algorithm_version: raise ValueError('algorithm version required')
        with self.connect() as c:
            self._lock(c,scope_key(e))
            fingerprint,_=self._raw_summary(c,e)
            key=digest([scope_key(e),e['start_ms'],e['end_ms'],algorithm_version,fingerprint,payload])
            c.execute('''INSERT INTO tester_storage.derived_aggregates(aggregate_id,scope_key,symbol,start_ms,end_ms,algorithm_version,source_fingerprint,payload)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (key,scope_key(e),e['symbol'],e['start_ms'],e['end_ms'],algorithm_version,fingerprint,Jsonb(dict(data=payload,scope={k:e[k] for k in SCOPE}))))
            self._audit(c,e,'aggregate',dict(aggregate_id=key,source_fingerprint=fingerprint))
        return key

    def check_aggregate(self,aggregate_id):
        with self.connect() as c:
            row=c.execute('SELECT * FROM tester_storage.derived_aggregates WHERE aggregate_id=%s',(aggregate_id,)).fetchone()
            if not row: raise ValueError('unknown aggregate')
            e=row['payload']['scope']|dict(start_ms=row['start_ms'],end_ms=row['end_ms'])
            self._lock(c,scope_key(e))
            fingerprint,actual=self._raw_summary(c,e)
            expected=row['payload']['data']; matches=True
            for k,v in actual.items():
                if k not in expected: matches=False; continue
                if v is None: matches &= expected[k] is None
                else:
                    from .models import decimal
                    matches &= Decimal(decimal(expected[k],k))==Decimal(v)
            stale=fingerprint!=row['source_fingerprint']
        if stale or not matches:
            self.mark_range(e|dict(status='UNVERIFIED',pages=[dict(params={},cursor=None,next_cursor=None,count=0)]),
                            'unknown','aggregate source changed or OHLCV mismatch',code='aggregate_mismatch')
        return dict(matches=bool(matches and not stale),stale=stale,actual=actual,source_fingerprint=fingerprint)

    def recover(self,fetcher,venue,category,symbol,dataset,interval='',max_jobs=10,retry_delay_ms=30000):
        """Run bounded retry work through an injected public-data downloader.

        fetcher(job) -> (record iterable, pagination evidence, checkpoint).
        Network I/O runs before the atomic publication transaction. Failures retain prior
        confirmed records and schedule another attempt; only fresh verified data resolves findings.
        """
        integer(max_jobs,'max_jobs'); integer(retry_delay_ms,'retry_delay_ms')
        results=[]
        due=[]; now=time.time_ns()//1000000
        for job in self.recovery_jobs(venue,category,symbol,dataset,interval):
            with self.connect() as c:
                schedule=c.execute('SELECT next_attempt_ms,attempt_count FROM tester_storage.download_jobs WHERE job_key=%s',(job['job_key'],)).fetchone()
            if schedule['next_attempt_ms']<=now: due.append((schedule['next_attempt_ms'],schedule['attempt_count'],job['job_key'],job))
        for _,_,_,job in sorted(due,key=lambda item:item[:3])[:max_jobs]:
            key=job['job_key']; now=time.time_ns()//1000000
            with self.connect() as c:
                row=c.execute('SELECT next_attempt_ms FROM tester_storage.download_jobs WHERE job_key=%s',(key,)).fetchone()
                if row['next_attempt_ms']>now: continue
                c.execute('UPDATE tester_storage.download_jobs SET attempt_count=attempt_count+1 WHERE job_key=%s',(key,))
            try:
                records,evidence,checkpoint=fetcher(copy.deepcopy(job))
                e=copy.deepcopy(evidence); e['resolves']=job['resolves']
                result=self.ingest(records,e,key,checkpoint)
                if not result['verified']: raise ValueError('recovery did not verify range')
                results.append(dict(job_key=key,state='verified'))
            except Exception as exc:
                # Exception text can include URLs/credentials; persist only a safe class code.
                code=type(exc).__name__
                with self.connect() as c:
                    c.execute("UPDATE tester_storage.download_jobs SET state='retry',next_attempt_ms=%s,last_error_code=%s,updated_at=now() WHERE job_key=%s",
                              (time.time_ns()//1000000+retry_delay_ms,code,key))
                    self._audit(c,job['request'],'retry',dict(job_key=key,error_code=code,attempt_ms=now))
                results.append(dict(job_key=key,state='retry',error_code=code))
        return results
