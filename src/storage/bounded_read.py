"""Resource-bounded record read with original evidence documents (issue #104).

Every budget (records, payload/source bytes, observation fan-out per record and
in total, distinct evidence documents and their bytes, wall-clock time) is
checked by SQL aggregates inside one READ ONLY REPEATABLE READ transaction
BEFORE any payload, source or evidence document is transferred. Exceeding a
budget raises ReadBudgetExceeded; nothing is partially returned. Reads never
prove coverage. Requires a dict_row connection (Store.connect).
"""
import time
from decimal import Decimal

from .models import DATASETS, integer
from .provenance import resolve

BUDGET_KEYS=('max_row_bytes','max_source_bytes','max_observations_per_record',
             'max_observations','max_evidence_documents','max_evidence_bytes','timeout_ms')


class ReadBudgetExceeded(ValueError):
    """A bounded read refused before transferring documents; .budget names the limit."""
    reason='read_budget_exceeded'

    def __init__(self,budget):
        super().__init__('read budget exceeded: '+budget)
        self.budget=budget


def _validate(budget):
    if not isinstance(budget,dict) or set(budget)!=set(BUDGET_KEYS):
        raise ValueError('explicit complete read budget required')
    for key in BUDGET_KEYS:
        if type(budget[key]) is not int or not 1<=budget[key]<=1<<40: raise ValueError('invalid read budget: '+key)
    return dict(budget)


def read_bounded(conn,dataset,venue,category,symbol,start_ms,end_ms,interval='',*,limit,budget):
    if dataset not in DATASETS or type(limit) is not int or not 1<=limit<=10000: raise ValueError('invalid dataset/limit')
    integer(start_ms,'start_ms'); integer(end_ms,'end_ms')
    if end_ms<=start_ms: raise ValueError('empty read range')
    budget=_validate(budget)
    deadline=time.monotonic()+budget['timeout_ms']/1000

    def run(query,params):
        remaining=int((deadline-time.monotonic())*1000)
        if remaining<=0: raise ReadBudgetExceeded('timeout_ms')
        conn.execute("SELECT set_config('statement_timeout',%s,true)",(str(remaining),))
        return conn.execute(query,params)

    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    scope=[venue,category,symbol,dataset,interval,start_ms,end_ms,limit]
    usage=run('''WITH page AS (
            SELECT record_key,octet_length(payload::text) AS bytes FROM tester_storage.raw_records
            WHERE venue=%s AND category=%s AND symbol=%s AND dataset=%s AND interval=%s
              AND timestamp_ms>=%s AND timestamp_ms<%s ORDER BY timestamp_ms,record_key LIMIT %s),
        src AS (SELECT s.record_key,s.source_id,octet_length(s.source::text) AS bytes
            FROM tester_storage.record_sources s JOIN page USING(record_key)),
        obs AS (SELECT src.record_key,o.evidence_id FROM src
            JOIN tester_storage.source_observations o ON o.source_id=src.source_id)
        SELECT (SELECT coalesce(array_agg(record_key),'{}') FROM page) AS keys,
            (SELECT coalesce(sum(bytes),0) FROM page) AS row_bytes,
            (SELECT coalesce(sum(bytes),0) FROM src) AS source_bytes,
            (SELECT count(*) FROM src) AS sources,
            (SELECT count(*) FROM obs) AS observations,
            (SELECT coalesce(max(n),0) FROM (SELECT count(*) AS n FROM src GROUP BY record_key) x) AS max_record_sources,
            (SELECT coalesce(max(n),0) FROM (SELECT count(*) AS n FROM obs GROUP BY record_key) x) AS max_record_observations,
            (SELECT count(DISTINCT evidence_id) FROM obs) AS evidence_documents,
            (SELECT coalesce(sum(octet_length(p.evidence::text)),0) FROM tester_storage.pagination_evidence p
                WHERE p.evidence_id IN (SELECT evidence_id FROM obs)) AS evidence_bytes''',scope).fetchone()
    checks=(('max_row_bytes',usage['row_bytes']),('max_source_bytes',usage['source_bytes']),
            ('max_observations_per_record',max(usage['max_record_observations'],usage['max_record_sources'])),
            ('max_observations',max(usage['observations'],usage['sources'])),
            ('max_evidence_documents',usage['evidence_documents']),('max_evidence_bytes',usage['evidence_bytes']))
    for key,used in checks:
        if used>budget[key]: raise ReadBudgetExceeded(key)
    keys=list(usage['keys'])
    if not keys: return []
    rows=run('''SELECT r.*, (SELECT source FROM tester_storage.record_sources s WHERE s.record_key=r.record_key ORDER BY source_id LIMIT 1) AS source
        FROM tester_storage.raw_records r WHERE r.record_key=ANY(%s) ORDER BY timestamp_ms,record_key''',(keys,)).fetchall()
    remaining=int((deadline-time.monotonic())*1000)
    if remaining<=0: raise ReadBudgetExceeded('timeout_ms')
    conn.execute("SELECT set_config('statement_timeout',%s,true)",(str(remaining),))
    provenance=resolve(conn,keys,include_documents=False)
    ids=sorted({o['evidence_id'] for item in provenance.values() for o in item['observations'] if o['evidence_id']})
    documents={r['evidence_id']:r['evidence'] for r in run('''SELECT evidence_id,evidence
        FROM tester_storage.pagination_evidence WHERE evidence_id=ANY(%s)''',(ids,)).fetchall()} if ids else {}
    if set(documents)!=set(ids): raise ValueError('evidence document missing from bounded snapshot')
    for row in rows:
        item=provenance[row['record_key']]
        for observation in item['observations']:
            observation['evidence']=documents.get(observation['evidence_id'])
        row['provenance']=item
        row['sources']=[o['source'] for o in item['observations']]
        row.pop('inserted_at',None)
        for k,v in row.items():
            if isinstance(v,Decimal): row[k]=str(v)
    return rows
