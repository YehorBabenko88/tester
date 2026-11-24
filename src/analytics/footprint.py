"""Legacy positional adapter; new consumers use src.analytics.footprint.

Raw legacy rows require category, symbol and canonical provenance.
"""
from collections import defaultdict
from .core import normalize_trade, deduplicate, custom_bounds, taker_record
from .exact import number, decimal


def aggregate(trades, timeframe_ms: int, tick_size: str):
    if isinstance(timeframe_ms,bool) or not isinstance(timeframe_ms,int) or timeframe_ms<=0:
        raise ValueError('Invalid timeframe')
    canonical = []
    for t in trades:
        canonical.append(normalize_trade(dict(t,time=str(t['timestamp'])),
            category=t['category'],symbol=t['symbol'],source_ref=t['provenance']))
    step = number(tick_size,positive=True)
    groups = defaultdict(list)
    for t in deduplicate(canonical):
        start,end = custom_bounds(t['timestamp_ms'],str(timeframe_ms)+'ms')
        level = number(t['price'])//step*step
        groups[(t['venue'],t['category'],t['symbol'],start,end,level)].append(t)
    result = []
    for key,rows in sorted(groups.items()):
        start,end,level = key[-3:]
        r = taker_record(rows,start,end)
        result.append(dict(start_ms=start,price=decimal(level),buy=r['buy_volume'],sell=r['sell_volume'],
            delta=r['delta'],trades=len(rows)))
    return result
