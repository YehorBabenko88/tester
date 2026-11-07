"""Trade-based footprint and CVD. Side must be verified taker side."""
from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR

def aggregate(trades, timeframe_ms: int, tick_size: str):
    if timeframe_ms <= 0: raise ValueError("Invalid timeframe")
    tick=Decimal(tick_size)
    if tick<=0: raise ValueError("Invalid tick")
    buckets=defaultdict(lambda: {"buy":Decimal(0),"sell":Decimal(0),"count":0})
    for trade in trades:
        ts=int(trade["timestamp"])
        price=Decimal(str(trade["price"]))
        qty=Decimal(str(trade["size"]))
        side=trade["side"]
        if side not in ("Buy","Sell") or qty<0:
            raise ValueError("Unverified side or negative quantity")
        bar=ts//timeframe_ms*timeframe_ms
        level=(price/tick).to_integral_value(rounding=ROUND_FLOOR)*tick
        entry=buckets[(bar,level)]
        entry["buy" if side=="Buy" else "sell"]+=qty
        entry["count"]+=1
    out=[]
    for (bar,level),v in sorted(buckets.items()):
        out.append({"start_ms":bar,"price":str(level),"buy":str(v["buy"]),
                    "sell":str(v["sell"]),"delta":str(v["buy"]-v["sell"]),
                    "trades":v["count"]})
    return out

