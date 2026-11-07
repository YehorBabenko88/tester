"""Normalize disclosed trader events without confusing anonymous market prints with trader fills."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from json import dumps
from typing import Mapping

KINDS = frozenset({"DISCLOSED_EXECUTION", "DISCLOSED_POSITION", "ANONYMOUS_MARKET_PRINT", "INFERRED_EVENT"})
SIDES = frozenset({"BUY", "SELL", "UNKNOWN"})
VALIDITY = frozenset({"OBSERVED", "DISCLOSED", "INFERRED", "UNVERIFIED"})
MAX_EVENTS = 100000

def decimal(value, *, positive=False):
    if not isinstance(value, str) or not value or len(value)>128:
        raise ValueError("decimal must be an exact string")
    try: d=Decimal(value)
    except InvalidOperation as exc: raise ValueError("invalid decimal") from exc
    if not d.is_finite() or (positive and d<=0) or abs(d.as_tuple().exponent)>128 or abs(d.adjusted())>128:
        raise ValueError("nonfinite or nonpositive decimal")
    return d

@dataclass(frozen=True)
class Event:
    source: str
    trader_id: str | None
    event_id: str
    venue: str
    category: str
    symbol: str
    kind: str
    side: str
    timestamp_ms: int
    observed_at_ms: int
    price: str
    quantity: str
    validity: str
    source_ref: str
    revision: str

    @property
    def identity(self):
        return (*self.stable_identity, self.revision)

    @property
    def stable_identity(self):
        return (self.source, self.trader_id, self.venue, self.category, self.symbol, self.event_id)

def normalize(row: Mapping) -> Event:
    if not isinstance(row, Mapping): raise ValueError("invalid row")
    keys=("source","event_id","venue","category","symbol","kind","side","timestamp_ms","observed_at_ms","price","quantity","validity","source_ref","revision")
    for key in keys:
        if key not in row: raise ValueError("missing "+key)
    for key in ("source","event_id","venue","category","symbol","source_ref","revision"):
        if not isinstance(row[key],str) or not 0<len(row[key])<=256: raise ValueError("invalid "+key)
    if any(not isinstance(row[k],str) for k in ('kind','side','validity')) or row["kind"] not in KINDS or row["side"] not in SIDES or row["validity"] not in VALIDITY: raise ValueError("invalid classification")
    for key in ("timestamp_ms","observed_at_ms"):
        if type(row[key]) is not int or row[key]<0: raise ValueError("invalid timestamp")
    if row["observed_at_ms"] < row["timestamp_ms"]: raise ValueError("observed before event")
    trader=row.get("trader_id")
    if trader is not None and (not isinstance(trader,str) or not 0<len(trader)<=256): raise ValueError("invalid trader")
    if row["kind"]=="ANONYMOUS_MARKET_PRINT" and trader is not None: raise ValueError("anonymous print cannot identify trader")
    if row["kind"]=="INFERRED_EVENT" and row["validity"]!="INFERRED": raise ValueError("inferred event mislabeled")
    if row["kind"] in {"DISCLOSED_EXECUTION","DISCLOSED_POSITION"} and (trader is None or row["validity"]!="DISCLOSED"): raise ValueError("unverified disclosed event")
    if row['kind']=='ANONYMOUS_MARKET_PRINT' and row['validity']!='OBSERVED': raise ValueError('market classification')
    decimal(row["price"],positive=True); decimal(row["quantity"],positive=True)
    return Event(*(row.get(k) for k in ("source","trader_id","event_id","venue","category","symbol","kind","side","timestamp_ms","observed_at_ms","price","quantity","validity","source_ref","revision")))

def select_events(rows, *, start_ms, end_ms, trader_id=None, as_of_ms=None):
    if type(start_ms) is not int or type(end_ms) is not int or start_ms<0 or start_ms>=end_ms: raise ValueError("invalid range")
    if as_of_ms is not None and (type(as_of_ms) is not int or as_of_ms<0): raise ValueError("invalid as_of")
    seen={}; latest={}
    for idx,row in enumerate(rows):
        if idx>=MAX_EVENTS: raise ValueError("too many events")
        e=normalize(row)
        previous=seen.get(e.identity)
        if previous is not None:
            if previous!=e: raise ValueError("conflicting event identity")
            continue
        seen[e.identity]=e
        if as_of_ms is not None and e.observed_at_ms>as_of_ms: continue
        previous=latest.get(e.stable_identity)
        if previous and previous.revision!=e.revision and previous.observed_at_ms==e.observed_at_ms:
            raise ValueError('ambiguous revision order')
        if previous is None or e.observed_at_ms>previous.observed_at_ms: latest[e.stable_identity]=e
    return sorted([e for e in latest.values() if start_ms<=e.timestamp_ms<end_ms and
                   (trader_id is None or e.trader_id==trader_id)],
                  key=lambda e:(e.timestamp_ms,e.observed_at_ms,e.source,e.trader_id or '',e.venue,e.category,e.symbol,e.event_id,e.revision))

def analyze(rows, *, start_ms, end_ms, trader_id=None, as_of_ms=None):
    selected=select_events(rows,start_ms=start_ms,end_ms=end_ms,trader_id=trader_id,as_of_ms=as_of_ms)
    disclosed=[e for e in selected if e.kind=="DISCLOSED_EXECUTION"]
    inferred=[e for e in selected if e.kind=="INFERRED_EVENT"]
    digest=sha256(dumps({'events':[e.__dict__ for e in selected], 'start_ms':start_ms,
                        'end_ms':end_ms,'trader_id':trader_id,'as_of_ms':as_of_ms},
                        sort_keys=True,separators=(",",":")).encode()).hexdigest()
    overlays=[{"timestamp_ms":e.timestamp_ms,"price":e.price,"side":e.side,
               "event_id":e.event_id,"trader_id":e.trader_id,"source_ref":e.source_ref,
               "classification":e.validity,"kind":e.kind,"observed_at_ms":e.observed_at_ms,
               "source":e.source,"source_revision":e.revision,"venue":e.venue,
               "category":e.category,"symbol":e.symbol,"disclosure_lag_ms":e.observed_at_ms-e.timestamp_ms}
              for e in selected if e.kind in {"DISCLOSED_EXECUTION","INFERRED_EVENT"}]
    return {"schema_version":"1.0","start_ms":start_ms,"end_ms":end_ms,"as_of_ms":as_of_ms,
            "disclosed_executions":len(disclosed),"inferred_events":len(inferred),
            "anonymous_prints":sum(e.kind=="ANONYMOUS_MARKET_PRINT" for e in selected),
            "events":len(selected),"digest":digest,"overlays":overlays,
            "performance_status":"UNAVAILABLE","performance_reason":"execution records alone do not establish complete positions, costs or realized PnL"}
