"""Exact normalized records; never invent time precision or market identity."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

VERSION = '1.0'
DATASETS = {'trades','candles','mark','index','premium','open_interest','funding','ratio'}
CANDLES = {'candles','mark','index','premium'}

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()

def integer(value,name):
    if type(value) is not int or not 0<=value<=9223372036854775807:
        raise ValueError(f'{name} must be nonnegative int64')
    return value

def decimal(value,name,minimum=None,positive=False):
    if not isinstance(value,(str,Decimal,int)) or isinstance(value,bool):
        raise ValueError(f'{name} requires exact decimal string')
    try:
        n=Decimal(value)
        if not n.is_finite() or (minimum is not None and n<minimum) or (positive and n<=0):
            raise ValueError(f'invalid {name}')
        if n.adjusted()>131071 or n.as_tuple().exponent < -16383: raise ValueError('numeric out of range')
        if n==0: return '0'
        exact=format(n,'f')
        return exact.rstrip('0').rstrip('.') if '.' in exact else exact
    except InvalidOperation as exc: raise ValueError(f'invalid {name}') from exc

def utc(value):
    if not isinstance(value,str): raise ValueError('aware fetched_at required')
    try: dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc: raise ValueError('invalid fetched_at') from exc
    if dt.tzinfo is None or dt.utcoffset() is None: raise ValueError('naive source time')
    return dt.astimezone(timezone.utc).isoformat()

def normalize(envelope):
    r=copy.deepcopy(envelope)
    if r.get('schema_version')!=VERSION: raise ValueError('unsupported schema_version')
    for name in ('venue','category','symbol','dataset'):
        if not isinstance(r.get(name),str) or not r[name] or len(r[name])>200: raise ValueError(f'invalid {name}')
    if r['venue']!='bybit' or r['category'] not in ('spot','linear','inverse','option'): raise ValueError('unsupported market')
    if r['dataset'] not in DATASETS: raise ValueError('unsupported dataset')
    integer(r.get('timestamp_ms'),'timestamp_ms')
    r.setdefault('interval','')
    if not isinstance(r['interval'],str): raise ValueError('invalid interval')
    if r['dataset']=='trades' and r['interval']!='': raise ValueError('trades have no candle interval')
    if r['dataset'] in CANDLES and not r['interval']: raise ValueError('candle interval required')
    p=r.get('payload'); source=r.get('source')
    if not isinstance(p,dict) or not isinstance(source,dict): raise ValueError('payload/source required')
    if not isinstance(source.get('url'),str) or not source['url']: raise ValueError('source URL required')
    source['fetched_at']=utc(source.get('fetched_at'))
    if 'sha256' in source and (not isinstance(source['sha256'],str) or len(source['sha256'])!=64 or any(x not in '0123456789abcdef' for x in source['sha256'])):
        raise ValueError('invalid SHA256')
    precision=r.get('timestamp_precision',p.get('timestamp_precision','ms'))
    ns=r.get('timestamp_ns',p.get('timestamp_ns'))
    if precision not in ('s','ms','us','ns'): raise ValueError('unsupported time precision')
    if precision=='s' and r['timestamp_ms']%1000: raise ValueError('seconds precision mismatch')
    if ns is not None:
        integer(ns,'timestamp_ns')
        if precision!='ns' or ns//1000000!=r['timestamp_ms']: raise ValueError('ns precision mismatch')
    if precision=='ns' and ns is None: raise ValueError('ns value required')
    if precision=='us':
        us=integer(r.get('timestamp_us',p.get('timestamp_us')),'timestamp_us')
        if us//1000!=r['timestamp_ms']: raise ValueError('us precision mismatch')
        r['timestamp_us']=us
    r['timestamp_ns'],r['timestamp_precision']=ns,precision
    r['trade_id'],r['id_origin']=None,None
    if r['dataset']=='trades':
        p['price']=decimal(p.get('price'),'price',positive=True)
        p['size']=decimal(p.get('size'),'size',positive=True)
        if p.get('side') not in ('Buy','Sell','Unknown'): raise ValueError('invalid taker side')
        tid=p.get('trade_id')
        if tid is not None:
            if not isinstance(tid,str) or not tid or len(tid)>512: raise ValueError('invalid original trade_id')
            r['trade_id'],r['id_origin']=tid,'exchange'
        else:
            if not isinstance(source.get('record_key'),str) or not source['record_key']: raise ValueError('missing ID requires stable source record_key')
            r['trade_id']='composite-v1:'+digest([source['record_key'],r['timestamp_ms'],ns,r.get('timestamp_us'),p['price'],p['size'],p['side']])
            r['id_origin']='composite_v1'
    elif r['dataset'] in CANDLES:
        for name in ('open','high','low','close'): p[name]=decimal(p.get(name),name,minimum=None if r['dataset']=='premium' else 0)
        o,h,l,c=(Decimal(p[x]) for x in ('open','high','low','close'))
        if h<max(o,c,l) or l>min(o,c,h): raise ValueError('invalid OHLC')
        for name in ('volume','turnover'):
            if name in p and p[name] is not None: p[name]=decimal(p[name],name,minimum=0)
    else:
        p['value']=decimal(p.get('value'),'value',minimum=None if r['dataset']=='funding' else 0)
        if r['dataset']=='ratio' and Decimal(p['value'])>1: raise ValueError('ratio > 1')
    identity=[r[x] for x in ('venue','category','symbol','dataset','interval')]
    identity=identity[:4]+[r['trade_id']] if r['dataset']=='trades' else identity+[r['timestamp_ms']]
    r['record_key']=digest(identity)
    r['content_hash']=digest({k:r[k] for k in ('schema_version','timestamp_ms','timestamp_ns','timestamp_precision','payload','trade_id','id_origin')}|{'timestamp_us':r.get('timestamp_us')})
    canonical(r)
    return r
