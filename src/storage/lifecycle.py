"""Non-destructive lifecycle: evidence-gated dry runs, disk pause, snapshot scans.

No method deletes, VACUUM FULLs, truncates WAL, moves raw files or mutates source history.
Backup verification records describe an actual caller-performed restore drill, not a claim
that hashing a dump alone verifies a restore. Destructive policy execution is unavailable.
"""
import hashlib
import os
import shutil
import time
import uuid
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from .archives import verify_archive
from .models import digest, normalize, integer

CLASSES={'RAW_PROTECTED','VERIFIED_ARCHIVE','DERIVED_REBUILDABLE','CACHE','TEMP'}


def file_manifest(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file(): raise ValueError('regular nonsymlink file required')
    h=hashlib.sha256(); size=0
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block); size+=len(block)
    if not size: raise ValueError('empty lifecycle object')
    return dict(storage_uri=str(path.resolve()),sha256=h.hexdigest(),bytes=size)


class StorageLifecycle:
    def __init__(self,store,root):
        self.store=store; self.root=Path(root).resolve()
        if not self.root.is_dir(): raise ValueError('existing archive/cache root required')

    def register_object(self,object_id,retention_class,path,evidence_refs):
        if retention_class not in CLASSES: raise ValueError('unknown retention class')
        path=Path(path)
        if not path.resolve().is_relative_to(self.root) or path.is_symlink(): raise ValueError('object outside configured root')
        manifest=file_manifest(path); refs=sorted(set(evidence_refs))
        if not refs: raise ValueError('object requires authentic evidence references')
        row=dict(object_id=object_id,retention_class=retention_class,root_uri=str(self.root),canonical_uri=os.path.normcase(manifest['storage_uri']),**manifest)
        with self.store.connect() as c:
            self.store._lock(c,'lifecycle-path:'+row['canonical_uri'])
            self.store._lock(c,'lifecycle:'+object_id)
            alias=c.execute('SELECT object_id FROM tester_storage.lifecycle_objects WHERE canonical_uri=%s',(row['canonical_uri'],)).fetchone()
            if alias and alias['object_id']!=object_id: raise ValueError('path already registered; object aliases/reclassification refused')
            old=c.execute('SELECT object_id,retention_class,root_uri,storage_uri,canonical_uri,sha256,bytes FROM tester_storage.lifecycle_objects WHERE object_id=%s',(object_id,)).fetchone()
            if old and old!=row: raise ValueError('object identity/classification cannot be overwritten')
            c.execute('''INSERT INTO tester_storage.lifecycle_objects(object_id,retention_class,root_uri,storage_uri,canonical_uri,sha256,bytes)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',tuple(row[k] for k in ('object_id','retention_class','root_uri','storage_uri','canonical_uri','sha256','bytes')))
            for ref in refs: c.execute('INSERT INTO tester_storage.lifecycle_object_evidence VALUES(%s,%s) ON CONFLICT DO NOTHING',(object_id,ref))
        return row

    def reference_object(self,parent_id,child_id,evidence_refs):
        if not evidence_refs: raise ValueError('reference evidence required')
        with self.store.connect() as c:
            for ref in set(evidence_refs): c.execute('INSERT INTO tester_storage.lifecycle_references VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(parent_id,child_id,ref))

    def record_backup(self,backup_id,path,evidence_refs,*,restored,data_cutoff_ms,backup_start_ms,backup_end_ms,restore_start_ms,restore_end_ms):
        if type(restored) is not bool: raise ValueError('explicit actual restore result required')
        timings=(data_cutoff_ms,backup_start_ms,backup_end_ms,restore_start_ms,restore_end_ms)
        for value in timings: integer(value,'backup time')
        if list(timings)!=sorted(timings): raise ValueError('invalid backup/restore chronology')
        manifest=file_manifest(path); refs=sorted(set(evidence_refs))
        if not refs: raise ValueError('backup must identify restored source evidence')
        with self.store.connect() as c:
            self.store._lock(c,'backup-verification:'+backup_id)
            expected=dict(manifest=manifest,restored=restored,data_cutoff_ms=data_cutoff_ms,backup_start_ms=backup_start_ms,
                backup_end_ms=backup_end_ms,restore_start_ms=restore_start_ms,restore_end_ms=restore_end_ms,
                rpo_ms=backup_start_ms-data_cutoff_ms,rto_ms=restore_end_ms-restore_start_ms)
            old=c.execute('SELECT * FROM tester_storage.backup_verifications WHERE backup_id=%s',(backup_id,)).fetchone()
            if old and any(old[k]!=v for k,v in expected.items()): raise ValueError('backup verification identity conflicts')
            if old:
                old_refs=[r['evidence_id'] for r in c.execute('SELECT evidence_id FROM tester_storage.backup_evidence WHERE backup_id=%s ORDER BY evidence_id',(backup_id,))]
                if old_refs!=refs: raise ValueError('completed backup evidence set is immutable')
            c.execute('''INSERT INTO tester_storage.backup_verifications(backup_id,manifest,restored,data_cutoff_ms,backup_start_ms,
                backup_end_ms,restore_start_ms,restore_end_ms,rpo_ms,rto_ms) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (backup_id,Jsonb(manifest),restored,*timings,expected['rpo_ms'],expected['rto_ms']))
            for ref in refs: c.execute('INSERT INTO tester_storage.backup_evidence VALUES(%s,%s) ON CONFLICT DO NOTHING',(backup_id,ref))
        return dict(backup_id=backup_id,**expected)

    def acquisition_gate(self,min_free_bytes=1024**3):
        integer(min_free_bytes,'minimum free bytes')
        free=shutil.disk_usage(self.root)[2]
        allowed=free>=min_free_bytes
        return dict(allowed=allowed,state='READY' if allowed else 'PAUSED_LOW_DISK',free_bytes=free,min_free_bytes=min_free_bytes,root_uri=str(self.root))

    def guarded_fetch(self,fetcher,min_free_bytes=1024**3):
        gate=self.acquisition_gate(min_free_bytes)
        return fetcher() if gate['allowed'] else gate

    def plan(self):
        with self.store.connect() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            rows=c.execute('''SELECT o.*,
                ARRAY(SELECT evidence_id FROM tester_storage.lifecycle_object_evidence e WHERE e.object_id=o.object_id ORDER BY evidence_id) AS evidence_refs,
                EXISTS(SELECT 1 FROM tester_storage.lifecycle_references r WHERE r.child_id=o.object_id) AS referenced,
                EXISTS(SELECT 1 FROM tester_storage.lifecycle_quarantine q WHERE q.object_id=o.object_id) AS quarantined
                FROM tester_storage.lifecycle_objects o WHERE root_uri=%s ORDER BY object_id''',(str(self.root),)).fetchall()
            backups=c.execute('''SELECT b.*,ARRAY(SELECT evidence_id FROM tester_storage.backup_evidence e WHERE e.backup_id=b.backup_id ORDER BY evidence_id) AS evidence_refs
                FROM tester_storage.backup_verifications b WHERE restored ORDER BY backup_id''').fetchall()
            archives=c.execute('SELECT storage_uri FROM tester_storage.source_archives').fetchall()
        archive_paths={a['storage_uri'] for a in archives}; objects=[]
        for row in rows:
            kind=row['retention_class']; refs=row['evidence_refs']
            backup=next((b for b in backups if set(refs)<=set(b['evidence_refs']) and verify_archive(b['manifest'])),None)
            if kind=='RAW_PROTECTED': reason='raw_protected'
            elif kind=='VERIFIED_ARCHIVE' or row['storage_uri'] in archive_paths: reason='archive_protected'
            elif row['quarantined'] or not verify_archive(row): reason='quarantined'
            elif row['referenced']: reason='referenced'
            elif not refs: reason='missing_source_evidence'
            elif backup is None: reason='missing_verified_backup'
            else: reason='eligible_dry_run_only'
            objects.append(dict(object_id=row['object_id'],retention_class=kind,estimated_bytes=row['bytes'],
                evidence_refs=refs,backup_id=backup['backup_id'] if backup else None,
                eligible=reason=='eligible_dry_run_only',reason=reason))
        plan=dict(mode='DRY_RUN',policy_version='lifecycle-1',objects=objects,
            estimated_reclaimable_bytes=sum(o['estimated_bytes'] for o in objects if o['eligible']),
            raw_policy='ALWAYS_PROTECTED',execution_enabled=False)
        plan_id=digest(plan); plan['plan_id']=plan_id
        with self.store.connect() as c:
            c.execute("INSERT INTO tester_storage.lifecycle_plans(plan_id,mode,plan) VALUES(%s,'DRY_RUN',%s) ON CONFLICT DO NOTHING",(plan_id,Jsonb(plan)))
        return plan

    def execute(self,plan_id):
        raise PermissionError('destructive lifecycle execution is unavailable; separate approved policy required')

    def metrics(self):
        with self.store.connect() as c:
            tables=c.execute('''SELECT relname AS "table",pg_total_relation_size(relid) AS bytes,n_live_tup AS rows_estimate,
                last_autovacuum::text,last_autoanalyze::text FROM pg_stat_user_tables WHERE schemaname='tester_storage' ORDER BY relname''').fetchall()
            last=c.execute('SELECT scan_id,created_at::text,report FROM tester_storage.lifecycle_scans ORDER BY created_at DESC,scan_id DESC LIMIT 1').fetchone()
            backups=c.execute('SELECT backup_id,restored,rpo_ms,rto_ms,created_at::text FROM tester_storage.backup_verifications ORDER BY created_at DESC LIMIT 10').fetchall()
            settings=c.execute("SELECT name,setting FROM pg_settings WHERE name IN ('autovacuum','checkpoint_timeout','max_wal_size','min_wal_size','fsync','synchronous_commit')").fetchall()
            wal=None; wal_reason=None
            try:
                with c.transaction(): wal=c.execute('SELECT coalesce(sum(size),0)::bigint AS bytes FROM pg_ls_waldir()').fetchone()['bytes']
            except psycopg.Error: wal_reason='insufficient_wal_monitor_privilege'
        return dict(tables=tables,wal_bytes=wal,wal_unavailable_reason=wal_reason,free_bytes=shutil.disk_usage(self.root)[2],
                    root_uri=str(self.root),last_scan=last,backups=backups,settings=settings)

    def scan(self,max_records=10000):
        integer(max_records,'scan record bound')
        if not 1<=max_records<=100000: raise ValueError('scan bound must be 1..100000')
        findings=[]; began=time.monotonic(); scan_id=uuid.uuid4().hex
        with self.store.connect() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            rows=c.execute('''SELECT s.source_id,s.source,s.raw_envelope,r.* FROM tester_storage.record_sources s
                JOIN tester_storage.raw_records r USING(record_key) ORDER BY source_id LIMIT %s''',(max_records+1,)).fetchall()
            truncated=len(rows)>max_records
            for row in rows[:max_records]:
                try:
                    n=normalize(row['raw_envelope'])
                    fields=('record_key','content_hash','payload','timestamp_ms','timestamp_ns','timestamp_us',
                            'timestamp_precision','trade_id','id_origin','venue','category','symbol','dataset','interval')
                    if any(n.get(k)!=row[k] for k in fields):
                        findings.append(dict(code='raw_identity_or_content_mismatch',object_id=row['record_key']))
                    if n['source']!=row['source'] or digest([n['record_key'],n['source'],row['raw_envelope']])!=row['source_id']:
                        findings.append(dict(code='source_identity_hash_mismatch',object_id=row['source_id']))
                except (ValueError,TypeError): findings.append(dict(code='raw_envelope_invalid',object_id=row['record_key']))
            for row in c.execute('''SELECT r.record_key FROM tester_storage.raw_records r WHERE NOT EXISTS
                (SELECT 1 FROM tester_storage.record_sources s WHERE s.record_key=r.record_key) LIMIT %s''',(max_records,)):
                findings.append(dict(code='raw_without_source',object_id=row['record_key']))
            for row in c.execute('''SELECT s.source_id FROM tester_storage.record_sources s WHERE NOT EXISTS
                (SELECT 1 FROM tester_storage.source_observations o WHERE o.source_id=s.source_id) LIMIT %s''',(max_records,)):
                findings.append(dict(code='legacy_source_without_run_evidence',object_id=row['source_id']))
            objects=c.execute('SELECT * FROM tester_storage.lifecycle_objects WHERE root_uri=%s',(str(self.root),)).fetchall()
            archives=c.execute('SELECT archive_id AS object_id,storage_uri,sha256,bytes FROM tester_storage.source_archives').fetchall()
            for row in objects+archives:
                if not verify_archive(row): findings.append(dict(code='object_checksum_mismatch',object_id=row['object_id']))
            for row in c.execute('''SELECT p.evidence_id,p.evidence,p.record_count,p.status FROM tester_storage.pagination_evidence p ORDER BY evidence_id LIMIT %s''',(max_records,)):
                from .store import validate_evidence
                try:
                    _,count=validate_evidence(row['evidence'])
                    if count!=row['record_count']: raise ValueError('page count')
                except (ValueError,TypeError): findings.append(dict(code='pagination_evidence_chain_invalid',object_id=row['evidence_id']))
        report=dict(scan_id=scan_id,findings=findings,records_checked=min(len(rows),max_records),
                    truncated=truncated,elapsed_ms=round((time.monotonic()-began)*1000),snapshot='REPEATABLE_READ',raw_mutations=0)
        with self.store.connect() as c:
            c.execute('INSERT INTO tester_storage.lifecycle_scans(scan_id,report) VALUES(%s,%s)',(scan_id,Jsonb(report)))
            for f in findings:
                c.execute('INSERT INTO tester_storage.lifecycle_quarantine VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(f['object_id'],scan_id,f['code']))
        return report
