"""Dedicated PostgreSQL research database. Never mutate other databases or roles."""
import os
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

DBNAME=os.getenv("PGDATABASE","bybit_tester")

def cfg(dbname=None):
    return dict(host=os.getenv("PGHOST","127.0.0.1"),port=int(os.getenv("PGPORT","5432")),
                user=os.getenv("PGUSER","postgres"),password=os.getenv("PGPASSWORD",""),
                dbname=dbname or DBNAME,connect_timeout=8)

def connect():
    return psycopg.connect(**cfg())

def initialize():
    if not DBNAME.replace("_","").isalnum() or DBNAME in ("postgres","template0","template1"):
        raise ValueError("Invalid dedicated database name")
    with psycopg.connect(**cfg("postgres"),autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s",(DBNAME,)).fetchone():
            c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DBNAME)))
    with connect() as c:
        for statement in [
        """CREATE TABLE IF NOT EXISTS candles(category text NOT NULL,symbol text NOT NULL,kind text NOT NULL,
            timeframe text NOT NULL,ts bigint NOT NULL,open numeric NOT NULL,high numeric NOT NULL,
            low numeric NOT NULL,close numeric NOT NULL,volume numeric,turnover numeric,
            PRIMARY KEY(category,symbol,kind,timeframe,ts))""",
        """CREATE TABLE IF NOT EXISTS observations(category text NOT NULL,symbol text NOT NULL,kind text NOT NULL,
            timeframe text NOT NULL,ts bigint NOT NULL,value numeric,extra jsonb NOT NULL,
            PRIMARY KEY(category,symbol,kind,timeframe,ts))""",
        """CREATE TABLE IF NOT EXISTS trades(category text NOT NULL,symbol text NOT NULL,day date NOT NULL,
            trade_id text NOT NULL,ts_ns bigint NOT NULL,price numeric NOT NULL,size numeric NOT NULL,
            side text NOT NULL,raw jsonb NOT NULL,PRIMARY KEY(category,symbol,day,trade_id))""",
        """CREATE TABLE IF NOT EXISTS trade_files(category text NOT NULL,symbol text NOT NULL,day date NOT NULL,
            url text NOT NULL,sha256 text NOT NULL,bytes bigint NOT NULL,row_count bigint NOT NULL,
            checked_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(category,symbol,day))""",
        """CREATE TABLE IF NOT EXISTS coverage(category text NOT NULL,symbol text NOT NULL,kind text NOT NULL,
            timeframe text NOT NULL,slice_start bigint NOT NULL,slice_end bigint NOT NULL,
            state text NOT NULL,count bigint NOT NULL DEFAULT 0,detail text NOT NULL DEFAULT '',
            checked_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(category,symbol,kind,timeframe,slice_start,slice_end))""",
        """CREATE TABLE IF NOT EXISTS clusters(category text NOT NULL,symbol text NOT NULL,
            timeframe text NOT NULL,bucket bigint NOT NULL,price numeric NOT NULL,buy_volume numeric NOT NULL,
            sell_volume numeric NOT NULL,trade_count bigint NOT NULL,
            PRIMARY KEY(category,symbol,timeframe,bucket,price))""",
        "CREATE INDEX IF NOT EXISTS candles_time ON candles(category,symbol,kind,timeframe,ts)",
        "CREATE INDEX IF NOT EXISTS trades_time ON trades(category,symbol,ts_ns)"
        ]: c.execute(statement)

def coverage_state(cat,sym,kind,tf,a,b):
    with connect() as c:
        r=c.execute("""SELECT state FROM coverage WHERE category=%s AND symbol=%s AND kind=%s
          AND timeframe=%s AND slice_start=%s AND slice_end=%s""",(cat,sym,kind,tf,a,b)).fetchone()
        return r[0] if r else None

def coverage_set(cat,sym,kind,tf,a,b,state,count=0,detail=""):
    with connect() as c:
        c.execute("""INSERT INTO coverage(category,symbol,kind,timeframe,slice_start,slice_end,state,count,detail)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT(category,symbol,kind,timeframe,slice_start,slice_end)
          DO UPDATE SET state=EXCLUDED.state,count=EXCLUDED.count,detail=EXCLUDED.detail,
          checked_at=now()""",(cat,sym,kind,tf,a,b,state,count,str(detail)[:1500]))

def save_candles(cat,sym,kind,tf,rows,closed_until):
    batch=[(cat,sym,kind,tf,int(r[0]),r[1],r[2],r[3],r[4],
            r[5] if len(r)>5 else None,r[6] if len(r)>6 else None)
           for r in rows if int(r[0])<closed_until]
    if batch:
        with connect() as c:
            with c.cursor() as cur:
                cur.executemany("""INSERT INTO candles VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT(category,symbol,kind,timeframe,ts) DO UPDATE SET open=EXCLUDED.open,
                  high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,
                  volume=EXCLUDED.volume,turnover=EXCLUDED.turnover""",batch)
    return len(batch)

def save_observations(cat,sym,kind,tf,rows):
    if rows:
        with connect() as c:
            with c.cursor() as cur:
                cur.executemany("""INSERT INTO observations VALUES (%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT(category,symbol,kind,timeframe,ts) DO UPDATE SET
                  value=EXCLUDED.value,extra=EXCLUDED.extra""",
                  [(cat,sym,kind,tf,int(t),val,Jsonb(raw)) for t,val,raw in rows])
    return len(rows)

def report(cat,sym):
    with connect() as c:
        return c.execute("""SELECT kind,timeframe,state,count(*) FROM coverage WHERE category=%s
             AND symbol=%s GROUP BY kind,timeframe,state ORDER BY kind,timeframe,state""",(cat,sym)).fetchall()

def series(cat,sym,kind,tf,limit=4000):
    with connect() as c:
        if kind in ("trade","mark","index","premium"):
            rows=c.execute("""SELECT ts,open,high,low,close,volume FROM candles WHERE category=%s
              AND symbol=%s AND kind=%s AND timeframe=%s ORDER BY ts DESC LIMIT %s""",
              (cat,sym,kind,tf,limit)).fetchall()
        else:
            rows=c.execute("""SELECT ts,value FROM observations WHERE category=%s AND symbol=%s
              AND kind=%s AND timeframe=%s ORDER BY ts DESC LIMIT %s""",
              (cat,sym,kind,tf,limit)).fetchall()
    return rows[::-1]

def cluster_totals(cat,sym,tf,limit=4000):
    with connect() as c:
        return c.execute("""SELECT bucket/1000000 AS ts,
             sum(buy_volume) AS buys,sum(sell_volume) AS sells,
             sum(buy_volume-sell_volume) AS delta,sum(trade_count) AS trades
             FROM clusters WHERE category=%s AND symbol=%s AND timeframe=%s
             GROUP BY bucket ORDER BY bucket DESC LIMIT %s""",(cat,sym,tf,limit)).fetchall()[::-1]

def footprint(cat,sym,tf,limit=700):
    with connect() as c:
        return c.execute("""SELECT bucket/1000000,price,buy_volume,sell_volume,
                 buy_volume-sell_volume,trade_count FROM clusters
                 WHERE category=%s AND symbol=%s AND timeframe=%s
                 ORDER BY bucket DESC,price DESC LIMIT %s""",(cat,sym,tf,limit)).fetchall()
