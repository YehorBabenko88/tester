"""Hash-addressed immutable object publication. Database manifests are committed last."""
import gzip
import hashlib
import os
import tempfile
from pathlib import Path


def verify_archive(manifest):
    try:
        p=Path(manifest['storage_uri'])
        if not p.is_file() or p.is_symlink() or p.stat().st_size!=manifest['bytes']: return False
        h=hashlib.sha256()
        with p.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
        return h.hexdigest()==manifest['sha256']
    except OSError: return False


def publish_archive(path,root):
    path=Path(path); root=Path(root).resolve(); root.mkdir(parents=True,exist_ok=True)
    if path.is_symlink(): raise ValueError('symlink source refused')
    h=hashlib.sha256(); size=0
    fd,temporary=tempfile.mkstemp(prefix='.partial-',dir=root)
    temporary=Path(temporary)
    try:
        with os.fdopen(fd,'wb') as output,path.open('rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''):
                h.update(block); size+=len(block); output.write(block)
            output.flush(); os.fsync(output.fileno())
        if not size: raise ValueError('empty archive')
        # CRC is verified through EOF before any manifest can refer to a gzip archive.
        with temporary.open('rb') as stream: magic=stream.read(2)
        if magic==b'\x1f\x8b':
            with gzip.open(temporary,'rb') as stream:
                while stream.read(1024*1024): pass
        sha=h.hexdigest(); dest=root/sha
        manifest=dict(storage_uri=str(dest),sha256=sha,bytes=size)
        if dest.exists():
            if not verify_archive(manifest): raise ValueError('existing content-addressed object is damaged')
        else:
            # Windows rename refuses an existing destination; POSIX hard-link gives the same no-clobber guarantee.
            try:
                if os.name=='nt': os.rename(temporary,dest)
                else: os.link(temporary,dest)
            except FileExistsError:
                if not verify_archive(manifest): raise ValueError('concurrent damaged object')
            if os.name!='nt':
                directory=os.open(root,os.O_RDONLY)
                try: os.fsync(directory)
                finally: os.close(directory)
        return manifest
    finally:
        temporary.unlink(missing_ok=True)


def repair_archive(downloaded_path,manifest):
    """Replace a damaged object only with independently verified identical original bytes.

    A changed source revision never overwrites the confirmed hash-addressed object.
    Caller records the repair incident/resolution through Store after revalidation.
    """
    candidate=dict(manifest,storage_uri=str(downloaded_path))
    if not verify_archive(candidate): raise ValueError('redownload does not match original archive hash/size')
    dest=Path(manifest['storage_uri'])
    if dest.name!=manifest['sha256'] or dest.is_symlink(): raise ValueError('repair requires a managed hash-addressed object')
    if verify_archive(manifest): return True
    dest.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.repair-',dir=dest.parent); temp=Path(temp)
    try:
        with os.fdopen(fd,'wb') as output,Path(downloaded_path).open('rb') as source:
            for block in iter(lambda:source.read(1024*1024),b''): output.write(block)
            output.flush(); os.fsync(output.fileno())
        if not verify_archive(dict(manifest,storage_uri=str(temp))): raise ValueError('redownload changed during repair')
        with temp.open('rb') as stream: magic=stream.read(2)
        if magic==b'\x1f\x8b':
            with gzip.open(temp,'rb') as stream:
                while stream.read(1024*1024): pass
        os.replace(temp,dest)
        if os.name!='nt':
            directory=os.open(dest.parent,os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        return verify_archive(manifest)
    finally: temp.unlink(missing_ok=True)
