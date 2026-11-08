"""Portable immutable research artifacts. B owns canonical database persistence."""
import json
import os
import tempfile
from pathlib import Path
from src.strategy.dsl import digest
from .engine import ENGINE_VERSION


def save_artifact(result,strategy,snapshot,directory):
    if result['strategy_hash']!=digest(strategy) or result['market_snapshot_hash']!=digest(snapshot):
        raise ValueError('artifact inputs do not match result')
    payload={'schema_version':'1.0','strategy':strategy,'market_snapshot':snapshot,'result':result}
    envelope={'sha256':digest(payload),'payload':payload}
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    path=directory/(result['run_id']+'.json')
    data=json.dumps(envelope,sort_keys=True,indent=2,allow_nan=False)+'\n'
    if path.exists():
        if load_artifact(path)!=payload: raise ValueError('artifact ID collision')
        return str(path.resolve())
    fd,temp=tempfile.mkstemp(prefix='.strategy-',suffix='.json',dir=directory)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        # Hard-link is atomic create-if-absent, preserving immutable run IDs.
        try: os.link(temp,path)
        except FileExistsError:
            if load_artifact(path)!=payload: raise ValueError('artifact ID collision')
    finally:
        os.unlink(temp)
    return str(path.resolve())


def load_artifact(path):
    envelope=json.loads(Path(path).read_text(encoding='utf-8'))
    payload=envelope['payload']
    if envelope['sha256']!=digest(payload): raise ValueError('artifact integrity failure')
    r=payload['result']
    if r['engine_version']!=ENGINE_VERSION or r['strategy_hash']!=digest(payload['strategy']) or r['market_snapshot_hash']!=digest(payload['market_snapshot']):
        raise ValueError('artifact version/input mismatch')
    return payload
