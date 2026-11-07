"""Crash-safe local task/provenance journal; not the shared market database."""
import json
import sqlite3
import threading
from datetime import datetime,timezone

def utcnow(): return datetime.now(timezone.utc).isoformat()

class Journal:
    def __init__(self,path):
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.lock=threading.RLock()
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS tasks(key TEXT PRIMARY KEY,status TEXT,detail TEXT,updated TEXT); CREATE TABLE IF NOT EXISTS records(key TEXT PRIMARY KEY,body TEXT NOT NULL);')

    def task(self,key):
        with self.lock: row=self.db.execute('SELECT status,detail FROM tasks WHERE key=?',(key,)).fetchone()
        return None if row is None else {'status':row[0],**json.loads(row[1])}

    def save(self,key,status,detail,records=()):
        with self.lock,self.db:
            for record in records:
                fields=('venue','category','symbol','dataset','trade_id') if record.get('trade_id') else ('venue','category','symbol','dataset','interval','timestamp_ms')
                identity=json.dumps([record[k] for k in fields],separators=(',',':'))
                self.db.execute('INSERT OR REPLACE INTO records VALUES(?,?)',(identity,json.dumps(record,separators=(',',':'))))
            self.db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?,?,?)',(key,status,json.dumps(detail),utcnow()))

    def export(self,path):
        with self.lock,open(path,'w',encoding='utf-8') as out:
            for row in self.db.execute('SELECT body FROM records ORDER BY key'): out.write(row[0]+'\n')

    def window_records(self,category,symbol,dataset,interval,start,end):
        with self.lock:
            records=[json.loads(row[0]) for row in self.db.execute('SELECT body FROM records')]
        return {r['timestamp_ms']:r for r in records if (r['category'],r['symbol'],r['dataset'],r['interval'])==(category,symbol,dataset,interval) and start<=r['timestamp_ms']<end}

    def close(self): self.db.close()
