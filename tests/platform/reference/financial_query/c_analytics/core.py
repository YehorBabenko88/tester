"""Canonical v1 trade normalization, UTC aggregation and source identity."""
import copy
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from .exact import decimal, number, total

VERSION = '1.0'
METHOD = 'tester-analytics/1.0'
INTERVALS = {'1','3','5','15','30','60','120','240','360','720','D','W','M'}


class SourceConflict(ValueError):
    """Same venue/category/symbol/trade ID has conflicting economic content."""


def unique(items):
    return [json.loads(x) for x in sorted({json.dumps(i, sort_keys=True) for i in items})]


def integer(value):
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError('Expected integer timestamp')
    if not re.fullmatch(r'[0-9]+', str(value)):
        raise ValueError('Expected unsigned integer timestamp')
    result = int(value)
    if result > 9007199254740991:
        raise ValueError('Timestamp exceeds canonical JSON range')
    return result


def validate_trade(t):
    required = {'schema_version','venue','category','symbol','timestamp_ms','timestamp_ns','timestamp_precision',
                'trade_id','id_origin','price','quantity','taker_side','provenance','availability','coverage'}
    if not isinstance(t,dict) or not required <= t.keys() or t.keys()-required-{'identity_method'}:
        raise ValueError('Noncanonical trade fields')
    if not isinstance(t['timestamp_ms'],int) or isinstance(t['timestamp_ms'],bool):
        raise ValueError('Canonical timestamp_ms must be an integer')
    for key in ['price','quantity']:
        if not isinstance(t[key],str) or not re.fullmatch(r'(?:0\.[0-9]*[1-9][0-9]*|[1-9][0-9]*(?:\.[0-9]+)?)',t[key]):
            raise ValueError('Canonical positive decimal string required')
    if not isinstance(t['trade_id'],str) or not t['trade_id']:
        raise ValueError('Canonical trade_id string required')
    if t.get('schema_version') != VERSION or t.get('venue') != 'bybit':
        raise ValueError('Unsupported schema or venue')
    if t.get('category') not in {'linear','inverse','spot'} or not isinstance(t.get('symbol'),str) or not re.fullmatch(r'[A-Z0-9-]{2,50}', t.get('symbol','')):
        raise ValueError('Invalid market identity')
    integer(t['timestamp_ms'])
    number(t['price'], positive=True); number(t['quantity'], positive=True)
    if t.get('taker_side') not in {'Buy','Sell','Unknown'} or not t.get('trade_id'):
        raise ValueError('Invalid side or identity')
    precision = t.get('timestamp_precision'); ns = t.get('timestamp_ns')
    if ns is not None and not isinstance(ns,str):
        raise ValueError('Canonical timestamp_ns must be a string')
    if precision not in {'s','ms','us','ns'}:
        raise ValueError('Unknown source precision')
    if precision in {'s','ms'} and ns is not None:
        raise ValueError('Fabricated sub-ms precision')
    if precision == 's' and t['timestamp_ms'] % 1000:
        raise ValueError('Fabricated milliseconds')
    if precision in {'us','ns'}:
        if ns is None or not re.fullmatch(r'[0-9]{1,19}', str(ns)) or int(ns)//1000000 != t['timestamp_ms']:
            raise ValueError('Missing/inconsistent source sub-ms timestamp')
        if precision == 'us' and int(ns)%1000:
            raise ValueError('Fabricated nanoseconds')
    if not isinstance(t.get('provenance'),list) or not t['provenance'] or not all(isinstance(p,dict) for p in t['provenance']) or any(p.get('timestamp_precision') != precision for p in t['provenance']):
        raise ValueError('Missing or inconsistent source provenance')
    for p in t['provenance']:
        required_source = {'source_kind','source_ref','source_hash','ingestion_run','timestamp_precision','source_timestamp'}
        if not required_source <= p.keys() or p.keys()-required_source-{'parent_refs','method_version'}:
            raise ValueError('Noncanonical source provenance')
        if p['source_hash'] is not None and (not isinstance(p['source_hash'],str) or not re.fullmatch('[a-f0-9]{64}',p['source_hash'])):
            raise ValueError('Invalid source hash')
        if p['source_timestamp'] is not None and not isinstance(p['source_timestamp'],str):
            raise ValueError('Source timestamp must preserve original string')
        for key in ['source_ref','ingestion_run']:
            if not isinstance(p[key],str) or not p[key]: raise ValueError('Provenance string required')
        if 'parent_refs' in p and (not isinstance(p['parent_refs'],list) or not all(isinstance(x,str) and x for x in p['parent_refs'])):
            raise ValueError('Invalid parent references')
        if 'method_version' in p and (not isinstance(p['method_version'],str) or not p['method_version']):
            raise ValueError('Invalid method version')
        if not p.get('source_ref') or not p.get('ingestion_run') or p.get('source_kind') not in {'bybit_rest','bybit_archive','synthetic_fixture'}:
            raise ValueError('Unidentified source')
    if t.get('id_origin') not in {'exchange','synthetic'}:
        raise ValueError('Invalid ID origin')
    if 'identity_method' in t and (not isinstance(t['identity_method'],str) or not t['identity_method']):
        raise ValueError('Invalid identity method')
    if t['id_origin'] == 'synthetic' and not t.get('identity_method'):
        raise ValueError('Synthetic identity requires documented method')
    availability = t.get('availability', {})
    coverage = t.get('coverage', {})
    if availability.keys() != {'status','reason'} or coverage.keys() != {'status','start_ms','end_ms','evidence_refs'}:
        raise ValueError('Noncanonical quality metadata')
    if availability['reason'] is not None and not isinstance(availability['reason'],str):
        raise ValueError('Availability reason must be string or null')
    if not all(isinstance(coverage[k],int) and not isinstance(coverage[k],bool) for k in ['start_ms','end_ms']):
        raise ValueError('Coverage boundaries must be canonical integers')
    if not isinstance(coverage['evidence_refs'],list) or not all(isinstance(x,str) and x for x in coverage['evidence_refs']):
        raise ValueError('Invalid evidence references')
    if availability.get('status') not in {'AVAILABLE','PARTIAL','UNAVAILABLE','UNVERIFIED'}:
        raise ValueError('Missing availability')
    if availability['status'] != 'AVAILABLE' and not availability.get('reason'):
        raise ValueError('Weaker availability needs reason')
    if coverage.get('status') not in {'NOT_STARTED','RUNNING','COMPLETE','PARTIAL','GAPS','EMPTY','PROVISIONAL','UNKNOWN','ERROR','UNAVAILABLE'}:
        raise ValueError('Missing coverage')
    if not integer(coverage['start_ms']) <= t['timestamp_ms'] < integer(coverage['end_ms']):
        raise ValueError('Trade outside source coverage range')
    if coverage['status'] == 'COMPLETE' and (not coverage.get('evidence_refs') or availability['status'] != 'AVAILABLE' or t['id_origin']=='synthetic'):
        raise ValueError('Unproved complete coverage')
    return t


def normalize_trade(raw, *, category, symbol, source_ref):
    """Normalize Bybit REST or documented archive rows; metadata is explicit.

    source_ref is a canonical provenance mapping (or a nonempty list of them).
    Archive timestamp units are declared by timestamp_precision. Decimal seconds
    in archive `timestamp` are preserved, never converted through float.
    """
    if raw.get('symbol',symbol) != symbol or raw.get('category',category) != category:
        raise ValueError('Source market identity mismatch')
    if 'schema_version' in raw:
        result = copy.deepcopy(raw)
        if result['category'] != category or result['symbol'] != symbol:
            raise ValueError('Market identity mismatch')
        return validate_trade(result)
    sources = copy.deepcopy(source_ref if isinstance(source_ref, list) else [source_ref])
    if not sources or not all(isinstance(p, dict) for p in sources):
        raise ValueError('source_ref must carry canonical provenance, not just an URL')
    precision = sources[0].get('timestamp_precision')
    raw_ts = raw.get('time', raw.get('timestamp'))
    if raw_ts is None:
        raise ValueError('Exchange timestamp required')
    if 'time' in raw:  # Bybit REST: always milliseconds
        if precision != 'ms':
            raise ValueError('REST time is documented in milliseconds')
        stamp = integer(raw_ts); ns = None
    else:  # Bybit public archive timestamp: decimal seconds
        seconds = number(raw_ts)
        scale = {'s':1,'ms':1000,'us':1000000,'ns':1000000000}.get(precision)
        if scale is None or seconds < 0 or (seconds*scale).denominator != 1:
            raise ValueError('Archive timestamp exceeds declared precision')
        stamp = integer((seconds*1000).__floor__())
        ns = str(int(seconds*1000000000)) if precision in {'us','ns'} else None
    for p in sources:
        p.setdefault('source_hash', None)
        p.setdefault('source_timestamp', str(raw_ts))
    price = decimal(number(raw['price'], positive=True))
    quantity = decimal(number(raw.get('size', raw.get('quantity')), positive=True))
    side = raw.get('side', raw.get('taker_side'))
    if any(p.get('source_kind')=='bybit_archive' for p in sources):
        # REST documents side as taker, but that is not proof for archive CSV.
        # A/B may pass an already verified canonical Trade without this mapping.
        side = 'Unknown'
    if side not in {'Buy','Sell'}: side = 'Unknown'
    original_id = raw.get('execId', raw.get('trdMatchID', raw.get('trade_id')))
    result = dict(schema_version=VERSION, venue='bybit', category=category,
        symbol=symbol, timestamp_ms=stamp, timestamp_ns=ns,
        timestamp_precision=precision, price=price, quantity=quantity,
        taker_side=side, provenance=sources,
        availability=copy.deepcopy(raw.get('availability', {'status':'UNVERIFIED','reason':'Source range not verified'})),
        coverage=copy.deepcopy(raw.get('coverage', {'status':'UNKNOWN','start_ms':stamp,'end_ms':stamp+1,'evidence_refs':[]})))
    if original_id is not None and str(original_id):
        result.update(trade_id=str(original_id), id_origin='exchange')
    else:
        payload = [category,symbol,stamp,ns,price,quantity,side]
        result.update(trade_id=hashlib.sha256(json.dumps(payload).encode()).hexdigest(),
            id_origin='synthetic', identity_method='sha256(category,symbol,timestamp_ms,timestamp_ns,price,quantity,taker_side); identical executions may collide')
        result['coverage']['status'] = 'UNKNOWN'
        result['availability'] = {'status':'UNVERIFIED','reason':'Composite identity cannot distinguish identical executions'}
    return validate_trade(result)


def identity(t):
    return t['venue'], t['category'], t['symbol']


def deduplicate(trades):
    by_id = {}
    for raw in trades:
        t = copy.deepcopy(validate_trade(raw))
        t['price'] = decimal(number(t['price']))
        t['quantity'] = decimal(number(t['quantity']))
        key = (*identity(t), t['trade_id'])
        economic = {k:v for k,v in t.items() if k not in {'provenance','coverage','availability','_observations'}}
        if key in by_id:
            prev = by_id[key]
            prev_economic = {k:v for k,v in prev.items() if k not in {'provenance','coverage','availability','_observations'}}
            if economic != prev_economic:
                raise SourceConflict('Conflicting source payload for '+repr(key))
            prev['provenance'] = unique(prev['provenance']+t['provenance'])
            # Repeated observations cannot promote source quality.
            prev['_observations'].append(t)
        else:
            t['_observations'] = [copy.deepcopy(t)]
            by_id[key] = t
    return sorted(by_id.values(), key=lambda t:(*identity(t), int(t['timestamp_ns']) if t['timestamp_ns'] is not None else t['timestamp_ms']*1000000, t['trade_id']))


def interval_bounds(timestamp_ms, interval):
    timestamp_ms = integer(timestamp_ms)
    if interval not in INTERVALS:
        raise ValueError('Unsupported official interval; use custom_candles')
    if interval.isdigit():
        width = int(interval)*60000
        start = timestamp_ms//width*width
        return start, start+width
    day = datetime.fromtimestamp(timestamp_ms//1000, timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    if interval == 'D': start, end = day, day+timedelta(days=1)
    elif interval == 'W':
        start = day-timedelta(days=day.weekday()); end = start+timedelta(days=7)
    else:
        start = day.replace(day=1)
        end = start.replace(year=start.year+1,month=1) if start.month == 12 else start.replace(month=start.month+1)
    return int(start.timestamp())*1000, int(end.timestamp())*1000


def custom_bounds(ts, interval):
    match = re.fullmatch(r'([1-9][0-9]*)(ms|s|m|h|d)', interval)
    if not match: raise ValueError('Custom interval must be positive integer ms/s/m/h/d')
    width = int(match[1])*{'ms':1,'s':1000,'m':60000,'h':3600000,'d':86400000}[match[2]]
    start = ts//width*width
    return start, start+width


def grouped(trades, interval, price_step, *, custom=False):
    number(price_step, positive=True)
    bounds = custom_bounds if custom else interval_bounds
    bounds(0, interval)  # validate even for empty input
    groups = defaultdict(list)
    for t in deduplicate(trades):
        start,end = bounds(t['timestamp_ms'], interval)
        if start < 0:
            raise ValueError('Calendar bucket begins before canonical epoch range')
        groups[(*identity(t),start,end)].append(t)
    return sorted(groups.items())


def metadata(rows, start, end, *, closed=True):
    """Conservative inherited evidence; a row count never proves completeness."""
    observations = [o for t in rows for o in t.get('_observations',[t])]
    sources = unique([p for t in rows for p in t['provenance']])
    parents = sorted({p['source_ref'] for p in sources})
    derived = dict(source_kind='derived',source_ref='analytics:'+hashlib.sha256(json.dumps([parents,start,end],sort_keys=True).encode()).hexdigest(),
        source_hash=None,ingestion_run='derived:'+METHOD,timestamp_precision='ms',source_timestamp=None,parent_refs=parents,method_version=METHOD)
    rank = {'AVAILABLE':0,'UNVERIFIED':1,'PARTIAL':2,'UNAVAILABLE':3}
    weakest = max((t['availability']['status'] for t in observations), key=rank.get)
    reasons = sorted({t['availability']['reason'] for t in observations if t['availability']['reason']})
    availability = {'status':weakest,'reason':'; '.join(reasons) or None}
    coverages = [t['coverage'] for t in observations]
    evidence = sorted({e for c in coverages for e in c['evidence_refs']})
    bad = [c['status'] for c in coverages if c['status'] != 'COMPLETE']
    status = 'UNKNOWN'
    if bad:
        order = ['ERROR','UNAVAILABLE','GAPS','PARTIAL','EMPTY','PROVISIONAL','RUNNING','NOT_STARTED','UNKNOWN']
        status = next(x for x in order if x in bad)
    else:
        spans = sorted((c['start_ms'],c['end_ms']) for c in coverages)
        cursor = start
        for lo,hi in spans:
            if lo <= cursor: cursor = max(cursor,hi)
        if cursor >= end and evidence and weakest == 'AVAILABLE': status = 'COMPLETE'
    if not closed and status == 'COMPLETE': status = 'PROVISIONAL'
    return dict(schema_version=VERSION,venue=rows[0]['venue'],category=rows[0]['category'],symbol=rows[0]['symbol'],
        timestamp_ms=start,provenance=sources+[derived],availability=availability,
        coverage={'status':status,'start_ms':start,'end_ms':end,'evidence_refs':evidence})


def _aggregate(trades, *, interval, price_step, as_of_ms=None, custom=False):
    if as_of_ms is not None: integer(as_of_ms)
    out = []
    for key, rows in grouped(trades,interval,price_step,custom=custom):
        start,end = key[-2:]
        closed = as_of_ms is not None and end <= as_of_ms
        record = metadata(rows,start,end,closed=closed)
        record.update(start_ms=start,end_ms=end,interval=interval,kind='derived',
            open=rows[0]['price'],high=decimal(max(number(t['price']) for t in rows)),
            low=decimal(min(number(t['price']) for t in rows)),close=rows[-1]['price'],
            volume=decimal(total(t['quantity'] for t in rows)),is_closed=closed)
        out.append(record)
    return out


def aggregate(trades, *, interval, price_step, as_of_ms=None):
    return _aggregate(trades,interval=interval,price_step=price_step,as_of_ms=as_of_ms)


def custom_candles(trades, *, interval, price_step, as_of_ms=None):
    """Analytics extension; custom interval is outside canonical v1 enum."""
    return _aggregate(trades,interval=interval,price_step=price_step,as_of_ms=as_of_ms,custom=True)


def taker_record(rows, start, end):
    r = metadata(rows,start,end)
    buy = total(t['quantity'] for t in rows if t['taker_side']=='Buy')
    sell = total(t['quantity'] for t in rows if t['taker_side']=='Sell')
    unknown = total(t['quantity'] for t in rows if t['taker_side']=='Unknown')
    r.update(start_ms=start,end_ms=end,side_basis='taker',buy_volume=decimal(buy),sell_volume=decimal(sell),
        unknown_side_volume=decimal(unknown),delta=None if unknown else decimal(buy-sell))
    if unknown:
        if r['availability']['status']=='AVAILABLE': r['availability']['status']='PARTIAL'
        r['availability']['reason']='; '.join(filter(None,[r['availability']['reason'],'Unknown taker side: full delta unavailable']))
        if r['coverage']['status']=='COMPLETE': r['coverage']['status']='PARTIAL'
    return r


def footprint(trades, *, interval, price_step):
    """Canonical v1 time-bucket taker totals; price_levels supplies clusters."""
    return [taker_record(rows,*key[-2:]) for key,rows in grouped(trades,interval,price_step)]


def price_levels(trades, *, interval, price_step):
    """Extension wrapper with exact price bounds and a canonical footprint child."""
    step = number(price_step,positive=True)
    out = []
    for key,rows in grouped(trades,interval,price_step):
        levels = defaultdict(list)
        for t in rows: levels[(number(t['price'])//step)*step].append(t)
        for lower, ts in sorted(levels.items()):
            out.append({'schema_version':VERSION,'price_lower':decimal(lower),'price_upper':decimal(lower+step),
                'price_step':decimal(step),'trade_count':len(ts),'footprint':taker_record(ts,*key[-2:])})
    return out


def trade_notional(t):
    validate_trade(t)
    p,q = number(t['price']),number(t['quantity'])
    if t['category']=='inverse': return {'value':decimal(q),'unit':'quote','base_quantity':decimal(q/p)}
    return {'value':decimal(p*q),'unit':'quote','base_quantity':decimal(q)}
