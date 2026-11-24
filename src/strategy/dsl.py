"""Strict, bounded, JSON-only strategy definitions. Never evaluates user code."""
import copy
import hashlib
import json
import re
from decimal import Decimal, localcontext
from datetime import datetime, timezone

DECIMAL = r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?'
FIELDS = ('open', 'high', 'low', 'close', 'volume')


def decimal(value):
    if not isinstance(value, str) or len(value) > 80 or not re.fullmatch(DECIMAL, value):
        raise ValueError('finite plain Decimal string required')
    return Decimal(value)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def keys(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('missing or unknown configuration keys')


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('integer outside permitted range')


def ratio(value, low=Decimal(0), high=Decimal(1), inclusive=True):
    x = decimal(value)
    if x < low or x > high or (not inclusive and x == low):
        raise ValueError('decimal outside permitted range')
    return x


def operand(x):
    if not isinstance(x, dict): raise ValueError('operand object required')
    if 'value' in x:
        keys(x, ['value']); decimal(x['value'])
    elif 'field' in x and 'indicator' not in x:
        keys(x, ['field'], ['offset'])
        if x['field'] not in FIELDS: raise ValueError('unsupported field')
    elif 'feature' in x:
        keys(x, ['feature'], ['offset'])
        if not isinstance(x['feature'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,99}', x['feature']):
            raise ValueError('invalid feature identifier')
    elif 'indicator' in x:
        keys(x, ['indicator', 'period'], ['field', 'offset'])
        if x['indicator'] not in ('sma', 'ema', 'rsi', 'vwap'): raise ValueError('unsupported indicator')
        if x.get('field', 'close') not in FIELDS: raise ValueError('unsupported field')
        integer(x['period'], 1, 10000)
    else: raise ValueError('unknown operand')
    if 'offset' in x: integer(x['offset'], 0, 10000)


def expression(x, depth=0, budget=None):
    if budget is None: budget = [256]
    budget[0] -= 1
    if depth > 16 or budget[0] < 0: raise ValueError('expression too complex')
    if not isinstance(x, dict): raise ValueError('expression required')
    op = x.get('op')
    if op in ('and', 'or'):
        keys(x, ['op', 'args'])
        if not isinstance(x['args'], list) or not 1 <= len(x['args']) <= 32: raise ValueError('invalid arguments')
        for arg in x['args']: expression(arg, depth+1, budget)
    elif op in ('gt', 'gte', 'lt', 'lte', 'eq', 'cross_above', 'cross_below'):
        keys(x, ['op', 'left', 'right']); operand(x['left']); operand(x['right'])
    elif op == 'pattern':
        keys(x, ['op', 'name'])
        if x['name'] not in ('bullish', 'bearish', 'bullish_engulfing', 'bearish_engulfing', 'doji'):
            raise ValueError('unsupported candle pattern')
    elif op == 'session':
        keys(x, ['op', 'start_minute', 'end_minute', 'weekdays'])
        integer(x['start_minute'], 0, 1439); integer(x['end_minute'], 0, 1440)
        if not isinstance(x['weekdays'], list) or not x['weekdays']: raise ValueError('weekdays required')
        for d in x['weekdays']: integer(d, 0, 6)
    else: raise ValueError('unsupported operator')


def validate_strategy(config):
    with localcontext() as ctx:
        ctx.prec = 50
        return _validate_strategy(config)


def _validate_strategy(config):
    # JSON round-trip prevents caller mutation and rejects non-JSON values.
    try: c = json.loads(json.dumps(config, allow_nan=False))
    except (ValueError, TypeError, RecursionError) as exc: raise ValueError('JSON required') from exc
    keys(c, ['schema_version', 'id', 'version', 'entry', 'exit', 'order', 'risk', 'costs', 'data_policy'])
    if c['schema_version'] != '1.0': raise ValueError('unsupported schema version')
    for k in ('id', 'version'):
        if not isinstance(c[k], str) or not 1 <= len(c[k]) <= 100: raise ValueError('identifier required')
    keys(c['entry'], [], ['long', 'short'])
    if not c['entry']: raise ValueError('entry required')
    for e in c['entry'].values(): expression(e)
    expression(c['exit'])
    o = c['order']; keys(o, ['type', 'latency_bars'], ['offset_bps', 'max_participation'])
    if o['type'] not in ('market', 'limit', 'stop'): raise ValueError('unsupported simulated order')
    integer(o['latency_bars'], 1, 10000)
    if o['type'] != 'market' and 'offset_bps' not in o: raise ValueError('order offset required')
    if 'offset_bps' in o: ratio(o['offset_bps'], high=Decimal(9999))
    if 'max_participation' in o: ratio(o['max_participation'], inclusive=False)
    r = c['risk']; keys(r, ['stop_loss', 'take_profit', 'trailing_stop', 'size', 'leverage', 'maintenance_margin'])
    ratio(r['stop_loss'], high=Decimal('0.99'), inclusive=False)
    ratio(r['take_profit'], high=Decimal(100), inclusive=False)
    ratio(r['trailing_stop'], high=Decimal('0.99'))
    ratio(r['leverage'], low=Decimal(1), high=Decimal(100))
    ratio(r['maintenance_margin'], high=Decimal('0.5'))
    if decimal(r['maintenance_margin']) >= 1/decimal(r['leverage']): raise ValueError('invalid margin')
    keys(r['size'], ['mode', 'value'])
    if r['size']['mode'] not in ('units', 'equity_fraction'): raise ValueError('unsupported sizing')
    ratio(r['size']['value'], high=Decimal(1) if r['size']['mode'] == 'equity_fraction' else Decimal('1e30'), inclusive=False)
    costs = c['costs']; keys(costs, ['fee_rate', 'slippage_bps', 'spread_bps', 'funding_policy'])
    ratio(costs['fee_rate'])
    for k in ('slippage_bps', 'spread_bps'): ratio(costs[k], high=Decimal(1000))
    if costs['funding_policy'] not in ('required', 'not_applicable', 'ignore_exploratory'): raise ValueError('funding policy required')
    keys(c['data_policy'], ['partial', 'gaps'])
    if c['data_policy']['partial'] not in ('reject', 'exploratory'): raise ValueError('partial policy required')
    if c['data_policy']['gaps'] not in ('reject', 'continue'): raise ValueError('gap policy required')
    return c


def value_at(x, bars):
    if 'value' in x: return decimal(x['value'])
    end = len(bars) - x.get('offset', 0)
    if end <= 0: return None
    b = bars[end-1]
    if 'feature' in x:
        f = b.get('features', {}).get(x['feature'])
        if not f or f['available_at_ms'] > b['end_ms'] or f['availability']['status'] != 'AVAILABLE' or f['coverage']['status'] != 'COMPLETE': return None
        return decimal(f['value'])
    field = x.get('field', 'close')
    if 'indicator' not in x: return decimal(b[field]) if field in b else None
    p = x['period']; kind = x['indicator']
    if end < p + (kind == 'rsi'): return None
    seq = [decimal(z[field]) for z in bars[end-p-(kind == 'rsi'):end]]
    if kind == 'sma': return sum(seq)/p
    if kind == 'vwap':
        vols = [decimal(z['volume']) for z in bars[end-p:end]]
        total = sum(vols)
        return sum(a*v for a, v in zip(seq, vols))/total if total else None
    if kind == 'ema':
        # Finite rolling EMA, initialized at first sample; definition is versioned.
        v = seq[0]; alpha = Decimal(2)/(p+1)
        for a in seq[1:]: v += alpha*(a-v)
        return v
    diffs = [b-a for a, b in zip(seq, seq[1:])]
    gain = sum(max(d, Decimal(0)) for d in diffs)
    loss = sum(max(-d, Decimal(0)) for d in diffs)
    return Decimal(50) if not gain and not loss else Decimal(100) if not loss else 100-100/(1+gain/loss)


def _evaluate(x, bars):
    if not bars: return False
    op = x['op']
    if op in ('and', 'or'):
        return (all if op == 'and' else any)(_evaluate(a, bars) for a in x['args'])
    if op == 'session':
        dt = datetime.fromtimestamp(bars[-1]['end_ms']/1000, timezone.utc)
        minute = dt.hour*60+dt.minute; start=x['start_minute']; end=x['end_minute']
        inside = start <= minute < end if start < end else minute >= start or minute < end
        return dt.weekday() in x['weekdays'] and inside
    if op == 'pattern':
        b = bars[-1]; o=decimal(b['open']); c=decimal(b['close']); name=x['name']
        if name == 'bullish': return c > o
        if name == 'bearish': return c < o
        if name == 'doji': return abs(c-o) <= (decimal(b['high'])-decimal(b['low']))*Decimal('0.1')
        if len(bars) < 2: return False
        prev=bars[-2]; po=decimal(prev['open']); pc=decimal(prev['close'])
        return (c > o and pc < po and o <= pc and c >= po) if name == 'bullish_engulfing' else (c < o and pc > po and o >= pc and c <= po)
    a=value_at(x['left'], bars); b=value_at(x['right'], bars)
    if a is None or b is None: return False
    if op in ('cross_above', 'cross_below'):
        pa=value_at(x['left'], bars[:-1]); pb=value_at(x['right'], bars[:-1])
        if pa is None or pb is None: return False
        return pa <= pb and a > b if op == 'cross_above' else pa >= pb and a < b
    return {'gt': lambda: a>b, 'gte': lambda: a>=b, 'lt': lambda: a<b,
            'lte': lambda: a<=b, 'eq': lambda: a==b}[op]()


def evaluate(expr, closed_bars):
    expression(expr)
    with localcontext() as ctx:
        ctx.prec = 50
        return _evaluate(expr, closed_bars)


def preview_text(text, candidate=None, assumptions=None):
    """Boundary for a translator: proposes a candidate, never guesses or runs text."""
    if not isinstance(text, str) or not text.strip() or len(text) > 20000: raise ValueError('text required')
    p = {'text': text, 'strategy': validate_strategy(candidate) if candidate is not None else None,
         'assumptions': list(assumptions or []), 'requires_confirmation': True}
    if not all(isinstance(a, str) for a in p['assumptions']): raise ValueError('assumptions must be strings')
    p['preview_id'] = digest(p)
    return p


def confirm_preview(preview, preview_id):
    p = copy.deepcopy(preview); stored = p.pop('preview_id', None)
    if stored != preview_id or digest(p) != preview_id or p.get('strategy') is None:
        raise ValueError('preview changed or unresolved; new confirmation required')
    return validate_strategy(p['strategy'])
