"""Owned physical-test progress, not a service installer or administrator agent."""
import copy
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import uuid
from .supervisor_checkpoint import cycle_lock

PHASES={'PREFLIGHT','SNAPSHOT','INSTALL','START','TEST','DIAGNOSE','PATCH','RETEST','ROLLBACK','BLOCKED','ACCEPTED'}
GATES={'native_service','install','update','stop','crash_recovery','data_integrity','api','ui','ten_instruments','components','exact_head_ci'}


class JournalConflict(RuntimeError):pass
class RepairBlocked(RuntimeError):pass


class PhysicalJournal:
    def __init__(self,path,*,root,installation_id,host_fingerprint):
        self.root=Path(root).resolve();self.path=Path(path).resolve()
        if not self.path.is_relative_to(self.root):raise ValueError('Journal must be inside the owned root')
        if not installation_id or not host_fingerprint:raise ValueError('Owned installation and host identities required')
        self.installation_id=installation_id;self.host_fingerprint=host_fingerprint
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with cycle_lock(self.path.with_suffix('.lock'),'physical-journal-init'):
            if not self.path.exists():
                self._write(dict(schema_version=1,repository='YehorBabenko88/tester',installation_id=installation_id,
                    host_fingerprint=host_fingerprint,root=str(self.root),revision=0,phase='PREFLIGHT',events=[],
                    failed_repairs={},investigation_required=False))
            self._state=self._load()

    @property
    def state(self):return copy.deepcopy(self._state)

    def _load(self):
        value=json.loads(self.path.read_text(encoding='utf-8'))
        if (value.get('schema_version')!=1 or value.get('repository')!='YehorBabenko88/tester' or
            value.get('installation_id')!=self.installation_id or value.get('host_fingerprint')!=self.host_fingerprint or
            value.get('root')!=str(self.root) or value.get('phase') not in PHASES):
            raise JournalConflict('Existing journal belongs to another or incompatible installation')
        return value

    def _write(self,value):
        temporary=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
        with temporary.open('x',encoding='utf-8',newline='\n') as stream:
            json.dump(value,stream,indent=2,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,self.path)

    def _record(self,operation_id,phase,signature,repair,success):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',operation_id):raise ValueError('Invalid operation identity')
        if not re.fullmatch(r'[A-Z0-9_:-]{1,128}',signature):raise ValueError('Use a safe reason code, never raw diagnostics')
        if phase not in PHASES-{'ACCEPTED'}:raise ValueError('Unsupported observation phase')
        with cycle_lock(self.path.with_suffix('.lock'),'physical-journal-write'):
            current=self._load()
            if current['revision']!=self._state['revision']:raise JournalConflict('Stale writer must reload checkpoint')
            if any(e['operation_id']==operation_id for e in current['events']):return False
            failed=current['failed_repairs'].get(signature,0)
            if repair and failed>=3:raise RepairBlocked('Three failed repairs require a separate investigation before more repairs')
            if repair:
                current['failed_repairs'][signature]=0 if success else failed+1
                if not success and failed+1>=3:
                    phase='BLOCKED';current['investigation_required']=True
            current['events'].append(dict(operation_id=operation_id,phase=phase,signature=signature,
                kind='REPAIR_RESULT' if repair else 'OBSERVATION',success=success,
                at=datetime.now(timezone.utc).isoformat()))
            current['phase']=phase;current['revision']+=1
            self._write(current);self._state=current
            return True

    def observe(self,operation_id,phase,signature):return self._record(operation_id,phase,signature,False,None)

    def repair_result(self,operation_id,signature,*,success):
        if type(success) is not bool:raise ValueError('Explicit repair verification result required')
        return self._record(operation_id,'RETEST',signature,True,success)
