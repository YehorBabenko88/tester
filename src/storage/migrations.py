"""Checksummed, serialized migrations. Caller controls transaction/DDL privileges."""
import hashlib
from pathlib import Path

MIGRATIONS=Path(__file__).with_name('migrations')

def migrate(conn, directory=MIGRATIONS):
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('tester_storage:migrations',0))")
        conn.execute('CREATE SCHEMA IF NOT EXISTS tester_storage')
        conn.execute('''CREATE TABLE IF NOT EXISTS tester_storage.schema_migrations(
            version integer PRIMARY KEY, name text NOT NULL, checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now())''')
        applied=dict(conn.execute('SELECT version,checksum FROM tester_storage.schema_migrations').fetchall())
        files=sorted(Path(directory).glob('[0-9]*_*.sql'))
        versions={int(p.name.split('_')[0]) for p in files}
        if set(applied)-versions: raise ValueError('database migration version is newer than this client')
        for path in files:
            version=int(path.name.split('_')[0]); content=path.read_bytes()
            # Git may check out CRLF on Windows; checksums are platform independent.
            content=content.replace(b'\r\n',b'\n')
            checksum=hashlib.sha256(content).hexdigest()
            if version in applied:
                if applied[version]!=checksum: raise ValueError('migration checksum mismatch')
                continue
            if any(v>version for v in applied): raise ValueError('out-of-order migration')
            conn.execute(content.decode('utf-8'))
            conn.execute('INSERT INTO tester_storage.schema_migrations(version,name,checksum) VALUES(%s,%s,%s)',
                         (version,path.name,checksum))
