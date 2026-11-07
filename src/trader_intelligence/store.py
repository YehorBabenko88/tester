"""Transactional H persistence. Caller supplies tester-only DSN; no database creation."""
from pathlib import Path
from hashlib import sha256
import psycopg
from psycopg.types.json import Jsonb
from .imports import validate_dataset


class Store:
    def __init__(self, dsn):
        self.dsn=dsn

    def connection(self):
        return psycopg.connect(self.dsn,connect_timeout=5,
                               options='-c statement_timeout=15000 -c lock_timeout=10000')

    def migrate(self):
        ddl=Path(__file__).with_name('schema.sql').read_text().replace('\r\n','\n')
        checksum=sha256(ddl.encode()).hexdigest()
        with self.connection() as c:
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended('tester_trader:migrations',0))")
            exists=c.execute("SELECT to_regclass('tester_trader.schema_version')").fetchone()[0]
            if exists:
                old=c.execute('SELECT checksum FROM tester_trader.schema_version').fetchone()
                if old and old[0]!=checksum: raise ValueError('H migration checksum mismatch')
            c.execute(ddl)
            c.execute('INSERT INTO tester_trader.schema_version(checksum) VALUES(%s) ON CONFLICT DO NOTHING',(checksum,))

    def snapshot(self,c,tenant,revision):
        row=c.execute('''SELECT d.payload,s.revoked FROM tester_trader.snapshots d
          JOIN tester_trader.trader_sources s USING(tenant,source)
          WHERE d.tenant=%s AND d.dataset_revision=%s FOR SHARE OF s''',(tenant,revision)).fetchone()
        if not row: raise LookupError('snapshot not found')
        if row[1]: raise PermissionError('source revoked; dependent artifacts invalid')
        return row[0]

    def put(self,tenant,dataset):
        validate_dataset(dataset)
        m=dataset['manifest']
        with self.connection() as c:
            c.execute('INSERT INTO tester_trader.trader_sources(tenant,source) VALUES(%s,%s) ON CONFLICT DO NOTHING',(tenant,m['source']))
            revoked=c.execute('SELECT revoked FROM tester_trader.trader_sources WHERE tenant=%s AND source=%s FOR UPDATE',(tenant,m['source'])).fetchone()[0]
            if revoked: raise PermissionError('source revoked')
            previous=c.execute('SELECT dataset_revision FROM tester_trader.snapshots WHERE tenant=%s AND source=%s AND source_revision=%s',
                               (tenant,m['source'],m['source_revision'])).fetchone()
            if previous and previous[0]!=dataset['dataset_revision']: raise ValueError('immutable source revision conflict')
            c.execute('INSERT INTO tester_trader.snapshots VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                      (tenant,dataset['dataset_revision'],m['source'],m['source_revision'],Jsonb(dataset)))
        return dataset['dataset_revision']

    def revoke(self,tenant,source):
        with self.connection() as c:
            if not c.execute('UPDATE tester_trader.trader_sources SET revoked=true WHERE tenant=%s AND source=%s RETURNING source',
                             (tenant,source)).fetchone(): raise LookupError('source not found')

    def save_run(self,c,tenant,request_id,request_digest,revision,result):
        c.execute('INSERT INTO tester_trader.analysis_runs VALUES(%s,%s,%s,%s,%s)',
                  (tenant,request_id,request_digest,revision,Jsonb(result)))
