"""Read-only official Bybit HTTP with bounded retry and shared throttling."""
import random
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
import requests


class Transport:
    def __init__(self, session=None, attempts=6, rate=3, sleep=time.sleep):
        if attempts<1 or rate<=0: raise ValueError('Invalid retry/rate settings')
        self.session=session
        self.local=threading.local()
        self.attempts,self.rate,self.sleep=attempts,rate,sleep
        self.lock=threading.Lock(); self.next_request=0

    def defer(self,seconds):
        with self.lock: self.next_request=max(self.next_request,time.monotonic()+seconds)

    def get(self,url,**kwargs):
        p=urlsplit(url)
        if p.scheme!='https' or p.netloc not in {'public.bybit.com','api.bybit.com'}:
            raise ValueError('Only official public Bybit HTTPS hosts allowed')
        for attempt in range(self.attempts):
            with self.lock:
                wait=max(0,self.next_request-time.monotonic())
                self.next_request=max(time.monotonic(),self.next_request)+1/self.rate
            self.sleep(wait)
            r=None
            try:
                if self.session is None and not hasattr(self.local,'session'): self.local.session=requests.Session()
                session=self.session if self.session is not None else self.local.session
                r=session.request('GET',url,timeout=(15,60),allow_redirects=False,**kwargs)
                if 300<=r.status_code<400: raise ValueError('Unexpected redirect')
                r.raise_for_status()
                return r
            except requests.RequestException as exc:
                status=exc.response.status_code if exc.response is not None else None
                if status is not None and status not in {403,429,500,502,503,504}: raise
                if attempt==self.attempts-1: raise
                delay=min(2**attempt,30)+random.uniform(0,0.2)
                if r is not None:
                    retry=r.headers.get('Retry-After')
                    if retry:
                        try: delay=max(delay,float(retry))
                        except ValueError:
                            try: delay=max(delay,parsedate_to_datetime(retry).timestamp()-time.time())
                            except (ValueError,TypeError): pass
                    if status==403: delay=max(delay,600)
                    r.close()
                self.defer(delay); self.sleep(delay)
