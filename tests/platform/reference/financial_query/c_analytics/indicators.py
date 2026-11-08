"""Descriptive analytics extensions, never substitutes for unavailable sources.

Inputs are canonical candle/observation rows. Decimal transcendental results use
50 significant digits and HALF_EVEN. Financial sums/differences remain exact.
"""
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from fractions import Fraction
from functools import wraps
from .exact import number, decimal, total
from .core import metadata, interval_bounds


def precision(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with localcontext() as ctx:
            ctx.prec=50; ctx.rounding=ROUND_HALF_EVEN
            return fn(*args, **kwargs)
    return call


def series(rows, *, candle=True):
    rows = sorted(rows,key=lambda r:r['start_ms'] if candle else r['timestamp_ms'])
    keys = {(r['venue'],r['category'],r['symbol']) for r in rows}
    if len(keys)>1: raise ValueError('One market identity per series required')
    seen = set(); previous = None
    for r in rows:
        if r.get('schema_version')!='1.0' or not r.get('provenance') or not r.get('coverage') or not r.get('availability'):
            raise ValueError('Canonical metadata required')
        ts = r['start_ms'] if candle else r['timestamp_ms']
        if ts in seen: raise ValueError('Duplicate series timestamp')
        seen.add(ts)
        if candle:
            if (r['start_ms'],r['end_ms']) != interval_bounds(ts,r['interval']):
                raise ValueError('Inconsistent candle boundaries')
            if previous is not None and previous != ts: raise ValueError('Noncontiguous candle series; no gap filling')
            number(r['close'],positive=True)
            previous = r['end_ms']
        else: number(r['value'])
    if not candle and len({(r['kind'],r['unit'],r['interval']) for r in rows})>1:
        raise ValueError('Observation kind/unit/interval mismatch')
    return rows


def quality(rows):
    if not rows:
        return {'availability':{'status':'UNAVAILABLE','reason':'No observations'},'coverage':None,'provenance':[]}
    start=min(r.get('start_ms',r['timestamp_ms']) for r in rows)
    end=max(r.get('end_ms',r['timestamp_ms']+1) for r in rows)
    return metadata(rows,start,end)


def result(rows,value,reason=None,**extra):
    return {'value':value,'reason':reason,'quality':quality(rows),**extra}


def returns(rows):
    return [(Decimal(b['close'])/Decimal(a['close'])).ln() for a,b in zip(rows,rows[1:])]


@precision
def realized_volatility(candles):
    rows=series(candles); rs=returns(rows)
    if not rs: return result(rows,None,'At least two contiguous closes required')
    return result(rows,decimal(sum((r*r for r in rs),Decimal(0)).sqrt()),count=len(rs),annualized=False)


@precision
def historical_volatility(candles, *, periods_per_year):
    rows=series(candles); rs=returns(rows); scale=Decimal(decimal(number(periods_per_year,positive=True)))
    if len(rs)<2: return result(rows,None,'At least three contiguous closes required')
    mean=sum(rs)/len(rs)
    variance=sum((r-mean)**2 for r in rs)/(len(rs)-1)
    return result(rows,decimal((variance*scale).sqrt()),periods_per_year=decimal(number(periods_per_year)),ddof=1)


@precision
def returns_distribution(candles):
    rows=series(candles); rs=returns(rows)
    if not rs: return {'count':0,'mean':None,'reason':'At least two closes required','quality':quality(rows)}
    n=len(rs); mean=sum(rs)/n; variance=sum((r-mean)**2 for r in rs)/n
    ordered=sorted(rs)
    def quantile(fraction): return decimal(ordered[max(0,(n*fraction.numerator+fraction.denominator-1)//fraction.denominator-1)])
    return {'count':n,'mean':decimal(mean),'minimum':decimal(min(rs)),'maximum':decimal(max(rs)),
        'stddev':decimal(variance.sqrt()),'p05':quantile(Fraction(5,100)),'p50':quantile(Fraction(1,2)),
        'p95':quantile(Fraction(95,100)),
        'skewness':None if not variance else decimal(sum((r-mean)**3 for r in rs)/n/variance**Decimal('1.5')),
        'excess_kurtosis':None if not variance else decimal(sum((r-mean)**4 for r in rs)/n/variance**2-3),
        'returns':[decimal(r) for r in rs],'reason':None,'quality':quality(rows)}


def atr(candles, *, period=14):
    if isinstance(period,bool) or not isinstance(period,int) or period<=0: raise ValueError('Positive integer ATR period required')
    rows=series(candles); tr=[]
    for prev,row in zip(rows,rows[1:]):
        h,l,c=(number(v) for v in [row['high'],row['low'],prev['close']])
        if h<l: raise ValueError('Inconsistent high/low')
        tr.append(max(h-l,abs(h-c),abs(l-c)))
    if len(tr)<period: return result(rows,None,'Insufficient true ranges for ATR seed')
    value=sum(tr[:period],Fraction())/period
    for x in tr[period:]: value=((period-1)*value+x)/period
    return result(rows,decimal(value),period=period,method='Wilder; first TR needs previous close')


@precision
def volume_anomalies(candles, *, window=20, threshold='3'):
    if isinstance(window,bool) or not isinstance(window,int) or window<2: raise ValueError('Window must be >=2')
    cutoff=number(threshold,positive=True); rows=series(candles); out=[]
    for n,row in enumerate(rows):
        baseline=[number(r['volume']) for r in rows[max(0,n-window):n]]
        z=None; reason='Insufficient prior baseline'
        if len(baseline)==window:
            mean=sum(baseline,Fraction())/window
            variance=sum(((v-mean)**2 for v in baseline),Fraction())/window
            reason='Zero baseline variance' if not variance else None
            if variance: z=Decimal(decimal(number(row['volume'])-mean))/Decimal(decimal(variance)).sqrt()
        out.append({'timestamp_ms':row['timestamp_ms'],'z_score':None if z is None else decimal(z),
            'is_anomaly':None if z is None else abs(z)>=Decimal(decimal(cutoff)), 'reason':reason,
            'quality':quality(rows[max(0,n-window):n+1])})
    return out


def oi_change(observations):
    rows=series(observations,candle=False)
    if any(r['kind']!='oi' or number(r['value'])<0 for r in rows): raise ValueError('Nonnegative OI observations required')
    out=[]
    for n,row in enumerate(rows):
        prev=rows[n-1] if n else None
        change=None if prev is None else number(row['value'])-number(prev['value'])
        relative=None if prev is None or number(prev['value'])==0 else change/number(prev['value'])
        out.append({'timestamp_ms':row['timestamp_ms'],'change':None if change is None else decimal(change),
            'relative_change':None if relative is None else decimal(relative), 'unit':row['unit'],
            'quality':quality(rows[max(0,n-1):n+1])})
    return out


def oi_price_divergence(candles, observations):
    prices=series(candles); oi=series(observations,candle=False)
    if prices and oi and (prices[0]['venue'],prices[0]['category'],prices[0]['symbol']) != (oi[0]['venue'],oi[0]['category'],oi[0]['symbol']):
        raise ValueError('OI and price market mismatch')
    oi_change(oi)  # validate kind and unit consistency
    by_ts={r['timestamp_ms']:r for r in oi}; out=[]
    for n,row in enumerate(prices):
        prev=prices[n-1] if n else None
        current=by_ts.get(row['timestamp_ms']); before=by_ts.get(prev['timestamp_ms']) if prev else None
        change=None if current is None or before is None else number(current['value'])-number(before['value'])
        price_change=None if prev is None else number(row['close'])-number(prev['close'])
        inherited=quality([r for r in [prev,row,current,before] if r is not None])
        if change is None:
            if inherited['availability']['status']=='AVAILABLE': inherited['availability']['status']='PARTIAL'
            inherited['availability']['reason']='; '.join(filter(None,[inherited['availability']['reason'],'Missing required aligned OI observation pair']))
            if inherited['coverage']['status'] not in {'ERROR','UNAVAILABLE','GAPS'}:
                inherited['coverage']['status']='PARTIAL'
        out.append({'timestamp_ms':row['timestamp_ms'],'oi_change':None if change is None else decimal(change),
            'price_change':None if price_change is None else decimal(price_change),
            'divergent':None if change is None or price_change is None else change*price_change<0,
            'reason':'Requires exact timestamp pair; no forward fill' if change is None else None,
            'definition':'opposite signs of OI and close-price changes',
            'quality':inherited})
    return out


def funding_dynamics(observations):
    rows=series(observations,candle=False)
    if any(r['kind']!='funding' for r in rows): raise ValueError('Funding observations required')
    values=[number(r['value']) for r in rows]
    return {'rate_sum':decimal(sum(values,Fraction())) if values else None,
        'mean_rate':decimal(sum(values,Fraction())/len(values)) if values else None,
        'last_change':decimal(values[-1]-values[-2]) if len(values)>1 else None,
        'count':len(values),'annualized':False,'reason':None if values else 'No funding observations',
        'quality':quality(rows)}


@precision
def correlation(left, right):
    a,b=series(left),series(right)
    pairs_a={(x['start_ms'],y['start_ms']):r for x,y,r in zip(a,a[1:],returns(a))}
    pairs_b={(x['start_ms'],y['start_ms']):r for x,y,r in zip(b,b[1:],returns(b))}
    keys=sorted(pairs_a.keys()&pairs_b.keys())
    if len(keys)<2: return result(a+b,None,'At least two aligned log returns required')
    xs,ys=[pairs_a[k] for k in keys],[pairs_b[k] for k in keys]
    mx,my=sum(xs)/len(xs),sum(ys)/len(ys)
    vx,vy=sum((x-mx)**2 for x in xs),sum((y-my)**2 for y in ys)
    if not vx or not vy: return result(a+b,None,'Zero variance')
    value=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/(vx*vy).sqrt()
    return result(a+b,decimal(max(Decimal(-1),min(Decimal(1),value))),count=len(keys))


def volatility_ranking(markets, *, as_of_ms):
    out={}
    for label,horizon in [('1h',3600000),('24h',86400000)]:
        ranked=[]
        for name,candles in sorted(markets.items()):
            rows=series(candles)
            selected=[r for r in rows if r['start_ms']>=as_of_ms-horizon and r['end_ms']<=as_of_ms]
            if not selected or selected[0]['start_ms']!=as_of_ms-horizon or selected[-1]['end_ms']!=as_of_ms:
                item=result(selected,None,'Insufficient full-window history')
            elif any(not r['is_closed'] or r['coverage']['status']!='COMPLETE' or r['availability']['status']!='AVAILABLE' for r in selected):
                item=result(selected,None,'Unverified full-window coverage')
            else: item=realized_volatility(selected)
            ranked.append(dict(item,market=name))
        ranked.sort(key=lambda r:(r['value'] is None,-number(r['value']) if r['value'] is not None else 0,r['market']))
        out[label]=ranked
    return out
