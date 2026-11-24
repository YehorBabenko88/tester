"""Extensions: exact taker metrics and traded-volume concentration."""
from collections import defaultdict
from fractions import Fraction
from .exact import number, decimal, total
from .core import grouped, taker_record, metadata


def candle_statistics(trades, *, interval, price_step, large_trade_threshold=None, value_area_fraction='0.7'):
    step = number(price_step,positive=True)
    fraction = number(value_area_fraction,positive=True)
    if fraction > 1: raise ValueError('Value area fraction exceeds one')
    threshold = None if large_trade_threshold is None else number(large_trade_threshold,positive=True)
    out = []; cumulative = {}; poisoned = {}; previous_end = {}
    for key,rows in grouped(trades,interval,price_step):
        market,start,end = key[:3],key[-2],key[-1]
        fp = taker_record(rows,start,end)
        qty = total(t['quantity'] for t in rows)
        buys,sells,unknown = (number(fp[k]) for k in ['buy_volume','sell_volume','unknown_side_volume'])
        delta = None if unknown else buys-sells
        levels = defaultdict(list)
        for t in rows: levels[number(t['price'])//step*step].append(t)
        volumes = {p:total(t['quantity'] for t in ts) for p,ts in levels.items()}
        prices = sorted(volumes)
        poc = min(prices,key=lambda p:(-volumes[p],p))
        lo = hi = prices.index(poc); area = volumes[poc]
        while area < qty*fraction:
            left = volumes[prices[lo-1]] if lo else Fraction(-1)
            right = volumes[prices[hi+1]] if hi+1 < len(prices) else Fraction(-1)
            if left >= right: lo -= 1; area += left
            else: hi += 1; area += right
        gap = market in previous_end and previous_end[market] != start
        poisoned[market] = poisoned.get(market,False) or unknown > 0 or gap
        cumulative[market] = cumulative.get(market,Fraction())+(delta or 0)
        previous_end[market] = end
        if poisoned[market]:
            if fp['availability']['status']=='AVAILABLE': fp['availability']['status']='PARTIAL'
            fp['availability']['reason']='; '.join(filter(None,[fp['availability']['reason'],'Full-window CVD unavailable: unknown side or missing bucket']))
            if fp['coverage']['status']=='COMPLETE': fp['coverage']['status']='PARTIAL'
        base_qty = sum((number(t['quantity'])/number(t['price']) if t['category']=='inverse' else number(t['quantity']) for t in rows),Fraction())
        quote_qty = sum((number(t['quantity']) if t['category']=='inverse' else number(t['quantity'])*number(t['price']) for t in rows),Fraction())
        large = [] if threshold is None else [t for t in rows if number(t['quantity'])>=threshold]
        profile = []
        for p in prices:
            level = taker_record(levels[p],start,end)
            b,s,u = (number(level[k]) for k in ['buy_volume','sell_volume','unknown_side_volume'])
            profile.append({'price_lower':decimal(p),'price_upper':decimal(p+step),'volume':decimal(volumes[p]),
                'buy_volume':decimal(b),'sell_volume':decimal(s),'unknown_side_volume':decimal(u),
                'delta':level['delta'],'imbalance':None if u else decimal((b-s)/(b+s)),
                'buy_sell_ratio':None if s==0 or u else decimal(b/s),
                'ratio_reason':'Unknown side or zero sell denominator' if s==0 or u else None})
        out.append(dict(schema_version='1.0',market=dict(zip(['venue','category','symbol'],market)),start_ms=start,end_ms=end,
            quality={k:fp[k] for k in ['schema_version','venue','category','symbol','timestamp_ms','provenance','availability','coverage']},trade_count=len(rows),buy_volume=decimal(buys),sell_volume=decimal(sells),
            unknown_side_volume=decimal(unknown),delta=None if delta is None else decimal(delta),
            cvd=None if poisoned[market] else decimal(cumulative[market]),
            cvd_scope='observed trades from first bucket; no inferred missing trades',
            cvd_reason='Unknown taker side or missing time bucket in cumulative window' if poisoned[market] else None,
            vwap=decimal(quote_qty/base_qty),base_volume=decimal(base_qty),quote_notional=decimal(quote_qty),
            quantity_unit='quote' if market[1]=='inverse' else 'base',
            volume_imbalance=None if unknown else decimal((buys-sells)/qty),
            volume_profile=profile,point_of_control=decimal(poc),
            value_area={'low':decimal(prices[lo]),'high_exclusive':decimal(prices[hi]+step),'fraction':decimal(fraction)},
            traded_volume_concentration=decimal(sum((v*v for v in volumes.values()),Fraction())/(qty*qty)),
            large_trades={'threshold':None if threshold is None else decimal(threshold),'count':len(large),
                'volume':decimal(total(t['quantity'] for t in large)),
                'maximum':decimal(max(number(t['quantity']) for t in rows))},
            ratio_precision='50 significant decimal digits, ROUND_HALF_EVEN; finite decimal results exact'))
    return out
