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
            elif tf=="M":
                # Calendar-month continuity (UTC) cannot be checked using fixed 31d steps.
                from datetime import datetime, timezone
                p=datetime.fromtimestamp(x/1000,timezone.utc)
                year,month=p.year,p.month
                expected=set()
                while True:
                    t=int(datetime(year,month,1,tzinfo=timezone.utc).timestamp()*1000)
                    if t>=min(y,closed_before(tf,utc_ms())):break
                    if t>=x:expected.add(t)
                    month+=1
                    if month==13:year,month=year+1,1
                status="complete" if set(valid)==expected else "gaps"
            else:
                # Check both interior gaps AND missing leading/trailing closed bars.
                width=tf_minutes(tf)*60000
                offset=4*DAY if tf=="W" else 0
                first=((x-offset+width-1)//width)*width+offset
                expected=set(range(first,min(y,closed_before(tf,utc_ms())),width))
                status="complete" if set(valid)==expected else "gaps"
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
        # Old releases certified sparse pages as complete. Re-query observations
        # until coverage records carry a versioned proof of the sampling check.
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
                number=Decimal(val)
                if not number.is_finite():raise ValueError("Nonfinite observation")
                if kind=='oi' and number<0:raise ValueError("Negative open interest")
                if kind=='ratio' and not 0<=number<=1:raise ValueError("Invalid account ratio")
                if kind!='funding' and ts%(minutes*60000):
                    raise ValueError("Observation outside sampling grid")
                records.append((ts,val,item))
            if len({r[0] for r in records})!=len(records):
                raise ValueError("Duplicate observation timestamp")
            count=db.save_observations(cat,sym,kind,tf,records)
            if kind=='funding':
                state='unknown'  # Funding schedules can change; page exhaustion is no proof.
            elif not records:
                state='empty'
            else:
                width=minutes*60000
                expected=set(range(((x+width-1)//width)*width,y,width))
                state='complete' if {r[0] for r in records}==expected else 'gaps'
                if state=='complete' and y>=utc_ms()-DAY:state='provisional'
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
        dest=root/cat/sym/(day.isoformat()+".csv.gz")
        part=dest.with_suffix(".part")
        size=0;count=0;digest=hashlib.sha256()
        try:
            # The transaction lock also protects the shared partial filename.
            with db.connect() as c:
                lock_key=int.from_bytes(hashlib.sha256(f"{cat}/{sym}/{day}".encode()).digest()[:8],"big",signed=True)
                c.execute("SELECT pg_advisory_xact_lock(%s)",(lock_key,))
                try:
                    # Because the lock query started the outer transaction, this
                    # context is a savepoint; rollback never releases that lock.
                    with c.transaction():
                        cached=c.execute("""SELECT f.sha256 FROM trade_files f JOIN coverage v
                            ON v.category=f.category AND v.symbol=f.symbol
                            AND v.kind='archive' AND v.timeframe='daily'
                            AND v.slice_start=%s AND v.slice_end=%s AND v.state='complete'
                            AND v.detail=f.sha256 WHERE f.category=%s AND f.symbol=%s AND f.day=%s""",
                            (start,start+DAY,cat,sym,day)).fetchone()
                        if cached and dest.is_file():
                            with dest.open("rb") as cached_stream:
                                cached_hash=hashlib.file_digest(cached_stream,"sha256").hexdigest()
                            if cached_hash==cached[0]:
                                progress(i,len(days),"cached archive");continue
                        dest.parent.mkdir(parents=True,exist_ok=True)
                        url=client.archive_url(cat,sym,day)
                        with client.s.get(url,stream=True,timeout=(20,150)) as response:
                            response.raise_for_status()
                            content_length=int(response.headers.get("Content-Length") or 0)
                            last_report=-1
                            with part.open("wb") as out:
                                for block in response.iter_content(1024*1024):
                                    if block:
                                        out.write(block);digest.update(block);size+=len(block)
                                        checkpoint=size//(16*1024*1024)
                                        if content_length>0 and checkpoint!=last_report:
                                            last_report=checkpoint
                                            progress(i-1+min(size/content_length,0.99),len(days),"archive bytes")
                        if not size:raise ValueError("Empty archive")
                        c.execute("CREATE TEMP TABLE archive_stage (LIKE trades INCLUDING ALL) ON COMMIT DROP")
                        with gzip.open(part,"rt",encoding="utf-8-sig",newline="") as stream:
                            reader=csv.DictReader(stream)
                            if not {"timestamp","side","size","price"}.issubset(reader.fieldnames or []):
                                raise ValueError("Unknown archive schema "+str(reader.fieldnames))
                            with c.cursor() as cur:
                                batch=[]
                                for row in reader:
                                    direction=row["side"].strip().capitalize()
                                    if direction not in ("Buy","Sell"):raise ValueError("Unknown taker side")
                                    price=Decimal(row["price"]);qty=Decimal(row["size"])
                                    if not price.is_finite() or not qty.is_finite() or price<=0 or qty<=0:
                                        raise ValueError("Invalid trade")
                                    ts=timestamp_ns(row["timestamp"])
                                    if not start*1000000<=ts<(start+DAY)*1000000:
                                        raise ValueError("Trade is outside archive UTC date")
                                    trade_id=row.get("trdMatchID") or row.get("execId")
                                    if not trade_id:raise ValueError("Trade ID absent")
                                    batch.append((cat,sym,day,trade_id,ts,str(price),str(qty),direction,db.Jsonb(row)))
                                    count+=1
                                    if len(batch)>=1500:
                                        cur.executemany("INSERT INTO archive_stage VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",batch)
                                        batch.clear()
                                if batch:
                                    cur.executemany("INSERT INTO archive_stage VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",batch)
                        # EOF above validates the gzip CRC before any data is published.
                        conflict=c.execute("""SELECT EXISTS(SELECT 1 FROM archive_stage s JOIN trades t
                            USING(category,symbol,day,trade_id)
                            WHERE (t.ts_ns,t.price,t.size,t.side,t.raw)
                            IS DISTINCT FROM (s.ts_ns,s.price,s.size,s.side,s.raw))""").fetchone()[0]
                        if conflict:raise ValueError("Existing trade ID has changed payload")
                        c.execute("INSERT INTO trades SELECT * FROM archive_stage ON CONFLICT DO NOTHING")
                        known=c.execute("SELECT count(*) FROM trades WHERE category=%s AND symbol=%s AND day=%s",(cat,sym,day)).fetchone()[0]
                        if known!=count:raise ValueError("Archive trade count differs from stored unique IDs")
                        c.execute("""INSERT INTO trade_files(category,symbol,day,url,sha256,bytes,row_count)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT(category,symbol,day) DO UPDATE SET url=EXCLUDED.url,
                            sha256=EXCLUDED.sha256,bytes=EXCLUDED.bytes,row_count=EXCLUDED.row_count,checked_at=now()""",
                            (cat,sym,day,url,digest.hexdigest(),size,count))
                        # Filesystem and PG cannot commit atomically. Rename failure rolls back PG;
                        # PG commit failure leaves an untrusted file and raises to the caller.
                        part.replace(dest)
                        c.execute("""INSERT INTO coverage(category,symbol,kind,timeframe,slice_start,slice_end,state,count,detail)
                            VALUES (%s,%s,'archive','daily',%s,%s,'complete',%s,%s)
                            ON CONFLICT(category,symbol,kind,timeframe,slice_start,slice_end)
                            DO UPDATE SET state=EXCLUDED.state,count=EXCLUDED.count,detail=EXCLUDED.detail,checked_at=now()""",
                            (cat,sym,start,start+DAY,count,digest.hexdigest()))
                except Exception as exc:
                    c.execute("""INSERT INTO coverage(category,symbol,kind,timeframe,slice_start,slice_end,state,count,detail)
                        VALUES (%s,%s,'archive','daily',%s,%s,'error',%s,%s)
                        ON CONFLICT(category,symbol,kind,timeframe,slice_start,slice_end)
                        DO UPDATE SET state=EXCLUDED.state,count=EXCLUDED.count,
                        detail=EXCLUDED.detail,checked_at=now()""",
                        (cat,sym,start,start+DAY,count,str(exc)[:1500]))

        except Exception as exc:
            # Connection/lock/savepoint/coverage/commit failures cannot safely be
            # recorded after releasing the lock. Surface them to the caller.
            raise RuntimeError(f"Archive database publication failed for {cat}/{sym}/{day}: {exc}") from exc
        progress(i,len(days),"trade archive")


def build_clusters(cat,sym,timeframes=INTERVALS):
    """Aggregate trade-side delta by exact executed price, with UTC calendar buckets."""
    with db.connect() as c:
        for tf in timeframes:
            if tf=="M":
                bucket="(extract(epoch from date_trunc('month',to_timestamp(ts_ns/1000000000.0) AT TIME ZONE 'UTC'))::bigint*1000000000)"
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
                FROM trades t WHERE category=%s AND symbol=%s
                AND EXISTS(SELECT f.sha256 FROM trade_files f JOIN coverage v
                  ON v.category=f.category AND v.symbol=f.symbol
                  AND v.kind='archive' AND v.timeframe='daily' AND v.state='complete'
                  AND v.detail=f.sha256
                  AND v.slice_start=(extract(epoch FROM (f.day::timestamp AT TIME ZONE 'UTC'))*1000)::bigint
                  AND v.slice_end=v.slice_start+86400000
                  WHERE f.category=t.category AND f.symbol=t.symbol AND f.day=t.day)
                GROUP BY 1,2,4,5"""
            # Group by the selected bucket; repeated parameter placeholders are
            # distinct PG expressions even when their Python values match.
            c.execute(q,args)
        c.commit()

