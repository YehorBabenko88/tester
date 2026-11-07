"""Typed callable E/G port. Authentication and durable scheduling belong to E/G.

Tenant and approval must come from trusted local authentication/capability policy.
This module does not expose a network server or accept natural-language commands.
"""
from dataclasses import asdict
from .core import analyze, select_events
from .imports import digest, SOURCE_REGISTRY
from .reconstruction import reconstruct

ENGINE_VERSION='h-research-2'
QUERY={'dataset_revision','event_id','source','trader_id','venue','category','symbol',
       'start_ms','end_ms','as_of_ms'}


def text_id(value):
    if not isinstance(value,str) or not 1<=len(value)<=256: raise ValueError('invalid identifier')
    return value


def selected(dataset,q):
    if not isinstance(q,dict) or set(q)!=QUERY: raise ValueError('typed research query required')
    for k in QUERY-{'start_ms','end_ms','as_of_ms'}: text_id(q[k])
    if type(q['as_of_ms']) is not int or q['as_of_ms']<0: raise ValueError('as_of required')
    events=select_events(dataset['events'],start_ms=q['start_ms'],end_ms=q['end_ms'],
                         trader_id=q['trader_id'],as_of_ms=q['as_of_ms'])
    return [asdict(e) for e in events if (e.source,e.venue,e.category,e.symbol)==
            (q['source'],q['venue'],q['category'],q['symbol'])]


class Research:
    def __init__(self,store): self.store=store

    def sources(self): return [dict(x) for x in SOURCE_REGISTRY]

    def plan(self,tenant,query):
        with self.store.connection() as c:
            d=self.store.snapshot(c,text_id(tenant),text_id(query.get('dataset_revision')))
            rows=selected(d,query)
            if len(rows)>2000: raise ValueError('analysis budget: narrow range to at most 2000 events')
            if not any(r['event_id']==query['event_id'] and r['kind']=='DISCLOSED_EXECUTION' for r in rows):
                raise LookupError('selected disclosed event not available as-of')
            p={'schema_version':'1.0','action':'trader.research','query':dict(query),
               'engine_version':ENGINE_VERSION,'source_revision':d['manifest']['source_revision'],
               'evidence_sha256':d['evidence_sha256'],'classification':'INFERRED',
               'operations':['reconcile_disclosed_executions','describe_unknowns','produce_overlays'],
               'simulation':'REQUIRES_SEPARATE_F_DSL_APPROVAL'}
            return dict(p,plan_digest=digest(p))

    def events(self,tenant,query,*,limit=100,cursor=None):
        if type(limit) is not int or not 1<=limit<=500: raise ValueError('page limit')
        fingerprint=digest(query)
        offset=0
        if cursor is not None:
            if not isinstance(cursor,dict) or set(cursor)!={'query_digest','offset'} or cursor['query_digest']!=fingerprint:
                raise ValueError('cursor query mismatch')
            offset=cursor['offset']
            if type(offset) is not int or offset<0: raise ValueError('cursor offset')
        with self.store.connection() as c:
            d=self.store.snapshot(c,text_id(tenant),text_id(query.get('dataset_revision')))
            rows=selected(d,query)
            if offset>len(rows): raise ValueError('cursor past end')
            end=offset+limit
            return {'schema_version':'1.0','data':rows[offset:end],
                    'next_cursor':{'query_digest':fingerprint,'offset':end} if end<len(rows) else None,
                    'dataset_revision':query['dataset_revision'],'coverage':d['manifest']['coverage'],
                    'coverage_basis':'EXPORTER_ATTESTED','availability':'AVAILABLE'}

    def run(self,tenant,request_id,plan,*,approved_digest):
        text_id(tenant);text_id(request_id)
        if not isinstance(plan,dict) or approved_digest!=plan.get('plan_digest'): raise PermissionError('plan approval required')
        expected=self.plan(tenant,plan['query'])
        if plan!=expected: raise ValueError('plan stale or modified')
        request_digest=digest(plan)
        with self.store.connection() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(tenant+':'+request_id,))
            d=self.store.snapshot(c,tenant,plan['query']['dataset_revision'])
            prior=c.execute('SELECT request_digest,payload FROM tester_trader.analysis_runs WHERE tenant=%s AND request_id=%s',
                            (tenant,request_id)).fetchone()
            if prior:
                if prior[0]!=request_digest: raise ValueError('request_id payload conflict')
                return prior[1]
            q=plan['query']; rows=selected(d,q)
            a=analyze(rows,start_ms=q['start_ms'],end_ms=q['end_ms'],trader_id=q['trader_id'],as_of_ms=q['as_of_ms'])
            m=dict(d['manifest'])
            # Clipping the inventory prefix or any delayed record invalidates completeness.
            original=[r for r in d['events'] if (r['source'],r['trader_id'],r['venue'],r['category'],r['symbol'])==
                      (q['source'],q['trader_id'],q['venue'],q['category'],q['symbol'])]
            if any(r['timestamp_ms']<q['start_ms'] or r['observed_at_ms']>q['as_of_ms'] for r in original):
                m['coverage']='UNKNOWN'
            a.update(reconstruction=reconstruct(rows,m),dataset_revision=q['dataset_revision'],
                     source_revision=m['source_revision'],evidence_sha256=d['evidence_sha256'],
                     engine_version=ENGINE_VERSION,request_id=request_id,plan_digest=plan['plan_digest'],
                     coverage=m['coverage'],coverage_basis='EXPORTER_ATTESTED',synthetic=m['synthetic'])
            a['analysis_run_id']=digest({'tenant':tenant,'request_id':request_id,'request_digest':request_digest})
            a['artifact_digest']=digest(a)
            self.store.save_run(c,tenant,request_id,request_digest,q['dataset_revision'],a)
            return a

    def read_run(self,tenant,request_id):
        with self.store.connection() as c:
            row=c.execute('SELECT dataset_revision,payload FROM tester_trader.analysis_runs WHERE tenant=%s AND request_id=%s',
                          (text_id(tenant),text_id(request_id))).fetchone()
            if not row: raise LookupError('run not found')
            self.store.snapshot(c,tenant,row[0])
            return row[1]

    def ack(self,tenant,request_id,artifact_digest,renderer_revision):
        text_id(renderer_revision)
        with self.store.connection() as c:
            row=c.execute('SELECT dataset_revision,payload FROM tester_trader.analysis_runs WHERE tenant=%s AND request_id=%s',
                          (text_id(tenant),text_id(request_id))).fetchone()
            if not row: raise LookupError('run not found')
            self.store.snapshot(c,tenant,row[0])
            if row[1]['artifact_digest']!=artifact_digest: raise ValueError('artifact digest mismatch')
            c.execute('INSERT INTO tester_trader.chart_acks VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                      (tenant,request_id,artifact_digest,renderer_revision))
            return {'status':'RENDERED','artifact_digest':artifact_digest,'renderer_revision':renderer_revision,
                    'verification':'NOT_INDEPENDENTLY_VERIFIED'}
