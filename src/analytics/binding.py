"""Tester-owned input boundary; existing C arithmetic is reused unchanged.

B projections are coordinator-owned injectable ports. No storage reads, pagination,
HTTP, acquisition or source certification is performed by this module.
"""
import copy
import re
from datetime import datetime, timezone

from . import core


class BindingUnavailable(ValueError):
    code = 'DATA_UNAVAILABLE'


def _integer(value, name):
    if type(value) is not int:
        raise BindingUnavailable(name+' must be explicit epoch integer milliseconds')
    core.integer(value)
    return value


def _scope(venue, category, symbol, start_ms, end_ms, cutoff):
    _integer(start_ms,'start_ms'); _integer(end_ms,'end_ms'); _integer(cutoff,'information_cutoff_ms')
    if start_ms >= end_ms or venue != 'bybit' or category not in {'spot','linear','inverse'}:
        raise BindingUnavailable('Invalid canonical market/range')
    return dict(venue=venue,category=category,symbol=symbol,start_ms=start_ms,end_ms=end_ms,cutoff=cutoff)


def _in_scope(row, scope):
    if any(row.get(k) != scope[k] for k in ['venue','category','symbol']):
        raise BindingUnavailable('Input instrument differs from requested scope')
    ts=_integer(row.get('timestamp_ms'),'timestamp_ms')
    if not scope['start_ms'] <= ts < scope['end_ms']:
        raise BindingUnavailable('Input timestamp outside half-open requested range')


def _weaken(record, reason):
    """Preserve stronger upstream failure/unknown evidence, never promote it."""
    if record['availability']['status']=='AVAILABLE':
        record['availability']['status']='PARTIAL'
    record['availability']['reason']='; '.join(filter(None,[record['availability']['reason'],reason]))
    if record['coverage']['status']=='COMPLETE':
        record['coverage']['status']='PARTIAL'


def _fetched_at_ms(source):
    try:
        text=source['fetched_at']
        if not isinstance(text,str): raise ValueError('Missing fetched_at')
        match=re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})',text)
        if not match: raise ValueError('Unsupported acquisition timestamp precision/format')
        stamp=datetime.fromisoformat(match[1]+match[3].replace('Z','+00:00'))
        delta=stamp.astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
        # Conservative ceiling: a fractional-ms fetch must not become known early.
        digits=match[2] or '0'; scale=10**len(digits)
        fraction_ms=(int(digits)*1000+scale-1)//scale
        return _integer(delta.days*86400000+delta.seconds*1000+fraction_ms,'fetched_at')
    except (KeyError,ValueError,TypeError,AttributeError) as exc:
        raise BindingUnavailable('Original aware source fetched_at required for cutoff') from exc


class TradeBinding:
    """In-process bound input snapshot. Public responses have no extension fields."""
    def __init__(self, records, scope, *, source_links=None, excluded_records=0,
                 excluded_observations=0, input_exhausted=False, base_quality=None):
        if type(input_exhausted) is not bool:
            raise BindingUnavailable('input_exhausted requires explicit boolean attestation')
        self._records=copy.deepcopy(records)
        self._scope=copy.deepcopy(scope)
        self._links=copy.deepcopy(source_links or {})
        self._base_quality=copy.deepcopy(base_quality)
        self.excluded_records=excluded_records
        self.excluded_observations=excluded_observations
        reasons=[]
        if not input_exhausted: reasons.append('Input sequence not attested exhausted; backend must drain pages')
        if excluded_records or excluded_observations: reasons.append('Information cutoff excludes input observations')
        for row in self._records:
            for reason in reasons: _weaken(row,reason)
            core.validate_trade(row)

    @property
    def trades(self):
        return copy.deepcopy(self._records)

    @property
    def source_links(self):
        """Audit sidecar retains original eligible B resolver objects, not wire fields."""
        return copy.deepcopy(self._links)

    def response(self, resource, *, interval, price_step, limit=1000):
        if resource not in {'candles','footprint'}:
            raise BindingUnavailable('Only canonical candle/footprint outputs supported; extensions are separate')
        if type(limit) is not int or not 1<=limit<=1000:
            raise BindingUnavailable('Output limit must be an integer 1..1000')
        start,end=self._scope['start_ms'],self._scope['end_ms']
        if core.interval_bounds(start,interval)[0] != start or core.interval_bounds(end-1,interval)[1] != end:
            raise BindingUnavailable('Requested output range must align with complete UTC bar boundaries')
        if resource=='candles':
            rows=core.aggregate(self._records,interval=interval,price_step=price_step,as_of_ms=self._scope['cutoff'])
        else:
            rows=core.footprint(self._records,interval=interval,price_step=price_step)
            for row in rows:
                if row['end_ms']>self._scope['cutoff']:
                    _weaken(row,'Bar extends beyond information cutoff')
        if len(rows)>limit:
            raise BindingUnavailable('Derived output exceeds requested limit; backend must request a bounded bar window')
        if rows:
            inherited=core.metadata(rows,start,end,closed=end<=self._scope['cutoff'])
            availability,coverage,provenance=(inherited[k] for k in ['availability','coverage','provenance'])
        else:
            empty_reason='No eligible input trades at requested information cutoff; empty does not prove completeness'
            availability=copy.deepcopy(self._base_quality['availability']) if self._base_quality else {'status':'UNAVAILABLE','reason':None}
            if availability['status']=='AVAILABLE': availability['status']='UNAVAILABLE'
            availability['reason']='; '.join(filter(None,[availability['reason'],empty_reason]))
            coverage=copy.deepcopy(self._base_quality['coverage']) if self._base_quality else {'status':'UNKNOWN','start_ms':start,'end_ms':end,'evidence_refs':[]}
            if coverage['status']=='COMPLETE': coverage['status']='UNKNOWN'
            provenance=[]
        return dict(schema_version='1.0',data=rows,next_cursor=None,
                    availability=availability,coverage=coverage,provenance=provenance)


def bind_canonical_trades(records, *, venue, category, symbol, start_ms, end_ms,
                          information_cutoff_ms, available_at_ms, input_exhausted=False):
    """Bind shared v1 snapshots with explicit full-record availability timestamps.

    available_at_ms(record) must cover the entire supplied snapshot, including all
    provenance and quality evidence. Event time/source_timestamp is not a fallback.
    """
    scope=_scope(venue,category,symbol,start_ms,end_ms,information_cutoff_ms)
    if not callable(available_at_ms): raise BindingUnavailable('Availability-time resolver required')
    selected=[]; excluded=0
    for raw in records:
        row=copy.deepcopy(core.validate_trade(raw)); _in_scope(row,scope)
        known=_integer(available_at_ms(copy.deepcopy(row)),'available_at_ms')
        if known>information_cutoff_ms or row['timestamp_ms']>information_cutoff_ms:
            excluded+=1
        else: selected.append(row)
    return TradeBinding(selected,scope,excluded_records=excluded,input_exhausted=input_exhausted)


def bind_storage_trades(records, *, venue, category, symbol, start_ms, end_ms,
                        information_cutoff_ms, quality_available_at_ms, availability, coverage,
                        trade_projector=None, provenance_projector=None, input_exhausted=False):
    """Bind B read rows through authentic MASTER projection, no copied conversion.

    Defaults import tester's coordinator module only when installed. Tests inject
    the exact pinned coordinator callables; missing ports fail closed. Quality
    must be explicitly resolved as of the cutoff by the integration owner.
    """
    scope=_scope(venue,category,symbol,start_ms,end_ms,information_cutoff_ms)
    if _integer(quality_available_at_ms,'quality_available_at_ms')>information_cutoff_ms:
        raise BindingUnavailable('Quality snapshot was not available at information cutoff')
    if (coverage.get('start_ms'),coverage.get('end_ms')) != (start_ms,end_ms):
        raise BindingUnavailable('Quality report differs from requested range')
    try:
        from contracts.v1.validate import validate_record
        validate_record('availability',availability)
        validate_record('coverage',coverage)
        if coverage['status']=='COMPLETE' and availability['status']!='AVAILABLE':
            raise ValueError('Incomplete availability cannot prove complete coverage')
    except (ImportError,ValueError) as exc:
        raise BindingUnavailable('Valid shared quality metadata is required') from exc
    if trade_projector is None or provenance_projector is None:
        try:
            from src.integration.storage_api import project_trade, provenance_from_storage
        except ImportError as exc:
            raise BindingUnavailable('Tester MASTER trade/provenance projection ports are unavailable') from exc
        trade_projector=trade_projector or project_trade
        provenance_projector=provenance_projector or provenance_from_storage
    selected=[]; links={}; excluded=0; excluded_observations=0
    for raw in records:
        row=copy.deepcopy(raw); _in_scope(row,scope)
        proof=row.get('provenance')
        if not isinstance(proof,dict) or proof.get('authentic') is not True or proof.get('unlinked_sources'):
            raise BindingUnavailable('All authentic original source-run observations required')
        observations=proof.get('observations')
        if not isinstance(observations,list) or not observations:
            raise BindingUnavailable('Missing source observation ledger')
        eligible=[]
        for obs in observations:
            source=obs.get('source',{})
            if (obs.get('authentic_run') is not True or obs.get('source_run_status')!='matched'
                    or not isinstance(obs.get('run_id'),str) or not obs['run_id']
                    or source.get('ingestion_run')!=obs['run_id']):
                raise BindingUnavailable('Original acquisition run must be explicitly matched')
            if _fetched_at_ms(source)>information_cutoff_ms:
                excluded_observations+=1
            else: eligible.append(obs)
        if not eligible or row['timestamp_ms']>information_cutoff_ms:
            excluded+=1
            continue
        bound_proof=copy.deepcopy(proof)
        bound_proof.update(observations=eligible,
            run_ids=sorted({o['run_id'] for o in eligible}),
            evidence_refs=sorted({o['evidence_id'] for o in eligible}))
        try:
            sources=provenance_projector(bound_proof)
            if len(sources)!=len(eligible): raise ValueError('Observation projection lost references')
            for p,obs in zip(sources,eligible):
                if p['ingestion_run']!=obs['run_id']: raise ValueError('Projection changed original run')
                refs=['storage:record:'+row['record_key']]
                for key,label in [('observation_id','observation'),('source_id','source'),('evidence_id','evidence'),('job_key','job')]:
                    if not isinstance(obs.get(key),str) or not obs[key]: raise ValueError('Missing storage linkage')
                    refs.append('storage:'+label+':'+obs[key])
                p['parent_refs']=sorted(set(p.get('parent_refs',[])+refs))
            projected=trade_projector(row,provenance=sources,availability=availability,coverage=coverage)
            core.validate_trade(projected)
        except (KeyError,ValueError,TypeError) as exc:
            raise BindingUnavailable('B projection cannot produce exact canonical trade metadata') from exc
        selected.append(projected)
        key=row['record_key']
        if key not in links: links[key]=bound_proof
        else:
            for field in ['observations','unlinked_sources']:
                links[key][field]=core.unique(links[key].get(field,[])+bound_proof.get(field,[]))
            for field in ['run_ids','evidence_refs']:
                links[key][field]=sorted(set(links[key].get(field,[])+bound_proof.get(field,[])))
    return TradeBinding(selected,scope,source_links=links,excluded_records=excluded,
                        excluded_observations=excluded_observations,input_exhausted=input_exhausted,
                        base_quality={'availability':availability,'coverage':coverage})
