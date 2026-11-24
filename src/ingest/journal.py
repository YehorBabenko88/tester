"""Crash-safe local task/provenance journal; not the shared market database."""
import json
import sqlite3
import threading
import os
import uuid
import tempfile
import base64
import hashlib
from pathlib import Path
from itertools import chain
from datetime import datetime,timezone
from .handoff import build_bundle,canonical,digest,record_key,semantic_hash

def utcnow(): return datetime.now(timezone.utc).isoformat()

class Journal:
    def __init__(self,path):
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.lock=threading.RLock()
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS tasks(key TEXT PRIMARY KEY,status TEXT,detail TEXT,updated TEXT); CREATE TABLE IF NOT EXISTS records(key TEXT PRIMARY KEY,body TEXT NOT NULL);')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY,body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS evidence(evidence_ref TEXT PRIMARY KEY,body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS task_attempts(event_id TEXT PRIMARY KEY,task_key TEXT,run_id TEXT,status TEXT,detail TEXT,created_at TEXT);
          CREATE TABLE IF NOT EXISTS record_versions(record_id TEXT PRIMARY KEY,record_key TEXT NOT NULL,body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS record_links(record_id TEXT,event_id TEXT,PRIMARY KEY(record_id,event_id));
          CREATE TABLE IF NOT EXISTS journal_metadata(key TEXT PRIMARY KEY,value TEXT);
        ''')
        # Legacy rows/tasks survive migration but missing acquisition facts stay unknown.
        if not self.db.execute("SELECT 1 FROM journal_metadata WHERE key='handoff_v1'").fetchone():
            old_rows=[json.loads(r[0]) for r in self.db.execute('SELECT body FROM records')]
            old_tasks=list(self.db.execute('SELECT key,status,detail,updated FROM tasks'))
            if old_rows or old_tasks:
                run=self.start_run('legacy_import',{'reason':'Acquisition linkage unavailable in pre-handoff journal'})
                self.save('legacy-record-import','unverified',{'legacy':True},old_rows,run_id=run)
                for key,status,detail,updated in old_tasks:
                    self.save(key,status,{**json.loads(detail),'legacy_updated_at':updated,'legacy':True},run_id=run)
                self.finish_run(run,'UNVERIFIED')
            with self.db: self.db.execute("INSERT INTO journal_metadata VALUES('handoff_v1','1')")

    def task(self,key):
        with self.lock: row=self.db.execute('SELECT status,detail FROM tasks WHERE key=?',(key,)).fetchone()
        return None if row is None else {'status':row[0],**json.loads(row[1])}

    def start_run(self,kind,request=None,run_id=None):
        run_id=run_id or 'run:'+uuid.uuid4().hex
        body={'ingestion_run':run_id,'kind':kind,'request':request or {},'created_at':utcnow(),'status':'RUNNING'}
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO runs VALUES(?,?)',(run_id,canonical(body)))
        return run_id

    def finish_run(self,run_id,status):
        with self.lock,self.db:
            row=self.db.execute('SELECT body FROM runs WHERE run_id=?',(run_id,)).fetchone()
            if row:
                body=json.loads(row[0]); body.update(status=status,updated_at=utcnow())
                self.db.execute('UPDATE runs SET body=? WHERE run_id=?',(canonical(body),run_id))

    def add_evidence(self,body):
        ref=body.get('evidence_ref') or 'evidence:'+digest(body)
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?)',(ref,canonical(body)))
        return ref

    def save(self,key,status,detail,records=(),run_id=None):
        iterator=iter(records); first=next(iterator,None)
        source=first.get('source',{}) if first else {}
        run_id=run_id or source.get('ingestion_run')
        run_id=self.start_run('task_observation',{'task_key':key},run_id=run_id)
        event_id='task:'+uuid.uuid4().hex; stamp=utcnow()
        event={'kind':'task_observation','ingestion_run':run_id,'task_key':key,'status':status,'detail':detail,'created_at':stamp}
        with self.lock,self.db:
            self.db.execute('INSERT INTO task_attempts VALUES(?,?,?,?,?,?)',(event_id,key,run_id,status,canonical(detail),stamp))
            self.db.execute('INSERT INTO evidence VALUES(?,?)',(event_id,canonical(event)))
            for record in chain([first],iterator) if first is not None else ():
                identity=record_key(record); record_id='record:'+digest(record)
                self.db.execute('INSERT OR IGNORE INTO record_versions VALUES(?,?,?)',(record_id,identity,canonical(record)))
                self.db.execute('INSERT OR IGNORE INTO record_links VALUES(?,?)',(record_id,event_id))
                artifact=record.get('source',{}).get('artifact_validation')
                if artifact:
                    artifact_ref='evidence:'+digest(artifact)
                    self.db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?)',(artifact_ref,canonical(artifact)))
                old=self.db.execute('SELECT body FROM records WHERE key=?',(identity,)).fetchone()
                if not old or semantic_hash(json.loads(old[0]))==semantic_hash(record):
                    self.db.execute('INSERT OR REPLACE INTO records VALUES(?,?)',(identity,canonical(record)))
            self.db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?,?,?)',(key,status,json.dumps(detail),utcnow()))
            run=json.loads(self.db.execute('SELECT body FROM runs WHERE run_id=?',(run_id,)).fetchone()[0])
            if run['kind']=='task_observation' and status!='running':
                run.update(status='FAILED' if status=='failed' else 'PARTIAL' if status in {'observed','validated','paused'} else 'UNVERIFIED',updated_at=utcnow())
                self.db.execute('UPDATE runs SET body=? WHERE run_id=?',(canonical(run),run_id))
        return event_id

    def export(self,path):
        """Atomically export a self-contained JSON package, not row-only JSONL.

        Raw versions and task attempts are retained even when projections fail.
        Strict shared records are records[*].contract_record; B inputs are in
        storage_batches. Their local publication counts are NOT response counts.
        """
        with self.lock,self.db:
            runs={row[0]:json.loads(row[1]) for row in self.db.execute('SELECT * FROM runs ORDER BY rowid')}
            evidence={row[0]:json.loads(row[1]) for row in self.db.execute('SELECT * FROM evidence ORDER BY rowid')}
            tasks=[{'event_id':r[0],'task_key':r[1],'run_id':r[2],'status':r[3],'detail':json.loads(r[4]),'created_at':r[5]} for r in self.db.execute('SELECT * FROM task_attempts ORDER BY rowid')]
            versions=[{'record_id':r[0],'record_key':r[1],'body':json.loads(r[2])} for r in self.db.execute('SELECT * FROM record_versions ORDER BY rowid')]
            task_map={t['event_id']:t for t in tasks}; version_map={v['record_id']:v for v in versions}
            links=[]
            for record_id,event_id in self.db.execute('SELECT * FROM record_links ORDER BY rowid'):
                t=task_map[event_id]; r=version_map[record_id]['body']; source=r.get('source',{})
                refs=[event_id]+[ref for ref in source.get('evidence_refs',[]) if ref in evidence]
                artifact=source.get('artifact_validation')
                if artifact: refs.append('evidence:'+digest(artifact))
                links.append({'record_id':record_id,'run_id':t['run_id'],'evidence_refs':list(dict.fromkeys(refs)),'detail':t['detail']})
            bundle=build_bundle(runs,tasks,evidence,versions,links)
            artifacts={}
            for entry in evidence.values():
                if entry['kind']=='archive_validation':
                    manifest=entry['checked']; uri=entry.get('storage_uri')
                elif entry['kind']=='archive_download':
                    manifest=entry['manifest']; uri=manifest.get('path')
                else: continue
                sha=manifest['sha256']
                if sha in artifacts and artifacts[sha].get('bytes_base64'): continue
                artifact={'sha256':sha,'storage_uri':uri,'status':'UNVERIFIED'}
                try:
                    data=Path(uri).read_bytes()
                    if hashlib.sha256(data).hexdigest()!=sha: raise ValueError('Archive changed since acquisition')
                    artifact.update(status='AVAILABLE',bytes=len(data),bytes_base64=base64.b64encode(data).decode())
                except (OSError,ValueError,TypeError) as exc: artifact['reason']=str(exc)
                artifacts[sha]=artifact
            bundle['artifacts']=artifacts
            for batch in bundle['storage_batches']:
                shas=batch['evidence']['source_archive_row_counts']
                batch['evidence']['acquisition_manifest']['artifacts']={sha:artifacts[sha] for sha in shas if sha in artifacts}
        destination=Path(path); destination.parent.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(prefix=destination.name+'.',suffix='.partial',dir=destination.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as out:
                json.dump(bundle,out,ensure_ascii=False,allow_nan=False,separators=(',',':'))
                out.flush(); os.fsync(out.fileno())
            os.replace(temp,destination)
        except BaseException:
            Path(temp).unlink(missing_ok=True); raise
        return {'path':str(destination),'records':len(bundle['records']),'evidence':len(evidence),'conflicts':len(bundle['conflicts'])}

    def window_records(self,category,symbol,dataset,interval,start,end):
        with self.lock:
            records=[json.loads(row[0]) for row in self.db.execute('SELECT body FROM records')]
        return {r['timestamp_ms']:r for r in records if (r['category'],r['symbol'],r['dataset'],r['interval'])==(category,symbol,dataset,interval) and start<=r['timestamp_ms']<end}

    def close(self): self.db.close()
