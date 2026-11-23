"""Frozen regression reference from retired Streamlit UI; never a production entry point.

Original source: e64e30d8369626197403b3962f91bb350656e4c9 app.py.
The AST-based root regression supplies the clock and fake public API.
"""

def one_hour_range(cat,symbol):
    now=utc_ms(); end=now-now%60000
    start=end-60*60000
    raw=bybit().api("/v5/market/kline",dict(category=cat,symbol=symbol,
                  interval="1",start=start,end=end-1,limit=100)).get("list",[])
    bars={}
    try:
        for row in raw:
            ts=int(row[0])
            if not start<=ts<end:continue
            if ts in bars or ts%60000:return float("nan")
            values=tuple(float(row[i]) for i in range(1,5))
            if not all(math.isfinite(v) and v>0 for v in values):return float("nan")
            opening,high,low,close=values
            if not low<=min(opening,close)<=max(opening,close)<=high:return float("nan")
            bars[ts]=values
    except (ValueError,TypeError,IndexError,OverflowError):
        return float("nan")
    if set(bars)!=set(range(start,end,60000)):return float("nan")
    result=(max(v[1] for v in bars.values())-min(v[2] for v in bars.values()))*100/bars[start][0]
    return result if math.isfinite(result) else float("nan")
