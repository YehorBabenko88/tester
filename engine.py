"""Resumable market-history ingestion with explicit incomplete/error coverage."""
import csv
import gzip
import hashlib
import os
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import db
from bybit import ENDPOINTS, INTERVALS, OI_INTERVALS, closed_before, tf_minutes

DAY=86400000

def utc_ms(): return int(time.time()*1000)

def slices(a,b,span):
    while a<b:
        end=min(a+span,b)
        yield a,end
        a=end

def candle_slices(tf,a,b):
    # Never let the API's 1000-record limit truncate a busy 1m series.
    step=min(45*DAY,700*tf_minutes(tf)*60000)
    yield from slices(a,b,max(60000,step))

def candle_kind(category):
    return ["trade"] if category=="spot" else ["trade","mark","index"]+(["premium"] if category=="linear" else [])

def download_candles(client,cat,sym,kind,tf,a,b,progress=lambda *args:None):
    chunks=list(candle_slices(tf,a,b))
    for i,(x,y) in enumerate(chunks,1):
        if db.coverage_state(cat,sym,kind,tf,x,y)=="complete":
            progress(i,len(chunks),kind+" "+tf+" cached");continue
        count=0
        try:
            rows=client.api(ENDPOINTS[kind],dict(category=cat,symbol=sym,
                interval=tf,start=x,end=y-1,limit=1000)).get("list",[])
            if len(rows)>=1000: raise ValueError("API page saturated; no completeness proof")
            valid=[]
            for r in rows:
                if len(r)<5: raise ValueError("Malformed candle")
                t=int(r[0])
                if not x<=t<y: raise ValueError("Timestamp outside request window")
                o,h,l,c=(Decimal(v) for v in r[1:5])
                if o<0 or l<0 or h<max(o,c,l) or l>min(o,c) or h<l:
                    raise ValueError("Invalid OHLC candle")
                if len(r)>5 and Decimal(r[5])<0: raise ValueError("Negative volume")
                if len(r)>6 and Decimal(r[6])<0: raise ValueError("Negative turnover")
                if t<closed_before(tf,utc_ms()): valid.append(t)
            if len(valid)!=len(set(valid)): raise ValueError("Duplicate candle timestamp")
            count=db.save_candles(cat,sym,kind,tf,rows,closed_before(tf,utc_ms()))
            if not valid:
                status="empty"
            elif tf.isdigit() or tf in ("D","W"):
                gap=any(v-u!=tf_minutes(tf)*60000 for u,v in zip(sorted(valid),sorted(valid)[1:]))
                status="gaps" if gap else "complete"
            else: status="complete"
            if y>closed_before(tf,utc_ms()):
                status="provisional"
            db.coverage_set(cat,sym,kind,tf,x,y,status,count,
                            "No closed records" if not valid else "Internal discontinuity" if status=="gaps" else "")
        except Exception as exc:
            db.coverage_set(cat,sym,kind,tf,x,y,"error",count,str(exc))
        progress(i,len(chunks),kind+" "+tf)

def download_observations(client,cat,sym,kind,tf,a,b,progress=lambda *args:None):
    path={"oi":"/v5/market/open-interest",
          "ratio":"/v5/market/account-ratio",
          "funding":"/v5/market/funding/history"}[kind]
    minutes={"5min":5,"15min":15,"30min":30,"1h":60,"4h":240,"1d":1440}.get(tf,480)
    # Short windows avoid hidden per-endpoint pagination/retention constraints.
    step=min(30*DAY,120*minutes*60000) if kind!="funding" else 14*DAY
    parts=list(slices(a,b,max(DAY,step)))
    for i,(x,y) in enumerate(parts,1):
        if db.coverage_state(cat,sym,kind,tf,x,y)=="complete":
            progress(i,len(parts),kind+" "+tf+" cached");continue
        try:
            p=dict(category=cat,symbol=sym,startTime=x,endTime=y-1,
                   limit=500 if kind=="ratio" else 200)
            if kind=="oi":p["intervalTime"]=tf
            if kind=="ratio":p["period"]=tf
            items=[]; cursor=""; seen=set()
            for _ in range(2000):
                if cursor:p["cursor"]=cursor
                result=client.api(path,p)
                page=result.get("list",[])
                items.extend(page)
                nxt=result.get("nextPageCursor","")
                if not nxt:
                    if len(page)>=p["limit"]:
                        raise ValueError("Full page but no cursor; unknown truncation")
                    break
                if nxt in seen: raise ValueError("Repeated pagination cursor")
                seen.add(nxt);cursor=nxt
            else:raise ValueError("Excessive API pagination")
            records=[]
            for item in items:
                ts=int(item.get("timestamp") or item.get("fundingRateTimestamp") or 0)
                if not x<=ts<y:raise ValueError("Observation outside requested range")
                val=item.get("openInterest") if kind=="oi" else (
                    item.get("buyRatio") if kind=="ratio" else item.get("fundingRate"))
                if val is None:raise ValueError("Missing numerical field")
                Decimal(val)
                records.append((ts,val,item))
            if len({r[0] for r in records})!=len(records):
                raise ValueError("Duplicate observation timestamp")
            count=db.save_observations(cat,sym,kind,tf,records)
            state="complete" if records and y<utc_ms()-DAY else ("provisional" if records else "empty")
            db.coverage_set(cat,sym,kind,tf,x,y,state,count)
        except Exception as exc:
            db.coverage_set(cat,sym,kind,tf,x,y,"error",0,str(exc))
        progress(i,len(parts),kind+" "+tf)

def timestamp_ns(raw):
    """Documented trade timestamp is seconds; detect common millisecond/microsecond archive eras."""
    n=Decimal(str(raw))
    if n>=Decimal("1e17"):return int(n)   # nanoseconds
    if n>=Decimal("1e14"):return int(n*1000)  # microseconds
    if n>=Decimal("1e11"):return int(n*1000000)  # milliseconds
    return int(n*1000000000)  # seconds, fractional allowed

def download_archives(client,cat,sym,days,progress=lambda *args:None):
    root=Path(os.getenv("BYBIT_DATA_DIR",".data")).resolve()
    for i,day in enumerate(days,1):
        start=int(datetime(day.year,day.month,day.day,tzinfo=timezone.utc).timestamp()*1000)
        if db.coverage_state(cat,sym,"archive","daily",start,start+DAY)=="complete":
            progress(i,len(days),"cached archive");continue
        dest=root/cat/sym/(day.isoformat()+".csv.gz")
        dest.parent.mkdir(parents=True,exist_ok=True)
        part=dest.with_suffix(".part")
        size=0;count=0;digest=hashlib.sha256()
        try:
            url=client.archive_url(cat,sym,day)
            with client.s.get(url,stream=True,timeout=(20,150)) as response:
                response.raise_for_status()
                with part.open("wb") as out:
                    for block in response.iter_content(1024*1024):
                        if block:
                            out.write(block);digest.update(block);size+=len(block)
            if not size: raise ValueError("Empty archive")
            # Reading until EOF validates gzip CRC. Rows are never silently omitted.
            with gzip.open(part,"rt",encoding="utf-8-sig",newline="") as stream:
                reader=csv.DictReader(stream)
                required={"timestamp","side","size","price"}
                if not required.issubset(reader.fieldnames or []):
                    raise ValueError("Unknown archive schema "+str(reader.fieldnames))
                with db.connect() as c:
                    with c.cursor() as cur:
                        batch=[]
                        for row in reader:
                            direction=row["side"].strip().capitalize()
                            if direction not in ("Buy","Sell"):raise ValueError("Unknown taker side")
                            price=Decimal(row["price"]);qty=Decimal(row["size"])
                            ts=timestamp_ns(row["timestamp"])
                            if not start*1000000<=ts<(start+DAY)*1000000:
                                raise ValueError("Trade is outside archive UTC date")
                            if price<=0 or qty<=0:raise ValueError("Invalid trade")
                            trade_id=row.get("trdMatchID") or row.get("execId")
                            if not trade_id:raise ValueError("Trade ID absent")
                            batch.append((cat,sym,day,trade_id,ts,str(price),str(qty),
                                          direction,db.Jsonb(row)))
                            count+=1
                            if len(batch)>=1500:
                                cur.executemany("""INSERT INTO trades VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                  ON CONFLICT DO NOTHING""",batch)
                                c.commit();batch.clear()
                        if batch:
                            cur.executemany("""INSERT INTO trades VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                              ON CONFLICT DO NOTHING""",batch)
                        c.commit()
            with db.connect() as c:
                known=c.execute("""SELECT count(*) FROM trades WHERE category=%s AND symbol=%s
                      AND day=%s""",(cat,sym,day)).fetchone()[0]
                if known!=count:raise ValueError("Archive trade count differs from stored unique IDs")
                c.execute("""INSERT INTO trade_files(category,symbol,day,url,sha256,bytes,row_count)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(category,symbol,day) DO UPDATE SET url=EXCLUDED.url,
                     sha256=EXCLUDED.sha256,bytes=EXCLUDED.bytes,row_count=EXCLUDED.row_count,
                     checked_at=now()""",(cat,sym,day,url,digest.hexdigest(),size,count))
            part.replace(dest)
            db.coverage_set(cat,sym,"archive","daily",start,start+DAY,"complete",count,digest.hexdigest())
        except Exception as exc:
            db.coverage_set(cat,sym,"archive","daily",start,start+DAY,"error",count,str(exc))
            part.unlink(missing_ok=True)
        progress(i,len(days),"trade archive")

def build_clusters(cat,sym,timeframes=INTERVALS):
    """Aggregate trade-side delta by exact executed price, with UTC calendar buckets."""
    with db.connect() as c:
        for tf in timeframes:
            if tf=="M":
                bucket="(extract(epoch from date_trunc('month',to_timestamp(ts_ns/1000000000.0)))::bigint*1000000000)"
                args=(tf,cat,sym)
            else:
                width=tf_minutes(tf)*60*1000000000
                offset=4*DAY*1000000 if tf=="W" else 0
                bucket="(((ts_ns-%s)/%s)*%s+%s)"
                args=(tf,offset,width,width,offset,cat,sym)
            c.execute("""DELETE FROM clusters WHERE category=%s AND symbol=%s
                      AND timeframe=%s""",(cat,sym,tf))
            q="""INSERT INTO clusters(category,symbol,timeframe,bucket,price,buy_volume,sell_volume,trade_count)
               SELECT category,symbol,%s,"""+bucket+""",price,
                COALESCE(sum(size) FILTER(WHERE side='Buy'),0),
                COALESCE(sum(size) FILTER(WHERE side='Sell'),0),count(*)
                FROM trades WHERE category=%s AND symbol=%s
                GROUP BY category,symbol,"""+bucket+""",price"""
            # The bucket expression occurs twice in SQL and therefore needs duplicated params.
            if tf=="M":
                c.execute(q,args)
            else:
                c.execute(q,(tf,offset,width,width,offset,cat,sym,offset,width,width,offset))
        c.commit()
