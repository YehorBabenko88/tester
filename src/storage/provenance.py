"""Authentic write-time provenance resolution. Reads never manufacture ingestion runs."""
from .models import digest


def resolve(conn,record_keys):
    keys=list(dict.fromkeys(record_keys))
    if len(keys)>10000: raise ValueError('provenance query limited to 10000 record keys')
    result={key:dict(observations=[],evidence_refs=[],run_ids=[],unlinked_sources=[],authentic=False) for key in keys}
    if not keys: return result
    rows=conn.execute('''SELECT s.record_key,s.source_id,s.source,s.raw_envelope,
        o.observation_id,o.run_id,o.job_key,o.evidence_id,o.source_kind,o.timestamp_precision,
        r.run_origin,p.status AS pagination_status,p.verified,p.evidence,p.validator_version
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
        observation['authentic_run']=row['run_origin']=='caller'
        item['observations'].append(observation)
        item['evidence_refs'].append(row['evidence_id']); item['run_ids'].append(row['run_id'])
    for item in result.values():
        item['evidence_refs']=sorted(set(item['evidence_refs'])); item['run_ids']=sorted(set(item['run_ids']))
        item['authentic']=bool(item['observations'] and not item['unlinked_sources'] and
                               all(o['authentic_run'] and o['source_kind']!='unknown' for o in item['observations']))
    return result
