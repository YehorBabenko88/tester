import copy
import os
import unittest
import uuid
import tempfile
import shutil
from pathlib import Path
from decimal import Decimal
import psycopg
from psycopg import sql
from tests.storage.test_models import trade

try:
    from src.storage.migrations import migrate
    from src.storage.store import Store, DataConflict
except ImportError:
    migrate=Store=DataConflict=None


def evidence(start=1700000000000,end=1700000060000,**changes):
    r=dict(venue='bybit',category='linear',symbol='BTCUSDT',dataset='trades',interval='',
           start_ms=start,end_ms=end,status='EXHAUSTED',
           pages=[dict(params={'start':start,'end':end-1},cursor=None,next_cursor=None,count=1)],
           available_range=None)
    r.update(changes)
    return r


@unittest.skipUnless(os.getenv('TESTER_TEST_ADMIN_DSN'),'requires isolated PostgreSQL TESTER_TEST_ADMIN_DSN')
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin=os.environ['TESTER_TEST_ADMIN_DSN']
        cls.database='tester_b_test_'+uuid.uuid4().hex[:16]
        with psycopg.connect(cls.admin,autocommit=True) as c:
            c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.database)))
        cls.dsn=psycopg.conninfo.make_conninfo(cls.admin,dbname=cls.database)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.admin,autocommit=True) as c:
            c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(cls.database)))

    def setUp(self):
        self.assertIsNotNone(migrate,'SQL migrations not implemented')
        with psycopg.connect(self.dsn) as c: migrate(c)
        self.store=Store(self.dsn)
        self.symbol='B'+uuid.uuid4().hex[:12]

    def record(self,**changes): return trade(symbol=self.symbol,**changes)
    def ev(self,**changes): return evidence(symbol=self.symbol,**changes)

    def count(self,table):
        with psycopg.connect(self.dsn) as c:
            return c.execute(sql.SQL('SELECT count(*) FROM tester_storage.{} WHERE symbol=%s').format(sql.Identifier(table)),(self.symbol,)).fetchone()[0]

    def test_migrations_repeat_and_checksum(self):
        with psycopg.connect(self.dsn) as c:
            migrate(c); migrate(c)
            self.assertGreater(c.execute('SELECT count(*) FROM tester_storage.schema_migrations').fetchone()[0],0)
            c.execute("UPDATE tester_storage.schema_migrations SET checksum='wrong' WHERE version=1")
            with self.assertRaises(ValueError): migrate(c)
            c.rollback()

    def test_atomic_replay_precise_read_and_provenance(self):
        self.store.ingest([self.record()],self.ev(),job_key=self.symbol,checkpoint={'cursor':'done'})
        replay=self.record(); replay['source']['fetched_at']='2026-11-07T13:00:00Z'
        self.store.ingest([replay],self.ev(),job_key=self.symbol,checkpoint={'cursor':'done'})
        rows=self.store.read('trades','bybit','linear',self.symbol,1700000000000,1700000060000)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['payload']['price'],'123.000000000000000001')
        self.assertIsNone(rows[0]['timestamp_ns'])
        self.assertEqual(self.count('record_sources'),2)
        self.assertEqual(self.store.checkpoint(self.symbol),{'cursor':'done'})

    def test_conflicting_replay_preserves_record_and_finding(self):
        self.store.ingest([self.record()],self.ev(),job_key=self.symbol)
        changed=self.record(); changed['payload']['price']='999'
        with self.assertRaises(DataConflict): self.store.ingest([changed],self.ev(),job_key=self.symbol)
        self.assertEqual(self.count('raw_records'),1)
        self.assertEqual(self.count('quality_findings'),1)
        self.assertFalse(self.store.coverage('bybit','linear',self.symbol,'trades','',1700000000000,1700000060000)['complete'])

    def test_failed_stream_rolls_back_all_and_checkpoint(self):
        self.store.ingest([self.record()],self.ev(),job_key=self.symbol,checkpoint={'offset':1})
        def interrupted():
            r=self.record(); r['payload']['trade_id']='second'; yield r
            raise RuntimeError('synthetic interruption')
        with self.assertRaises(RuntimeError): self.store.ingest(interrupted(),self.ev(),job_key=self.symbol,checkpoint={'offset':2})
        self.assertEqual(self.count('raw_records'),1)
        self.assertEqual(self.store.checkpoint(self.symbol),{'offset':1})

    def test_empty_exhausted_and_bad_page_chain_never_complete(self):
        ev=self.ev(status='EMPTY',pages=[dict(params={},cursor=None,next_cursor=None,count=0)])
        self.store.ingest([],ev,job_key=self.symbol)
        self.assertFalse(self.store.coverage('bybit','linear',self.symbol,'trades','',ev['start_ms'],ev['end_ms'])['complete'])
        bad=self.ev(pages=[dict(params={},cursor=None,next_cursor='more',count=1)])
        with self.assertRaises(ValueError): self.store.ingest([self.record()],bad,job_key=self.symbol+'bad')

    def test_identity_cannot_change_day_or_market(self):
        self.store.ingest([self.record()],self.ev(),job_key=self.symbol)
        with self.assertRaises(DataConflict):
            self.store.ingest([self.record(timestamp_ms=1700086400123)],self.ev(start_ms=1700086400000,end_ms=1700086460000),job_key=self.symbol+'2')
        r=self.record(category='spot')
        self.store.ingest([r],self.ev(category='spot'),job_key=self.symbol+'spot')
        self.assertEqual(self.count('raw_records'),2)

    def test_bad_scope_range_or_count_rolls_back(self):
        for ev in [self.ev(dataset='funding'),self.ev(end_ms=1700000000100),self.ev(pages=[dict(params={},cursor=None,next_cursor=None,count=2)])]:
            with self.assertRaises(ValueError): self.store.ingest([self.record()],ev,job_key=self.symbol)
        self.assertEqual(self.count('raw_records'),0)

    def test_verified_requires_available_evidence_and_recovery_supersedes_damage(self):
        ev=self.ev(available_range=dict(start_ms=1700000000000,end_ms=1700000060000,
             method='operator_attestation',source_url='https://example.test/manifest',sha256='a'*64))
        self.store.ingest([self.record()],ev,job_key=self.symbol)
        self.assertTrue(self.store.coverage('bybit','linear',self.symbol,'trades','',ev['start_ms'],ev['end_ms'])['complete'])
        finding=self.store.mark_range(ev,'corrupt','checksum mismatch')
        self.assertFalse(self.store.coverage('bybit','linear',self.symbol,'trades','',ev['start_ms'],ev['end_ms'])['complete'])
        jobs=self.store.recovery_jobs('bybit','linear',self.symbol,'trades','')
        self.assertEqual(len(jobs),1)
        ev['resolves']=[finding]
        self.store.ingest([self.record()],ev,job_key=jobs[0]['job_key'])
        self.assertTrue(self.store.coverage('bybit','linear',self.symbol,'trades','',ev['start_ms'],ev['end_ms'])['complete'])

    def test_sql_constraints_and_append_only(self):
        self.store.ingest([self.record()],self.ev(),job_key=self.symbol)
        with psycopg.connect(self.dsn) as c:
            value=c.execute('SELECT price FROM tester_storage.raw_trades WHERE symbol=%s',(self.symbol,)).fetchone()[0]
            self.assertEqual(value,Decimal('123.000000000000000001'))
            with self.assertRaises(psycopg.Error):
                c.execute('UPDATE tester_storage.raw_records SET timestamp_ms=0 WHERE symbol=%s',(self.symbol,))

    def test_verified_retry_can_supersede_empty_unknown_interval(self):
        empty=self.ev(status='EMPTY',pages=[dict(params={},cursor=None,next_cursor=None,count=0)])
        self.store.ingest([],empty,job_key=self.symbol)
        ev=self.ev(available_range=dict(start_ms=1700000000000,end_ms=1700000060000,method='operator_attestation',source_url='https://example.test/range',sha256='a'*64))
        self.store.ingest([self.record()],ev,job_key=self.symbol)
        self.assertTrue(self.store.coverage('bybit','linear',self.symbol,'trades','',ev['start_ms'],ev['end_ms'])['complete'])

    def test_keyset_read_does_not_drop_equal_timestamp_trades(self):
        first=self.record(); second=self.record(); second['payload']['trade_id']='another-id'
        ev=self.ev(pages=[dict(params={},cursor=None,next_cursor=None,count=2)])
        self.store.ingest([first,second],ev,job_key=self.symbol)
        rows=self.store.read('trades','bybit','linear',self.symbol,ev['start_ms'],ev['end_ms'],limit=1)
        tail=self.store.read('trades','bybit','linear',self.symbol,ev['start_ms'],ev['end_ms'],after=[rows[0]['timestamp_ms'],rows[0]['record_key']])
        self.assertEqual(len(tail),1)
        self.assertNotEqual(tail[0]['trade_id'],rows[0]['trade_id'])

    def test_trade_interval_cannot_create_shadow_coverage(self):
        self.store.ingest([self.record()],self.ev(),job_key=self.symbol)
        with self.assertRaises(ValueError):
            self.store.ingest([self.record(interval='1')],self.ev(interval='1'),job_key=self.symbol+'shadow')

    def test_sparse_observations_never_verify_available_range(self):
        for dataset,interval in [('open_interest','5min'),('ratio','5min'),('funding','')]:
            ev=self.ev(dataset=dataset,interval=interval,available_range=dict(start_ms=1700000000000,end_ms=1700000060000,method='operator_attestation',source_url='https://example.test/range',sha256='a'*64))
            r=self.record(dataset=dataset,interval=interval,payload={'value':'0.01'})
            self.assertFalse(self.store.ingest([r],ev,job_key=self.symbol+dataset)['verified'])

    def test_complete_periodic_observations_and_funding_inventory(self):
        for dataset,interval in [('open_interest','5min'),('ratio','5min'),('funding','')]:
            available=dict(start_ms=0,end_ms=600000,method='operator_attestation',source_url='https://example.test/inventory',sha256='a'*64)
            if dataset=='funding': available['expected_timestamps']=[0,300000]
            ev=self.ev(start_ms=0,end_ms=600000,dataset=dataset,interval=interval,available_range=available,
                       pages=[dict(params={},cursor=None,next_cursor=None,count=2)])
            records=[self.record(dataset=dataset,interval=interval,timestamp_ms=t,payload={'value':'0.01'}) for t in (0,300000)]
            self.assertTrue(self.store.ingest(records,ev,job_key=self.symbol+dataset)['verified'])

    def test_upgrade_preserves_version_one_raw_data(self):
        database='tester_b_upgrade_'+uuid.uuid4().hex[:12]
        target=psycopg.conninfo.make_conninfo(self.admin,dbname=database)
        with psycopg.connect(self.admin,autocommit=True) as c:
            c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                shutil.copy(Path(__file__).parents[2]/'src/storage/migrations/001_initial.sql',tmp)
                with psycopg.connect(target) as c: migrate(c,directory=tmp)
            # Seed a genuine v1-format record before applying later migrations;
            # a current writer must not be used against an unmigrated schema.
            from src.storage.models import normalize,digest
            from psycopg.types.json import Jsonb
            raw=self.record(); n=normalize(raw)
            with psycopg.connect(target) as c:
                c.execute('INSERT INTO tester_storage.instruments(venue,category,symbol) VALUES(%s,%s,%s)',('bybit','linear',self.symbol))
                c.execute('''INSERT INTO tester_storage.raw_records(record_key,venue,category,symbol,dataset,interval,schema_version,
                    timestamp_ms,timestamp_precision,trade_id,id_origin,payload,content_hash)
                    VALUES(%s,'bybit','linear',%s,'trades','','1.0',%s,'ms',%s,'exchange',%s,%s)''',
                    (n['record_key'],self.symbol,n['timestamp_ms'],n['trade_id'],Jsonb(n['payload']),n['content_hash']))
                c.execute('INSERT INTO tester_storage.record_sources VALUES(%s,%s,%s,%s,%s,now())',
                    (digest([n['record_key'],n['source'],raw]),n['record_key'],self.symbol,Jsonb(n['source']),Jsonb(raw)))
            # A historical v1 row and its unattested original source are
            # immutable across the 001 -> 008 upgrade, including exact Decimal
            # payloads, trade identity and original source JSON bytes.
            with psycopg.connect(target) as c:
                raw_before=c.execute('''SELECT record_key,trade_id,id_origin,
                    timestamp_ms,payload,content_hash FROM tester_storage.raw_records
                    WHERE record_key=%s''',(n['record_key'],)).fetchone()
                sources_before=c.execute('''SELECT source_id,record_key,symbol,
                    source,raw_envelope FROM tester_storage.record_sources
                    WHERE record_key=%s ORDER BY source_id''',(n['record_key'],)).fetchall()
                self.assertIsNotNone(raw_before)
                self.assertEqual(len(sources_before),1)
            with psycopg.connect(target) as c:
                migrate(c)
                self.assertEqual(c.execute('SELECT count(*) FROM tester_storage.raw_records').fetchone()[0],1)
                self.assertEqual(c.execute('SELECT max(version) FROM tester_storage.schema_migrations').fetchone()[0],8)
                raw_after=c.execute('''SELECT record_key,trade_id,id_origin,
                    timestamp_ms,payload,content_hash FROM tester_storage.raw_records
                    WHERE record_key=%s''',(n['record_key'],)).fetchone()
                sources_after=c.execute('''SELECT source_id,record_key,symbol,
                    source,raw_envelope FROM tester_storage.record_sources
                    WHERE record_key=%s ORDER BY source_id''',(n['record_key'],)).fetchall()
                self.assertEqual(raw_after,raw_before)
                self.assertEqual(sources_after,sources_before)
                self.assertEqual(raw_after[0],n['record_key'])
                self.assertEqual(raw_after[1],n['trade_id'])
                self.assertEqual(raw_after[4],n['payload'])
                self.assertEqual(raw_after[5],n['content_hash'])
                constraints=dict(c.execute('''SELECT conname,pg_get_constraintdef(oid)
                    FROM pg_constraint WHERE conrelid='tester_storage.raw_records'::regclass
                    AND conname IN ('raw_records_check2','trade_side_required')''').fetchall())
                self.assertEqual(set(constraints),{'raw_records_check2','trade_side_required'})
                for definition in constraints.values():
                    self.assertIn('Unknown',definition)
                    self.assertIn('Buy',definition)
                    self.assertIn('Sell',definition)
            provenance=Store(target).provenance([n['record_key']])[n['record_key']]
            self.assertFalse(provenance['authentic'])
            self.assertEqual(len(provenance['unlinked_sources']),1)
        finally:
            with psycopg.connect(self.admin,autocommit=True) as c:
                c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))

    def test_late_retry_cannot_regress_checkpoint_or_clear_it_implicitly(self):
        self.store.ingest([self.record()],self.ev(),self.symbol,checkpoint={'offset':2})
        with self.assertRaises(ValueError):
            self.store.ingest([self.record()],self.ev(),self.symbol,checkpoint={'offset':1})
        self.store.ingest([self.record()],self.ev(),self.symbol)
        self.assertEqual(self.store.checkpoint(self.symbol),{'offset':2})

    def test_opaque_cursor_changes_require_monotonic_sequence(self):
        self.store.ingest([self.record()],self.ev(),self.symbol,checkpoint={'sequence':1,'cursor':'page2'})
        with self.assertRaises(ValueError):
            self.store.ingest([self.record()],self.ev(),self.symbol,checkpoint={'sequence':1,'cursor':'page1'})
        self.store.ingest([self.record()],self.ev(),self.symbol,checkpoint={'sequence':2,'cursor':'done'})
        self.assertEqual(self.store.checkpoint(self.symbol),{'sequence':2,'cursor':'done'})


if __name__=='__main__': unittest.main()
