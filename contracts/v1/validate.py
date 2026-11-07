"""Structural/domain validation only. Analytics remain owned by component C."""
import json
from decimal import Decimal
from functools import lru_cache
from fractions import Fraction
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


class ContractError(ValueError):
    pass


@lru_cache(maxsize=1)
def _schema():
    value=json.loads(Path(__file__).with_name('market.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(value)
    return value


def validate_record(kind, record):
    """Raise ContractError on incompatible structure or unproved semantics.

    Validation does not authenticate source evidence, deduplicate records, or
    certify coverage. Producer tests must independently verify those claims.
    """
    schema=_schema()
    if kind not in schema['$defs']:
        raise ContractError('Unknown record kind')
    try:
        Draft202012Validator({'$schema':schema['$schema'],'$defs':schema['$defs'],
            '$ref':'#/$defs/'+kind},format_checker=FormatChecker()).validate(record)
    except ValidationError as error:
        raise ContractError(error.message) from error
    def require(condition,message):
        if not condition:raise ContractError(message)
    for item in [record,record.get('coverage',{})]:
        if 'start_ms' in item:
            require(item['start_ms']<item['end_ms'],'Range must be nonempty and half-open')
    availability=record.get('availability',record if kind=='availability' else {})
    if availability.get('status') in ('PARTIAL','UNAVAILABLE','UNVERIFIED'):
        require(bool((availability.get('reason') or '').strip()),'Unavailable/partial/unverified data requires reason')
    coverage=record.get('coverage',record if kind=='coverage' else {})
    if coverage.get('status')=='COMPLETE':
        require(bool(coverage.get('evidence_refs')),'Complete coverage needs evidence')
        if availability:require(availability['status']=='AVAILABLE','Incomplete availability cannot prove COMPLETE')
    sources=record.get('provenance',[])
    if kind=='provenance':sources=[record]
    if kind=='footprint' or (kind=='candle' and record.get('kind')=='derived'):
        require(any(p['source_kind']=='derived' for p in sources),'Derived record requires explicit derivation provenance')
    for source in sources:
        if source['source_kind']=='derived':
            require(bool(source.get('parent_refs')) and bool(source.get('method_version')),'Derived provenance requires parents and method version')
    if kind=='trade':
        ns=record['timestamp_ns'];precision=record['timestamp_precision']
        require(all(p['timestamp_precision']==precision for p in record['provenance']),'Source precision mismatch')
        if precision in ('s','ms'):
            require(ns is None,'Do not invent nanosecond precision from a coarse source')
            if precision=='s':require(record['timestamp_ms']%1000==0,'Second precision cannot invent milliseconds')
        elif ns is not None:
            require(int(ns)//1000000==record['timestamp_ms'],'Timestamp milliseconds/nanoseconds disagree')
            if precision=='us':require(int(ns)%1000==0,'Microsecond source cannot invent residual nanoseconds')
        if precision in ('us','ns'):
            require(ns is not None,'Source sub-millisecond precision must be preserved')
        if record['id_origin']=='synthetic':
            require(bool(record.get('identity_method')),'Synthetic identity needs a documented method')
            require(coverage['status']!='COMPLETE','Synthetic identity alone cannot establish COMPLETE')
    if kind=='candle':
        o,h,l,c=(Decimal(record[k]) for k in ['open','high','low','close'])
        require(l<=min(o,c)<=max(o,c)<=h,'Inconsistent OHLC')
        require(record['is_closed'] or coverage['status']!='COMPLETE','Unclosed candle cannot claim COMPLETE')
    if kind=='observation':
        value=Decimal(record['value'])
        if record['kind']=='oi':require(value>=0,'Negative open interest')
        if record['kind']=='ratio':require(0<=value<=1,'Ratio outside [0,1]')
    if kind=='footprint' and Decimal(record['unknown_side_volume'])>0:
        require(record['delta'] is None,'Missing taker side cannot produce verified delta')
        require(availability['status']!='AVAILABLE','Unknown taker side must retain partial/unverified availability')
    elif kind=='footprint' and record['delta'] is not None:
        require(Fraction(Decimal(record['delta']))==Fraction(Decimal(record['buy_volume']))-Fraction(Decimal(record['sell_volume'])),
                'Delta contradicts taker volumes')
    if kind=='progress':
        total=record['total_units']
        require(total is None or record['completed_units']<=total,'Progress exceeds denominator')
        if record['state']=='COMPLETE':
            require(total is not None and record['completed_units']==total,'Complete job requires verified known total')
    return record


def validate_response(record_kind, envelope):
    """Validate E's list response and every row; never promote weaker metadata."""
    validate_record('api_response',envelope)
    if not isinstance(envelope['data'],list):
        raise ContractError('Dataset response data must be a list')
    if not envelope['data'] and envelope['coverage']['status']=='COMPLETE':
        raise ContractError('Empty dataset does not establish complete coverage')
    for row in envelope['data']:
        validate_record(record_kind,row)
        if envelope['availability']['status']=='AVAILABLE' and row.get('availability',{}).get('status')!='AVAILABLE':
            raise ContractError('Envelope cannot promote partial/unverified data to AVAILABLE')
        if envelope['coverage']['status']=='COMPLETE' and row.get('coverage',{}).get('status')!='COMPLETE':
            raise ContractError('Envelope cannot promote unproved row coverage to COMPLETE')
    return envelope

