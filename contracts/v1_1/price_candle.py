"""Price-only source validation; no invented volume or analytic calculation."""
from decimal import Decimal
from datetime import datetime,timezone
from functools import lru_cache
import json
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker,ValidationError
from contracts.v1.validate import ContractError,validate_record


@lru_cache(maxsize=1)
def _schema():
    schema=json.loads(Path(__file__).with_name('price_candle.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    return schema


def validate_price_candle(record):
    """Opt-in version1.1. Existing v1.0 validation is deliberately unchanged."""
    try:Draft202012Validator(_schema(),format_checker=FormatChecker()).validate(record)
    except ValidationError as error:raise ContractError(error.message) from error
    validate_record('availability',record['availability'])
    validate_record('coverage',record['coverage'])
    for source in record['provenance']:validate_record('provenance',source)
    start,end=record['start_ms'],record['end_ms']
    if not start<end or record['timestamp_ms']!=start:
        raise ContractError('Price candle timestamp must equal its half-open interval start')
    interval=record['interval']
    if interval=='M':
        try:
            d=datetime.fromtimestamp(start//1000,timezone.utc)
            if start%1000 or (d.day,d.hour,d.minute,d.second)!=(1,0,0,0):
                raise ContractError('Calendar month must start at UTC month boundary')
            next_month=d.replace(year=d.year+(d.month==12),month=d.month%12+1)
            expected=int(next_month.timestamp())*1000
        except (ValueError,OverflowError,OSError) as error:
            raise ContractError('Unsupported calendar timestamp') from error
    else:
        width={'D':86400000,'W':604800000}.get(interval)
        if width is None:width=int(interval)*60000
        offset=345600000 if interval=='W' else 0
        if (start-offset)%width:raise ContractError('Price candle is off its UTC interval grid')
        expected=start+width
    if end!=expected:raise ContractError('Price candle does not cover its declared fixed/calendar interval')
    if not record['coverage']['start_ms']<=start<end<=record['coverage']['end_ms']:
        raise ContractError('Coverage context must contain price-candle interval')
    o,h,l,c=(Decimal(record[key]) for key in ['open','high','low','close'])
    if not l<=min(o,c)<=max(o,c)<=h:raise ContractError('Inconsistent price OHLC')
    if record['coverage']['status']=='COMPLETE':
        if record['availability']['status']!='AVAILABLE' or not record['is_closed']:
            raise ContractError('Complete price history requires available, closed source evidence')
    return record
