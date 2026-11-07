"""Only public Bybit market-data endpoints; no trading or authenticated requests."""
import os
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import quote
import requests

API = os.getenv("BYBIT_API", "https://api.bybit.com").rstrip("/")
ARCHIVE = os.getenv("BYBIT_ARCHIVE", "https://public.bybit.com").rstrip("/")
INTERVALS = ["1","3","5","15","30","60","120","240","360","720","D","W","M"]
OI_INTERVALS = ["5min","15min","30min","1h","4h","1d"]
ENDPOINTS = {"trade":"/v5/market/kline","mark":"/v5/market/mark-price-kline",
             "index":"/v5/market/index-price-kline","premium":"/v5/market/premium-index-price-kline"}
SYMBOL = re.compile(r"^[A-Z0-9-]{2,50}$")

def safe_symbol(symbol):
    if not SYMBOL.fullmatch(symbol):
        raise ValueError("Unsafe instrument symbol")
    return symbol

def tf_minutes(tf):
    return int(tf) if tf.isdigit() else {"D":1440,"W":10080,"M":44640}[tf]

def closed_before(tf, now_ms):
    """Start timestamp of current open UTC candle, including Monday-anchored weeks."""
    d = datetime.fromtimestamp(now_ms/1000, timezone.utc)
    if tf == "M":
        return int(datetime(d.year,d.month,1,tzinfo=timezone.utc).timestamp()*1000)
    length = tf_minutes(tf)*60000
    offset = 4*86400000 if tf == "W" else 0
    return ((now_ms-offset)//length)*length+offset

class Bybit:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "bybit-historical-laboratory/0.1"
        self.last = 0.0

    def api(self, endpoint, params=None):
        if not endpoint.startswith("/v5/market/"):
            raise ValueError("Only public market endpoints permitted")
        for attempt in range(7):
            delay = 0.20-(time.monotonic()-self.last)
            if delay > 0: time.sleep(delay)
            try:
                self.last = time.monotonic()
                r = self.s.get(API+endpoint,params=params,timeout=(10,40))
                if r.status_code in (429,500,502,503,504):
                    raise requests.RequestException("Transient HTTP "+str(r.status_code))
                r.raise_for_status()
                body = r.json()
                if body.get("retCode")==10006:
                    raise requests.RequestException("Rate limit")
                if body.get("retCode")!=0:
                    raise ValueError("Bybit %s: %s"%(body.get("retCode"),body.get("retMsg")))
                return body["result"]
            except (requests.RequestException,requests.Timeout,requests.ConnectionError):
                if attempt==6: raise
                time.sleep(min(2**attempt,30))
        raise RuntimeError("Unreachable")

    def instruments(self, category):
        out=[]; cursor=""; visited=set()
        while True:
            p={"category":category}
            if category!="spot":
                p["limit"]=1000
                if cursor: p["cursor"]=cursor
            data=self.api("/v5/market/instruments-info",p)
            out.extend(data.get("list",[]))
            nxt=data.get("nextPageCursor","")
            if not nxt: break
            if nxt in visited: raise ValueError("Repeated instruments cursor")
            visited.add(nxt)
            cursor=nxt
        return out

    def tickers(self, category):
        return self.api("/v5/market/tickers",{"category":category}).get("list",[])

    def archive_days(self, category, symbol):
        safe_symbol(symbol)
        folder = "spot" if category=="spot" else "trading"
        if category not in ("spot","linear","inverse"): raise ValueError(category)
        url="%s/%s/%s/"%(ARCHIVE,folder,quote(symbol))
        r=self.s.get(url,timeout=(10,45))
        r.raise_for_status()
        pattern=(re.escape(symbol)+("_" if category=="spot" else "")+
                 r"(\d{4}-\d{2}-\d{2})\.csv\.gz")
        result=set()
        for raw in re.findall(pattern,r.text):
            try: result.add(date.fromisoformat(raw))
            except ValueError: pass
        return sorted(result)

    def archive_url(self,category,symbol,day):
        safe_symbol(symbol)
        if category not in ("spot","linear","inverse"): raise ValueError(category)
        folder="spot" if category=="spot" else "trading"
        sep="_" if category=="spot" else ""
        return "%s/%s/%s/%s%s%s.csv.gz"%(ARCHIVE,folder,symbol,symbol,sep,day.isoformat())
