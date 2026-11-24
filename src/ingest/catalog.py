"""Current instrument metadata and explicitly verified screener metrics."""
import math
from decimal import Decimal
from .rest import Backfill
from .journal import utcnow

def instruments(api,category,status=None):
    params={'category':category}
    if category!='spot': params['limit']=1000
    if status: params['status']=status
    seen=set()
    while True:
        result,url=api.call('/instruments-info',params)
        for row in result.get('list',[]): yield {**row,'source':{'url':url,'fetched_at':utcnow()}}
        cursor=result.get('nextPageCursor')
        if not cursor: break
        if cursor in seen: raise ValueError('Repeated instrument cursor')
        seen.add(cursor); params['cursor']=cursor

def volatility(rows,end_ms,window_ms):
    """Unannualized sqrt(sum(log(close[t]/close[t-1])**2)), 1m closes.

    end_ms denotes the timestamp of the last CLOSED candle start. Caller must
    establish candle close using exchange server time, not local clock.
    """
    if end_ms%60000 or window_ms%60000: return None
    closes={int(r[0]):Decimal(r[4]) for r in rows}
    times=range(end_ms-window_ms,end_ms+1,60000)
    if any(t not in closes or closes[t]<=0 for t in times): return None
    return math.sqrt(sum(math.log(float(closes[t]/closes[t-60000]))**2 for t in range(end_ms-window_ms+60000,end_ms+1,60000)))

def screener(api,journal,category,symbol):
    result,_=api.call('/time',{})
    server_ms=int(result['timeSecond'])*1000
    last_closed=server_ms//60000*60000-60000
    backfill=Backfill(api,journal).run(category,symbol,'kline','1',last_closed-86400000,last_closed+60000)
    rows=[]
    with journal.lock:
        import json
        for (body,) in journal.db.execute('SELECT body FROM records'):
            row=json.loads(body)
            if (row['category'],row['symbol'],row['dataset'],row['interval'])==(category,symbol,'kline','1'): rows.append(row['payload'])
    tickers,url=api.call('/tickers',{'category':category,'symbol':symbol})
    ticker=next(iter(tickers.get('list',[])),{})
    metrics={'symbol':symbol,'category':category,'asof_ms':last_closed,'volatility_1h':volatility(rows,last_closed,3600000),'volatility_24h':volatility(rows,last_closed,86400000),'turnover_24h':ticker.get('turnover24h'),'price_change_24h':ticker.get('price24hPcnt'),'open_interest':ticker.get('openInterest'),'oi_change_24h':None,'ticker_source':url,'coverage_status':backfill['status']}
    if category!='spot':
        out=Backfill(api,journal).run(category,symbol,'open_interest','5min',last_closed-86400000-300000,last_closed+60000)
        values={}
        with journal.lock:
            for (body,) in journal.db.execute('SELECT body FROM records'):
                row=json.loads(body)
                if (row['category'],row['symbol'],row['dataset'],row['interval'])==(category,symbol,'open_interest','5min'):
                    values[row['timestamp_ms']]=Decimal(row['payload']['openInterest'])
        target=last_closed//300000*300000
        if target in values and target-86400000 in values:
            previous=values[target-86400000]
            metrics['oi_change_24h']=str(values[target]-previous)
            metrics['oi_change_24h_pct']=str((values[target]/previous-1)*100) if previous else None
            metrics['oi_asof_ms']=target
    return metrics
