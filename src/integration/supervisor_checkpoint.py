"""Durable deduplication and single-cycle ownership, not a scheduler."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

REPOSITORY='YehorBabenko88/tester'
STATES={'NEW','IN_PROGRESS','BLOCKED','READY_FOR_REVIEW','CHANGES_REQUESTED','ACCEPTED'}


class ActiveCycle(RuntimeError):pass


def task_key(issue,head,defect):
    if type(issue) is not int or issue<=0 or not re.fullmatch('[a-f0-9]{40}',head) or not defect:
        raise ValueError('Invalid task identity')
    return hashlib.sha256(json.dumps([REPOSITORY,issue,head,defect],separators=(',',':')).encode()).hexdigest()


def remember_task(checkpoint,key,*,comment_id,status):
    if status not in STATES or type(comment_id) is not int or comment_id<=0:raise ValueError('Invalid task result')
    tasks=checkpoint.setdefault('tasks',{})
    if key in tasks:return False
    tasks[key]={'comment_id':comment_id,'status':status}
    return True


def _validate(value):
    if value.get('schema_version')!=1 or value.get('repository')!=REPOSITORY or not isinstance(value.get('tasks'),dict):
        raise ValueError('Incompatible supervisor checkpoint')
    return value


def load_checkpoint(path):
    return _validate(json.loads(Path(path).read_text(encoding='utf-8')))


def save_checkpoint(path,value):
    _validate(value);path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as output:
            json.dump(value,output,indent=2,ensure_ascii=False);output.write('\n');output.flush();os.fsync(output.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def cycle_lock(path,cycle_id):
    """Never steal an existing lock: stale ownership needs explicit audit."""
    path=Path(path);token=uuid.uuid4().hex
    try:
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError as error:
        raise ActiveCycle('Another or interrupted cycle owns this workspace; inspect lock before recovery') from error
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as output:
            json.dump({'cycle_id':cycle_id,'token':token,'pid':os.getpid()},output)
        yield
    finally:
        # Release only this invocation's token, never a replacement owner's lock.
        try:
            if json.loads(path.read_text(encoding='utf-8')).get('token')==token:path.unlink()
        except (FileNotFoundError,json.JSONDecodeError):pass

