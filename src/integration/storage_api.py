"""B storage -> E contract adapter, with no optimistic quality defaults.

The caller must resolve authentic source-observation/run provenance. The current
B read API alone cannot provide that linkage; this adapter deliberately fails
closed until a resolver is supplied. It neither creates evidence nor analytics.
"""
import base64
import copy
import json

from contracts.v1.validate import ContractError, validate_record, validate_response


class IntegrationUnavailable(ValueError):
    code='DATA_UNAVAILABLE'


def project_trade(row, *, provenance, availability, coverage):
    if not provenance:
        raise IntegrationUnavailable('Original ingestion-run provenance is unavailable')
    try:
        if row['dataset']!='trades' or row['schema_version']!='1.0':
            raise IntegrationUnavailable('Unsupported storage record contract')
        precision=row['timestamp_precision'];ns=row.get('timestamp_ns')
        if precision=='us':
            us=row.get('timestamp_us')
            if type(us) is not int:
                raise IntegrationUnavailable('Original microsecond timestamp is unavailable')
            ns=str(us*1000)
        elif ns is not None:
            if type(ns) is not int:
                raise IntegrationUnavailable('Stored nanoseconds must be an exact integer')
            ns=str(ns)
        payload=row['payload'];origin=row['id_origin']
        value={k:row[k] for k in ('schema_version','venue','category','symbol','timestamp_ms','trade_id')}
        value.update(timestamp_ns=ns,timestamp_precision=precision,id_origin=origin,
                     price=payload['price'],quantity=payload['size'],taker_side=payload['side'],
                     provenance=copy.deepcopy(provenance),availability=copy.deepcopy(availability),coverage=copy.deepcopy(coverage))
        if origin=='composite_v1':
            value['id_origin']='synthetic'
            value['identity_method']='B composite_v1: SHA256 source record key, exact timestamp, price, size and side; collision checks required'
        validate_record('trade',value)
        return value
    except (KeyError,TypeError,ContractError) as error:
        raise IntegrationUnavailable('Storage trade lacks valid canonical metadata or exact values') from error


def _quality(report,start,end):
    refs=report.get('evidence_refs',[])
    if report.get('start')!=start or report.get('end')!=end:
        raise IntegrationUnavailable('Storage coverage boundaries do not match request')
    if report.get('corrupt') or report.get('unavailable'):
        state='UNAVAILABLE';status='ERROR';reason=report.get('reason') or 'Storage integrity/availability blocks requested range'
    elif report.get('complete') and refs:
        state='AVAILABLE';status='COMPLETE';reason=None
    elif report.get('verified') and not report.get('complete'):
        state='PARTIAL';status='PARTIAL';reason=report.get('reason') or 'Only part of requested history is verified'
    else:
        state='UNVERIFIED';status='UNKNOWN';reason='Authoritative coverage evidence references are unavailable'
    return dict(status=state,reason=reason),dict(status=status,start_ms=start,end_ms=end,evidence_refs=refs)


def _cursor(scope,position):
    data=json.dumps(dict(scope=scope,after=position),separators=(',',':')).encode()
    return base64.urlsafe_b64encode(data).decode()


def _decode_cursor(token,scope):
    try:
        if not isinstance(token,str) or len(token)>4096:raise ValueError('Invalid cursor')
        value=json.loads(base64.b64decode(token,altchars=b'-_',validate=True))
        position=value['after']
        if value['scope']!=scope or not isinstance(position,list) or len(position)!=2:
            raise ValueError('Cursor scope or shape mismatch')
        if type(position[0]) is not int or not isinstance(position[1],str):raise ValueError('Invalid position')
        return position
    except (ValueError,KeyError,TypeError) as error:
        raise ValueError('Invalid or cross-scope continuation cursor') from error


def read_trades(store, *, venue,category,symbol,start_ms,end_ms,limit,provenance_resolver,after=None):
    """Bounded keyset page; source resolver must return all authentic observations.

    Internal overfetch is one row; public response has at most 1000 records.
    No source run is generated here, and storage percentage is never quality proof.
    """
    if type(limit) is not int or not 1<=limit<=1000:raise ValueError('limit must be 1..1000')
    if type(start_ms) is not int or type(end_ms) is not int or not 0<=start_ms<end_ms:
        raise ValueError('Invalid half-open UTC range')
    scope=[venue,category,symbol,start_ms,end_ms]
    position=None if after is None else _decode_cursor(after,scope)
    if position is not None and not start_ms<=position[0]<end_ms:
        raise ValueError('Continuation position is outside request range')
    rows=store.read('trades',venue,category,symbol,start_ms,end_ms,interval='',limit=limit+1,after=position)
    previous=tuple(position) if position is not None else None
    for row in rows:
        ts=row.get('timestamp_ms');key=row.get('record_key')
        if type(ts) is not int or not start_ms<=ts<end_ms or not isinstance(key,str) or not key:
            raise IntegrationUnavailable('Storage page has an invalid key or timestamp boundary')
        if previous is not None and (ts,key)<=previous:
            raise IntegrationUnavailable('Storage page keyset is not strictly increasing')
        previous=(ts,key)
    more=len(rows)>limit;rows=rows[:limit]
    report=store.coverage(venue,category,symbol,'trades','',start_ms,end_ms)
    availability,coverage=_quality(report,start_ms,end_ms)
    if not rows and availability['status'] not in ('UNAVAILABLE','PARTIAL'):
        availability={'status':'UNAVAILABLE','reason':'No records on this page; empty results do not prove completeness'}
        coverage.update(status='UNKNOWN')
    projected=[];sources=[];seen_sources=set()
    for row in rows:
        if [row.get(k) for k in ('venue','category','symbol')]!=scope[:3]:
            raise IntegrationUnavailable('Storage returned a different instrument identity')
        provenance=provenance_resolver(row['record_key'])
        projected.append(project_trade(row,provenance=provenance,availability=availability,coverage=coverage))
        for source in provenance:
            key=json.dumps(source,sort_keys=True)
            if key not in seen_sources:sources.append(copy.deepcopy(source));seen_sources.add(key)
    next_cursor=_cursor(scope,[rows[-1]['timestamp_ms'],rows[-1]['record_key']]) if more else None
    value=dict(schema_version='1.0',data=projected,next_cursor=next_cursor,
               availability=availability,coverage=coverage,provenance=sources)
    validate_response('trade',value)
    return value

