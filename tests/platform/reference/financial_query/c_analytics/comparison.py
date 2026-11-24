"""Evidence-gated comparisons of trade candles."""
from .core import metadata, unique, interval_bounds
from .exact import number, decimal


def compare_candles(calculated, official, *, tolerance=None, rounding=None):
    def index(rows):
        result = {}
        for r in rows:
            key = (r['venue'],r['category'],r['symbol'],r['interval'],r['start_ms'])
            if key in result: raise ValueError('Duplicate comparison candle key')
            result[key] = r
        return result
    left,right = index(calculated),index(official)
    tol = None if tolerance is None else number(tolerance)
    if tol is not None and tol < 0: raise ValueError('Negative tolerance')
    if rounding not in {None,'none'}: raise ValueError('Only explicit no-rounding comparison supported')
    out = []
    for key in sorted(left.keys()|right.keys()):
        a,b = left.get(key),right.get(key)
        ref = a or b
        start,end = ref['start_ms'],ref['end_ms']
        context = None; reason = None
        if a is None: status,reason = 'MISSING_CALCULATED','No calculated candle'
        elif b is None: status,reason = 'MISSING_OFFICIAL','No official trade candle'
        else:
            eligible = all(r['coverage']['status']=='COMPLETE' and r['coverage']['evidence_refs'] and
                r['coverage']['start_ms']<=start and r['coverage']['end_ms']>=end and
                r['availability']['status']=='AVAILABLE' and r['is_closed'] for r in [a,b])
            eligible = eligible and a['kind']=='derived' and b['kind']=='official_trade'
            eligible = eligible and (start,end)==(b['start_ms'],b['end_ms'])==interval_bounds(start,key[3])
            if not eligible or tol is None or rounding is None:
                status,reason = 'NOT_COMPARABLE','Requires complete closed trade candles, same UTC boundaries, evidence and explicit tolerance/rounding'
            else:
                status = 'MATCH'
                context = {'calculated_coverage':'COMPLETE','official_coverage':'COMPLETE',
                    'boundary_policy':'UTC [start_ms,end_ms); Monday week; calendar month',
                    'rounding':rounding,'tolerance':decimal(tol),
                    'evidence_refs':sorted(set(a['coverage']['evidence_refs']+b['coverage']['evidence_refs']))}
        proxy = [dict(r, _observations=[r]) for r in [a,b] if r]
        base = metadata(proxy,start,end)
        for field in ['open','high','low','close','volume']:
            av,bv = a[field] if a else None,b[field] if b else None
            difference = None if av is None or bv is None else decimal(number(av)-number(bv))
            field_status = status
            if context is not None:
                field_status = 'MATCH' if abs(number(difference)) <= tol else 'MISMATCH'
            out.append(dict(base,field=field,calculated=av,official=bv,difference=difference,
                status=field_status,reason=reason,context=context))
    return out
