"""Snapshot gates. Evidence labels are validated, not independently certified."""
import copy
import re
from decimal import Decimal
from src.strategy.dsl import decimal, integer, keys


def metadata(x, start, end, exploratory, warnings):
    keys(x['availability'], ['status', 'reason'])
    status = x['availability']['status']
    if status not in ('AVAILABLE', 'PARTIAL'): raise ValueError('unverified/unavailable evidence')
    c=x['coverage']; keys(c, ['status', 'start_ms', 'end_ms', 'evidence_refs'])
    integer(c['start_ms'], 0, 9007199254740991); integer(c['end_ms'], c['start_ms']+1, 9007199254740991)
    if c['start_ms'] > start or c['end_ms'] < end: raise ValueError('coverage does not cover observation')
    if not isinstance(c['evidence_refs'], list) or not c['evidence_refs'] or not all(isinstance(r,str) and r for r in c['evidence_refs']):
        raise ValueError('coverage evidence required')
    if c['status'] not in ('COMPLETE', 'PARTIAL', 'GAPS', 'EMPTY', 'UNKNOWN', 'PROVISIONAL'):
        raise ValueError('invalid historical coverage')
    if status != 'AVAILABLE' or c['status'] != 'COMPLETE':
        if not exploratory: raise ValueError('partial history rejected')
        if not isinstance(x['availability']['reason'], str) and status != 'AVAILABLE': raise ValueError('partial reason required')
        warnings.add('NON_CERTIFIED')
    if not isinstance(x['provenance'], list) or not x['provenance']: raise ValueError('provenance required')
    for p in x['provenance']:
        keys(p, ['source_kind','source_ref','source_hash','ingestion_run','timestamp_precision','source_timestamp'], ['parent_refs','method_version'])
        if p['source_kind'] not in ('bybit_rest','bybit_archive','synthetic_fixture','derived'): raise ValueError('source kind')
        if not all(isinstance(p[k], str) and p[k] for k in ('source_ref','ingestion_run')): raise ValueError('source identity')
        if p['timestamp_precision'] not in ('s','ms','us','ns'): raise ValueError('precision')
        if p['source_hash'] is not None and not re.fullmatch('[a-f0-9]{64}',p['source_hash']): raise ValueError('source hash')
        if p['source_kind'] == 'synthetic_fixture': warnings.update(('SYNTHETIC_DATA','NON_CERTIFIED'))


def validate_snapshot(snapshot, strategy):
    s=copy.deepcopy(snapshot); warnings=set()
    keys(s, ['schema_version','venue','category','symbol','start_ms','end_ms','timeframe_ms','initial_equity',
             'quantity_step','candles','availability','coverage','provenance','funding'])
    if s['schema_version'] != '1.0' or s['venue'] != 'bybit': raise ValueError('snapshot contract')
    if s['category'] not in ('spot','linear'): raise ValueError('only spot and linear payoff supported')
    if not isinstance(s['symbol'], str) or not re.fullmatch('[A-Z0-9-]{2,50}',s['symbol']): raise ValueError('symbol')
    integer(s['start_ms'],0,9007199254740991); integer(s['end_ms'],s['start_ms']+1,9007199254740991)
    integer(s['timeframe_ms'],1,31*86400000)
    if decimal(s['initial_equity']) <= 0 or decimal(s['quantity_step']) <= 0: raise ValueError('positive capital/step required')
    exploratory = strategy['data_policy']['partial'] == 'exploratory'
    metadata(s,s['start_ms'],s['end_ms'],exploratory,warnings)
    if s['category']=='spot' and ('short' in strategy['entry'] or decimal(strategy['risk']['leverage']) != 1):
        raise ValueError('spot borrowing not modeled; use linear for short/leverage')
    expected=s['start_ms']; gaps=[]
    if not isinstance(s['candles'],list) or not s['candles']: raise ValueError('no price evidence')
    for b in s['candles']:
        keys(b, ['schema_version','venue','category','symbol','timestamp_ms','start_ms','end_ms','interval','kind',
                 'open','high','low','close','volume','is_closed','availability','coverage','provenance'],
             ['features','liquidity'])
        for k in ('schema_version','venue','category','symbol'):
            if b[k] != s[k]: raise ValueError('mixed identity/schema')
        integer(b['start_ms'],s['start_ms'],s['end_ms']-1)
        integer(b['end_ms'],b['start_ms']+1,s['end_ms'])
        if b['timestamp_ms'] != b['start_ms'] or type(b['timestamp_ms']) is not int: raise ValueError('candle timestamp')
        intervals={'1':60000,'3':180000,'5':300000,'15':900000,'30':1800000,'60':3600000,
                   '120':7200000,'240':14400000,'360':21600000,'720':43200000,'D':86400000,'W':604800000}
        if intervals.get(b['interval']) != s['timeframe_ms'] or b['end_ms']-b['start_ms'] != s['timeframe_ms']:
            raise ValueError('fixed timeframe required; calendar candles need explicit adapter')
        if b['start_ms'] < expected: raise ValueError('duplicate/overlapping/out-of-order candles')
        if b['start_ms'] > expected: gaps.append([expected,b['start_ms']])
        expected=b['end_ms']
        if b['is_closed'] is not True or b['kind'] not in ('official_trade','derived'): raise ValueError('closed trade prices required')
        vals={k:decimal(b[k]) for k in ('open','high','low','close','volume')}
        if min(vals[k] for k in ('open','high','low','close')) <= 0 or vals['volume'] < 0: raise ValueError('invalid OHLCV')
        if vals['low'] > min(vals['open'],vals['close']) or vals['high'] < max(vals['open'],vals['close']) or vals['low']>vals['high']:
            raise ValueError('inconsistent OHLC')
        metadata(b,b['start_ms'],b['end_ms'],exploratory,warnings)
        if 'features' in b:
            if not isinstance(b['features'],dict): raise ValueError('features map')
            for name,f in b['features'].items():
                keys(f,['value','available_at_ms','availability','coverage','provenance'])
                decimal(f['value']); integer(f['available_at_ms'],0,9007199254740991)
                metadata(f,b['start_ms'],b['end_ms'],exploratory,warnings)
        if 'liquidity' in b:
            q=b['liquidity']
            keys(q,['quantity','start_ms','end_ms','available_at_ms','evidence_refs',
                    'availability','coverage','provenance'])
            integer(q['start_ms'],0,9007199254740991)
            integer(q['end_ms'],q['start_ms']+1,9007199254740991)
            integer(q['available_at_ms'],0,9007199254740991)
            if (q['start_ms'],q['end_ms']) != (b['start_ms'],b['end_ms']):
                raise ValueError('liquidity range must match its bar')
            if q['available_at_ms']!=b['end_ms']:
                raise ValueError('whole-bar liquidity must become available exactly at close; late or premature evidence rejected')
            if (decimal(q['quantity'])<0 or not isinstance(q['evidence_refs'],list) or
                    not q['evidence_refs'] or not all(isinstance(r,str) and r for r in q['evidence_refs'])):
                raise ValueError('liquidity quantity/evidence required')
            # Exploratory price policy never upgrades incomplete execution evidence.
            metadata(q,b['start_ms'],b['end_ms'],False,warnings)
    if 'max_participation' in strategy['order']:
        if any('liquidity' not in b for b in s['candles']):
            raise ValueError('partial fills require liquidity evidence on every bar')
        warnings.add('BAR_CAPACITY_TIMING_UNCERTAIN')
    if expected < s['end_ms']: gaps.append([expected,s['end_ms']])
    if gaps:
        if strategy['data_policy']['gaps']=='reject': raise ValueError('data gaps')
        warnings.update(('NON_CERTIFIED','DATA_GAPS'))
    funding=strategy['costs']['funding_policy']
    if s['category']=='linear' and funding=='not_applicable': raise ValueError('linear funding must be modeled or explicitly ignored')
    if s['category']=='spot' and funding!='not_applicable': raise ValueError('spot funding policy')
    if funding=='ignore_exploratory': warnings.update(('FUNDING_IGNORED','NON_CERTIFIED'))
    if funding=='required':
        f=s['funding']
        if not isinstance(f,dict): raise ValueError('funding evidence missing')
        keys(f,['availability','coverage','provenance','expected_timestamps','events'])
        metadata(f,s['start_ms'],s['end_ms'],False,warnings)
        times=f['expected_timestamps']
        if not isinstance(times,list) or times != sorted(set(times)): raise ValueError('funding schedule')
        actual=[]; starts={b['start_ms'] for b in s['candles']}
        for event in f['events']:
            keys(event,['timestamp_ms','rate','mark_price'])
            integer(event['timestamp_ms'],s['start_ms'],s['end_ms']-1)
            if event['timestamp_ms'] not in starts: raise ValueError('funding needs observed bar boundary')
            if abs(decimal(event['rate']))>1 or decimal(event['mark_price'])<=0: raise ValueError('invalid funding')
            actual.append(event['timestamp_ms'])
        if actual != times: raise ValueError('funding schedule incomplete')
    warnings.update(('OHLC_EXECUTION_MODEL','FIXED_SPREAD_MODEL','LIQUIDATION_MODEL_SIMPLIFIED'))
    return s, sorted(warnings), gaps
