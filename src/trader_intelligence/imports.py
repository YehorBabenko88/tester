"""Explicit local export admission. No network, account keys or UI scraping."""
import csv
import io
import json
from dataclasses import asdict
from hashlib import sha256
from .core import Event, MAX_EVENTS, normalize

MAX_BYTES = 8 * 1024 * 1024
FIELDS = set(Event.__dataclass_fields__)
MANIFEST = {'source','source_revision','permission','rights_ref','coverage','flat_start',
            'position_mode','instrument_model','synthetic'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def _pairs(pairs):
    result = {}
    for k,v in pairs:
        if k in result: raise ValueError('duplicate JSON key')
        result[k] = v
    return result


def validate_manifest(m):
    if not isinstance(m,dict) or set(m)!=MANIFEST: raise ValueError('manifest fields required')
    if m['permission']!='USER_AUTHORIZED': raise ValueError('source not admitted')
    for k in ('source','source_revision','rights_ref'):
        if not isinstance(m[k],str) or not 1<=len(m[k])<=256: raise ValueError('invalid '+k)
    if m['coverage'] not in ('COMPLETE','PARTIAL','UNKNOWN'): raise ValueError('coverage')
    if type(m['flat_start']) is not bool or type(m['synthetic']) is not bool: raise ValueError('manifest boolean')
    if m['position_mode'] not in ('ONE_WAY','HEDGE','UNKNOWN'): raise ValueError('position mode')
    if m['instrument_model'] not in ('LINEAR','INVERSE','UNKNOWN'): raise ValueError('instrument model')


def import_bytes(raw, format, manifest, *, max_bytes=MAX_BYTES):
    validate_manifest(manifest)
    if type(raw) is not bytes or not 0<len(raw)<=min(max_bytes,MAX_BYTES): raise ValueError('file size limit')
    try:
        text=raw.decode('utf-8-sig')
        if format=='json':
            rows=json.loads(text,object_pairs_hook=_pairs)
            if not isinstance(rows,list): raise ValueError('JSON array required')
        elif format=='csv':
            reader=csv.DictReader(io.StringIO(text,newline=''))
            if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames): raise ValueError('CSV header')
            rows=reader
        else: raise ValueError('unsupported local export format')
        events={}
        for idx,row in enumerate(rows):
            if idx>=MAX_EVENTS: raise ValueError('row limit')
            if not isinstance(row,dict) or set(row)!=FIELDS: raise ValueError('canonical export fields required; private/unknown fields forbidden')
            row=dict(row)
            if format=='csv':
                for k in ('timestamp_ms','observed_at_ms'): row[k]=int(row[k])
                if row['trader_id']=='': row['trader_id']=None
            event=normalize(row)
            if event.source!=manifest['source']: raise ValueError('source scope mismatch')
            if event.identity in events and events[event.identity]!=row: raise ValueError('conflicting event')
            events[event.identity]=asdict(event)
    except (UnicodeError, json.JSONDecodeError,csv.Error,RecursionError,TypeError) as exc:
        raise ValueError('malformed export') from exc
    if not events: raise ValueError('empty export')
    ordered=sorted(events.values(),key=canonical)
    evidence=sha256(raw).hexdigest()
    result={'manifest':dict(manifest),'events':ordered,'evidence_sha256':evidence}
    result['dataset_revision']=digest(result)
    return result


def validate_dataset(dataset):
    import re
    if not isinstance(dataset,dict) or set(dataset)!={'manifest','events','evidence_sha256','dataset_revision'}:
        raise ValueError('dataset shape')
    validate_manifest(dataset['manifest'])
    if not isinstance(dataset['events'],list) or not 1<=len(dataset['events'])<=MAX_EVENTS: raise ValueError('dataset row limit')
    if len(canonical(dataset).encode())>MAX_BYTES*2: raise ValueError('dataset byte limit')
    for k in ('evidence_sha256','dataset_revision'):
        if not isinstance(dataset[k],str) or not re.fullmatch('[0-9a-f]{64}',dataset[k]): raise ValueError('dataset hash')
    seen=set()
    for row in dataset['events']:
        if not isinstance(row,dict) or set(row)!=FIELDS: raise ValueError('canonical event fields required')
        e=normalize(row)
        if e.source!=dataset['manifest']['source'] or e.identity in seen: raise ValueError('event scope or duplicate')
        seen.add(e.identity)
    if digest({k:v for k,v in dataset.items() if k!='dataset_revision'})!=dataset['dataset_revision']:
        raise ValueError('dataset digest mismatch')


SOURCE_REGISTRY = (
    {'source':'authorized_export','category':'USER_EXPORT','adapter':'canonical_csv_json',
     'availability':'CONDITIONAL','permission':'USER_AUTHORIZED','history':'SUPPLIED_ROWS_ONLY'},
    {'source':'bybit_master_ui','category':'PUBLIC_TRADER','adapter':None,
     'availability':'UNAVAILABLE','reason':'AUTOMATION_RIGHTS_AND_HISTORY_API_UNCONFIRMED'},
    {'source':'binance_copy_ui','category':'PUBLIC_TRADER','adapter':None,
     'availability':'UNAVAILABLE','reason':'AUTOMATION_RIGHTS_AND_HISTORY_API_UNCONFIRMED'},
    {'source':'bybit_recent_trades','category':'ANONYMOUS_MARKET','adapter':None,
     'availability':'UNAVAILABLE','reason':'NOT_A_TRADER_IDENTITY_SOURCE'},
)
