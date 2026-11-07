"""Bybit public market history downloader. Python 3.11+, pip install requests psycopg[binary]."""
import argparse, datetime as dt, json, os, time, hashlib
from decimal import Decimal
import requests
import psycopg

API="https://api.bybit.com/v5/market"
INTERVALS=["1","3","5","15","30","60","120","240","360","720","D","W","M"]
def call(path, params=None):
    for attempt in range(8):
        try:
            r=requests.get(API+path,params=params,timeout=35)
            if r.status_code in (429,500,502,503,504): raise RuntimeError(str(r.status_code))
            r.raise_for_status()
            j=r.json()
            if j.get("retCode") != 0: raise RuntimeError(str(j))
            return j["result"]
        except (requests.RequestException,RuntimeError):
            if attempt==7: raise
            time.sleep(min(2**attempt,30))
def instruments(category):
    cursor=None
    while True:
        p={"category":category,"limit":1000}
        if cursor:p["cursor"]=cursor
        r=call("/instruments-info",p)
        yield from r.get("list",[])
        cursor=r.get("nextPageCursor")
        if not cursor:break
def init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS raw_market(
        category text NOT NULL,symbol text NOT NULL,kind text NOT NULL,
        interval text NOT NULL DEFAULT '', ts bigint NOT NULL,
        payload jsonb NOT NULL, sha256 text NOT NULL,
        PRIMARY KEY(category,symbol,kind,interval,ts))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS ingest_ranges(
        category text,symbol text,kind text,interval text,
        range_start bigint,range_end bigint,records bigint,status text,
        updated_at timestamptz DEFAULT now(),
        PRIMARY KEY(category,symbol,kind,interval,range_start,range_end))""")
    conn.commit()
def persist(conn,category,symbol,kind,interval,rows):
    data=[]
    for row in rows:
        ts=int(row[0])
        payload=json.dumps(row,ensure_ascii=False,separators=(",",":"))
        digest=hashlib.sha256(payload.encode()).hexdigest()
        data.append((category,symbol,kind,interval,ts,payload,digest))
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO raw_market(category,symbol,kind,interval,ts,payload,sha256)
          VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s)
          ON CONFLICT(category,symbol,kind,interval,ts) DO UPDATE SET
          payload=excluded.payload,sha256=excluded.sha256""",data)
    conn.commit()
def fetch_backwards(conn,category,symbol,kind,interval,start,end):
    path={"kline":"/kline","mark":"/mark-price-kline",
          "index":"/index-price-kline","premium":"/premium-index-price-kline"}[kind]
    cursor=end
    total=0
    while cursor>=start:
        p={"category":category,"symbol":symbol,"interval":interval,
           "start":start,"end":cursor,"limit":1000}
        try: rows=call(path,p).get("list",[])
        except Exception as e:
            print(json.dumps({"error":str(e),"kind":kind,"interval":interval}),flush=True)
            raise
        rows=[r for r in rows if start<=int(r[0])<=cursor]
        if not rows:break
        persist(conn,category,symbol,kind,interval,rows)
        total+=len(rows)
        oldest=min(int(r[0]) for r in rows)
        if oldest>=cursor:break
        cursor=oldest-1
        print(json.dumps({"symbol":symbol,"kind":kind,"interval":interval,
            "records":total,"cursor_utc":dt.datetime.fromtimestamp(cursor/1000,dt.timezone.utc).isoformat()}),flush=True)
    return total
def oi(conn,category,symbol,start,end):
    if category not in ("linear","inverse"):return 0
    total=0
    for period in ("5min","15min","30min","1h","4h","1d"):
        cursor=end
        while cursor>=start:
            r=call("/open-interest",{"category":category,"symbol":symbol,
                "intervalTime":period,"startTime":start,"endTime":cursor,"limit":200})
            rows=[[x["timestamp"],x] for x in r.get("list",[])]
            if not rows:break
            persist(conn,category,symbol,"open_interest",period,rows)
            total+=len(rows)
            oldest=min(int(x[0]) for x in rows)
            if oldest>=cursor:break
            cursor=oldest-1
        print(json.dumps({"symbol":symbol,"kind":"open_interest","interval":period,"records":total}),flush=True)
    return total
def funding(conn,category,symbol,start,end):
    if category not in ("linear","inverse"):return 0
    cursor=end;total=0
    while cursor>=start:
        rows=call("/funding/history",{"category":category,"symbol":symbol,
            "startTime":start,"endTime":cursor,"limit":200}).get("list",[])
        if not rows:break
        batch=[[x["fundingRateTimestamp"],x] for x in rows]
        persist(conn,category,symbol,"funding","",batch)
        total+=len(batch)
        oldest=min(int(x[0]) for x in batch)
        if oldest>=cursor:break
        cursor=oldest-1
    return total
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--category",choices=["linear","inverse","spot"],default="linear")
    ap.add_argument("--list",action="store_true")
    ap.add_argument("--symbols",nargs="+")
    ap.add_argument("--start",default="2020-01-01")
    ap.add_argument("--end",default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--dsn",default=os.getenv("DATABASE_URL","postgresql://localhost/postgres"))
    args=ap.parse_args()
    if args.list:
        for x in instruments(args.category):print(x["symbol"],x.get("status",""))
        return
    if not args.symbols:ap.error("--symbols required unless --list")
    start=int(dt.datetime.fromisoformat(args.start).replace(tzinfo=dt.timezone.utc).timestamp()*1000)
    end=int(dt.datetime.fromisoformat(args.end).replace(tzinfo=dt.timezone.utc).timestamp()*1000)+86399999
    with psycopg.connect(args.dsn) as conn:
        init(conn)
        for symbol in args.symbols:
            for kind in ("kline","mark","index","premium"):
                if args.category=="spot" and kind!="kline":continue
                for interval in INTERVALS:
                    try:fetch_backwards(conn,args.category,symbol,kind,interval,start,end)
                    except Exception as e:print(f"FAILED {symbol} {kind} {interval}: {e}",flush=True)
            for kind,fn in (("open_interest",oi),("funding",funding)):
                try:fn(conn,args.category,symbol,start,end)
                except Exception as e:print(f"FAILED {symbol} {kind}: {e}",flush=True)
if __name__=="__main__":main()
