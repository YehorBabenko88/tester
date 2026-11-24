"""Authentic write-time provenance resolution. Reads never manufacture ingestion runs."""
from .models import digest


def source_run_status(source,run_id):
    """Compare an explicit original declaration without rewriting or inventing one."""
    if not isinstance(source,dict): return 'invalid'
    if 'ingestion_run' not in source: return 'undeclared'
    original=source['ingestion_run']
    if not isinstance(original,str) or not 1<=len(original)<=300: return 'invalid'
    return 'matched' if original==run_id else 'mismatched'


def resolve(conn,record_keys,*,include_documents=True):
    """Resolve original linkage; document omission is not a coverage proof.

    The default retains the legacy full documents. Compact callers keep every
    observation/run/evidence edge and authenticity decision, but do not transfer
    repeated raw envelopes or pagination proof bodies from PostgreSQL.
    """
    keys=list(dict.fromkeys(record_keys))
    if len(keys)>10000: raise ValueError('provenance query limited to 10000 record keys')
    result={key:dict(observations=[],evidence_refs=[],run_ids=[],unlinked_sources=[],authentic=False) for key in keys}
    if not keys: return result
    raw_document='s.raw_envelope' if include_documents else 'NULL::jsonb AS raw_envelope'
    evidence_document='p.evidence' if include_documents else 'NULL::jsonb AS evidence'
    rows=conn.execute(f'''SELECT s.record_key,s.source_id,s.source,{raw_document},
        o.observation_id,o.run_id,o.job_key,o.evidence_id,o.source_kind,o.timestamp_precision,
        r.run_origin,p.status AS pagination_status,p.verified,{evidence_document},p.validator_version
        FROM tester_storage.record_sources s
        LEFT JOIN tester_storage.source_observations o ON o.source_id=s.source_id
        LEFT JOIN tester_storage.ingestion_runs r ON r.run_id=o.run_id
        LEFT JOIN tester_storage.pagination_evidence p ON p.evidence_id=o.evidence_id
        WHERE s.record_key=ANY(%s) ORDER BY s.record_key,o.run_id,o.evidence_id,s.source_id''',(keys,)).fetchall()
    for row in rows:
        item=result[row['record_key']]
        if row['observation_id'] is None:
            item['unlinked_sources'].append(dict(source_id=row['source_id'],source=row['source'],reason='legacy_missing_run_evidence'))
            continue
        observation={k:row[k] for k in ('observation_id','source_id','source','raw_envelope','run_id','job_key',
            'evidence_id','source_kind','timestamp_precision','pagination_status','verified','evidence','validator_version')}
        status=source_run_status(row['source'],row['run_id'])
        observation['source_run_status']=status
        observation['source_ingestion_run']=row['source'].get('ingestion_run') if isinstance(row['source'],dict) else None
        observation['authentic_run']=row['run_origin']=='caller' and status in ('matched','undeclared')
        item['observations'].append(observation)
        item['evidence_refs'].append(row['evidence_id']); item['run_ids'].append(row['run_id'])
    for item in result.values():
        item['evidence_refs']=sorted(set(item['evidence_refs'])); item['run_ids']=sorted(set(item['run_ids']))
        item['authentic']=bool(item['observations'] and not item['unlinked_sources'] and
                               all(o['authentic_run'] and o['source_kind']!='unknown' for o in item['observations']))
    return result
