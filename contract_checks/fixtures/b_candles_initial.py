"""Independent UTC candle validation; no synthetic filling."""
from datetime import datetime, timezone

FIXED_MS = {"1":60000,"3":180000,"5":300000,"15":900000,"30":1800000,"60":3600000,"120":7200000,"240":14400000,"360":21600000,"720":43200000,"D":86400000}

def inspect(rows, interval, now_ms, grace_ms=2000):
    """Rows: [start_ms, open, high, low, close, volume, ...].
    W/M calendars require calendar-aware validation and are not accepted here.
    """
    if interval not in FIXED_MS:
        raise ValueError("Calendar interval requires calendar-aware validation")
    step = FIXED_MS[interval]
    errors, gaps, seen = [], [], set()
    ordered = sorted(rows, key=lambda x: int(x[0]))
    previous = None
    for row in ordered:
        ts = int(row[0])
        if ts in seen: errors.append({"ts":ts,"reason":"duplicate"})
        seen.add(ts)
        if ts % step: errors.append({"ts":ts,"reason":"unaligned"})
        if ts + step + grace_ms > now_ms: errors.append({"ts":ts,"reason":"unclosed"})
        o,h,l,c = map(float,row[1:5])
        if l > min(o,c) or h < max(o,c) or h < l:
            errors.append({"ts":ts,"reason":"invalid_ohlc"})
        if float(row[5]) < 0: errors.append({"ts":ts,"reason":"negative_volume"})
        if previous is not None and ts-previous>step:
            gaps.append({"start":previous+step,"end":ts-step,"count":(ts-previous)//step-1})
        previous=ts
    return {"records":len(ordered),"errors":errors,"gaps":gaps,"complete":not errors and not gaps}

