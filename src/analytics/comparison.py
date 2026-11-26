"""Evidence-gated comparisons of trade candles."""
from collections import defaultdict
from fractions import Fraction
from .core import metadata, unique, interval_bounds, deduplicate, integer, quantity_unit
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


MINUTE_MS = 60000
# Unit of Bybit trade `size` (REST recent-trade and public archive) and of the
# 1m kline `volume` field (list[5]); both are compared in this unit. Kline
# `turnover` (list[6]) is the other leg (quote for linear/spot, base for
# inverse) and is NOT reconciled: per-trade price*size or size/price would need
# rounding the exchange does not document.
VOLUME_UNITS = {'base': 'base_coin', 'quote': 'quote_coin_contracts'}  # Labels only; canonical category authority is core.quantity_unit.
VOLUME_STATUSES = ('MATCH','MISMATCH','INCOMPLETE_TRADES','INCOMPLETE_CANDLES','OFF_STEP','UNIT_UNSUPPORTED')
VOLUME_POLICY = ('exact equality of sum(trade size) and official 1m kline volume per UTC minute '
    '[m, m+60000); no tolerance (legacy db.reconcile_minute_volume used 1e-6 relative, not adopted). '
    'MATCH only when the minute is closed, explicit COMPLETE trade coverage with evidence covers it, '
    'every trade is AVAILABLE with an exchange trade ID, the official_trade candle is closed, '
    'AVAILABLE and COMPLETE with evidence, and all quantities lie on qty_step. '
    'Status precedence: UNIT_UNSUPPORTED, INCOMPLETE_TRADES, INCOMPLETE_CANDLES, OFF_STEP, MATCH/MISMATCH. '
    'MATCH is a numeric consistency finding only; no proof that a trade was known at as_of_ms.')


def _covered(spans, start, end):
    cursor = start
    for lo,hi in sorted(spans):
        if lo <= cursor < hi: cursor = hi
        if cursor >= end: return True
    return cursor >= end


def reconcile_minute_volume(trades, candles, *, category, symbol, start_ms, end_ms, qty_step,
                            trade_coverage, as_of_ms):
    """Tick-trade volume vs official 1m candle volume quality control (#113 GAP-5).

    ``trades`` are canonical Trade records, ``candles`` canonical Candle records
    of one venue/category/symbol. ``trade_coverage`` lists the coverage
    mappings ({status,start_ms,end_ms,evidence_refs}) that prove trade
    completeness, e.g. one per verified archive day: an empty minute is only
    known to be empty from such evidence, so missing archive days and partially
    covered boundary minutes are INCOMPLETE_TRADES, never MATCH. ``qty_step`` is
    the instrument's quantity increment (lotSizeFilter.qtyStep; for spot the
    caller-declared base precision). See VOLUME_POLICY for the exact rule.
    Returns {'policy','unit','minutes':[per-minute findings],'summary'}.
    """
    start_ms, end_ms, as_of_ms = integer(start_ms), integer(end_ms), integer(as_of_ms)
    if start_ms % MINUTE_MS or end_ms % MINUTE_MS or start_ms >= end_ms:
        raise ValueError('Minute-aligned non-empty [start_ms,end_ms) required')
    step = number(qty_step, positive=True)
    market = ('bybit', category, symbol)
    rows = deduplicate(trades)
    if any((t['venue'],t['category'],t['symbol']) != market for t in rows) or \
            any((c['venue'],c['category'],c['symbol']) != market for c in candles):
        raise ValueError('Trade/candle market identity mismatch')
    # Out-of-window rows fail closed. A trade's event timestamp is NOT its
    # availability/observation timestamp: canonical Trade has no known_at_ms.
    # Consequently MATCH is equality of two sourced values, never historical
    # visibility, no-lookahead proof or completeness certification.
    for t in rows:
        ts = integer(t['timestamp_ms'])
        if not start_ms <= ts < end_ms:
            raise ValueError('trade_outside_reconciliation_window')
    for cov in trade_coverage:
        if not isinstance(cov, dict):
            raise ValueError('invalid_trade_coverage_record')
        if cov.get('status') == 'COMPLETE':
            lo, hi = integer(cov['start_ms']), integer(cov['end_ms'])
            if lo >= hi:
                raise ValueError('invalid_trade_coverage_window')
    by_minute = {}
    for c in candles:
        key = integer(c['start_ms'])
        if key in by_minute: raise ValueError('Duplicate comparison candle key')
        if key < start_ms or key >= end_ms:
            raise ValueError('candle_outside_reconciliation_window')
        by_minute[key] = c
    complete = [(integer(cov['start_ms']), integer(cov['end_ms']), tuple(cov['evidence_refs']))
                for cov in trade_coverage if cov.get('status') == 'COMPLETE' and cov.get('evidence_refs')]
    spans = [(lo, min(hi, as_of_ms)) for lo, hi, _ in complete]
    bucket = defaultdict(list)
    for t in rows: bucket[t['timestamp_ms'] - t['timestamp_ms'] % MINUTE_MS].append(t)
    try:
        unit = VOLUME_UNITS[quantity_unit(category)]
    except (ValueError, KeyError):
        unit = None
    out = []
    for m in range(start_ms, end_ms, MINUTE_MS):
        e = m + MINUTE_MS
        ts = bucket.get(m, [])
        c = by_minute.get(m)
        reasons = {s: [] for s in VOLUME_STATUSES}
        if unit is None: reasons['UNIT_UNSUPPORTED'].append('No documented volume unit for category')
        if c is not None and c.get('kind') != 'official_trade':
            reasons['UNIT_UNSUPPORTED'].append('Candle kind %r carries no exchange trade volume' % c.get('kind'))
        if e > as_of_ms: reasons['INCOMPLETE_TRADES'].append('Minute not closed at as_of_ms')
        if not _covered(spans, m, e):
            reasons['INCOMPLETE_TRADES'].append('No COMPLETE trade coverage evidence for the whole minute')
        if any(t['availability']['status'] != 'AVAILABLE' or t['id_origin'] != 'exchange' for t in ts):
            reasons['INCOMPLETE_TRADES'].append('Trade without AVAILABLE status or exchange trade ID')
        candle_volume = None
        if c is None: reasons['INCOMPLETE_CANDLES'].append('No official 1m candle')
        else:
            cov = c['coverage']
            if c.get('interval') != '1' or (c['start_ms'], c['end_ms']) != (m, e):
                reasons['INCOMPLETE_CANDLES'].append('Candle is not the 1m candle of this UTC minute')
            if c.get('is_closed') is not True or e > as_of_ms:
                reasons['INCOMPLETE_CANDLES'].append('Candle not closed')
            if c['availability']['status'] != 'AVAILABLE' or cov['status'] != 'COMPLETE' or \
                    not cov['evidence_refs'] or not cov['start_ms'] <= m < e <= cov['end_ms']:
                reasons['INCOMPLETE_CANDLES'].append('Candle lacks AVAILABLE/COMPLETE evidence for the minute')
            try:
                candle_volume = number(c['volume'])
                if candle_volume < 0: raise ValueError
            except (KeyError, ValueError, TypeError):
                candle_volume = None
                reasons['INCOMPLETE_CANDLES'].append('Candle volume missing or invalid')
        quantities = [number(t['quantity']) for t in ts]
        if any(q % step for q in quantities): reasons['OFF_STEP'].append('Trade size not a multiple of qty_step')
        if candle_volume is not None and candle_volume % step:
            reasons['OFF_STEP'].append('Candle volume not a multiple of qty_step')
        trade_volume = sum(quantities, Fraction())
        status = next((s for s in VOLUME_STATUSES[2:] if reasons[s]), None)
        if reasons['UNIT_UNSUPPORTED']: status = 'UNIT_UNSUPPORTED'
        elif status is None: status = 'MATCH' if trade_volume == candle_volume else 'MISMATCH'
        evidence = sorted({r for lo, hi, refs in complete if lo < e and hi > m for r in refs}
                          | set(c['coverage']['evidence_refs'] if c else []))
        out.append(dict(schema_version='1.0', check='minute_trade_volume_vs_kline', venue='bybit',
            category=category, symbol=symbol, start_ms=m, end_ms=e, status=status,
            reasons=[r for s in VOLUME_STATUSES for r in reasons[s]], unit=unit, qty_step=decimal(step),
            tolerance='0', trade_count=len(ts), trade_volume=decimal(trade_volume),
            candle_volume=None if candle_volume is None else decimal(candle_volume),
            difference=None if candle_volume is None else decimal(trade_volume - candle_volume),
            evidence_refs=evidence))
    summary = {}
    for r in out:
        s = summary.setdefault(r['status'], dict(minute_count=0, trade_count=0, largest_abs_difference=None))
        s['minute_count'] += 1; s['trade_count'] += r['trade_count']
        if r['difference'] is not None:
            d = abs(number(r['difference']))
            if s['largest_abs_difference'] is None or d > number(s['largest_abs_difference']):
                s['largest_abs_difference'] = decimal(d)
    return dict(policy=VOLUME_POLICY, unit=unit, minutes=out, summary=summary,
                historical_visibility='UNKNOWN',
                historical_completeness_certified=False,
                strategy_admission=False,
                knowledge_time_status='NOT_PROVEN')
