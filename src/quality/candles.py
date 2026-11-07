"""Decimal/UTC candle validation. Gaps are unknown, never fabricated."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

FIXED_MS={str(n):n*60000 for n in (1,3,5,15,30,60,120,240,360,720)}|{'D':86400000}

def next_boundary(ts,interval):
    if type(ts) is not int or ts<0: raise ValueError('integer UTC epoch ms required')
    if interval in FIXED_MS:
        step=FIXED_MS[interval]
        return ts+step, ts%step==0
    dt=datetime.fromtimestamp(ts//1000,timezone.utc)
    midnight=dt.hour==dt.minute==dt.second==dt.microsecond==0 and ts%1000==0
    if interval=='W': return ts+604800000, midnight and dt.weekday()==0
    if interval=='M':
        nxt=datetime(dt.year+(dt.month==12),dt.month%12+1,1,tzinfo=timezone.utc)
        return int(nxt.timestamp())*1000, midnight and dt.day==1
    raise ValueError('unsupported interval')

def inspect(rows,interval,now_ms,grace_ms=2000,allow_negative=False):
    if interval not in FIXED_MS and interval not in ('W','M'): raise ValueError('unsupported interval')
    errors,gaps,seen=[],[],set(); valid_times=[]
    for row in rows:
        try:
            if len(row)<6: raise ValueError('malformed')
            ts=row[0]
            if isinstance(ts,str) and ts.isdigit(): ts=int(ts)
            end,aligned=next_boundary(ts,interval)
            if ts in seen: errors.append(dict(ts=ts,reason='duplicate'))
            seen.add(ts)
            if not aligned: errors.append(dict(ts=ts,reason='unaligned'))
            if end+grace_ms>now_ms: errors.append(dict(ts=ts,reason='unclosed'))
            if any(isinstance(v,(float,bool)) for v in row[1:6]): raise ValueError('inexact_value')
            o,h,l,c,v=map(Decimal,row[1:6])
            if not all(x.is_finite() for x in (o,h,l,c,v)): raise ValueError('nonfinite')
            if h<max(o,c,l) or l>min(o,c,h) or (not allow_negative and min(o,h,l,c)<0): errors.append(dict(ts=ts,reason='invalid_ohlc'))
            if v<0: errors.append(dict(ts=ts,reason='negative_volume'))
            if len(row)>6 and row[6] is not None:
                t=Decimal(row[6])
                if not t.is_finite() or t<0: raise ValueError('invalid_turnover')
            valid_times.append(ts)
        except (ValueError,TypeError,InvalidOperation,OverflowError) as exc:
            errors.append(dict(ts=row[0] if row else None,reason=str(exc)))
    ordered=sorted(set(valid_times))
    for previous,current in zip(ordered,ordered[1:]):
        expected,_=next_boundary(previous,interval)
        if current>expected: gaps.append(dict(start=expected,end=current,classification='unknown'))
    return dict(records=len(rows),errors=errors,gaps=gaps,complete=False,locally_valid=not errors and not gaps)
