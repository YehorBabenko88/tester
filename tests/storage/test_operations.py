import os
import tempfile
import unittest
import uuid
from pathlib import Path
import psycopg
from psycopg import sql
from tests.storage.test_models import trade
from tests.storage import test_postgres as support

try:
    from src.storage.archives import publish_archive, verify_archive, repair_archive
    from src.storage.bootstrap import bootstrap, SecretStore
except ImportError:
    publish_archive=verify_archive=repair_archive=bootstrap=SecretStore=None


class ArchiveTests(unittest.TestCase):
    def test_content_addressed_files_and_tamper_detection(self):
        self.assertIsNotNone(publish_archive,'archive publication not implemented')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source'; path.write_bytes(b'original raw archive bytes')
            result=publish_archive(path,Path(tmp)/'objects')
            self.assertEqual(result['bytes'],26)
            self.assertIn(result['sha256'],result['storage_uri'])
            self.assertTrue(verify_archive(result))
            self.assertEqual(publish_archive(path,Path(tmp)/'objects'),result)
            Path(result['storage_uri']).write_bytes(b'corrupted')
            self.assertFalse(verify_archive(result))
            with self.assertRaises(ValueError): publish_archive(path,Path(tmp)/'objects')
            self.assertTrue(repair_archive(path,result))
            self.assertTrue(verify_archive(result))
            path.write_bytes(b'different source version')
            with self.assertRaises(ValueError): repair_archive(path,result)


class MemorySecrets:
    """Test-local secret store; never used by production bootstrap."""
    def __init__(self): self.values={}
    def get(self,key): return self.values.get(key)
    def put(self,key,value): self.values[key]=value


@unittest.skipUnless(os.getenv('TESTER_TEST_ADMIN_DSN'),'requires PostgreSQL')
class ProvisionTests(unittest.TestCase):
    def test_minimal_runtime_repeat_and_refuse_foreign_objects(self):
        self.assertIsNotNone(bootstrap,'safe bootstrap not implemented')
        suffix=uuid.uuid4().hex[:12]; database='tester_b_provision_'+suffix
        owner='tester_b_owner_'+suffix; runtime='tester_b_runtime_'+suffix
        secrets=MemorySecrets(); admin=os.environ['TESTER_TEST_ADMIN_DSN']
        try:
            result=bootstrap(admin,database,owner,runtime,secrets)
            credentials=dict(secrets.values)
            self.assertEqual(result['database'],database)
            bootstrap(admin,database,owner,runtime,secrets)
            self.assertEqual(credentials,secrets.values)
            params=psycopg.conninfo.conninfo_to_dict(admin)
            params.update(dbname=database,user=runtime,password=secrets.get(result['runtime_secret_key']))
            with psycopg.connect(**params) as c:
                self.assertFalse(c.execute('SELECT rolcreatedb OR rolcreaterole OR rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()[0])
                self.assertFalse(c.execute("SELECT has_database_privilege(current_user,current_database(),'CREATE')").fetchone()[0])
                c.execute('SELECT count(*) FROM tester_storage.raw_trades')
                with self.assertRaises(psycopg.errors.InsufficientPrivilege): c.execute('CREATE TABLE tester_storage.bad(a int)')
            params.update(user=runtime)
            from src.storage.store import Store
            from tests.storage.test_postgres import evidence
            Store(psycopg.conninfo.make_conninfo(**params)).ingest([trade()],evidence(),job_key='runtime-test')
            with psycopg.connect(admin,autocommit=True) as c:
                foreign='tester_b_foreign_'+suffix
                c.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(foreign)))
                with self.assertRaises(ValueError): bootstrap(admin,database,foreign,runtime,secrets)
                c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(foreign)))
        finally:
            with psycopg.connect(admin,autocommit=True) as c:
                c.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(database)))
                c.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(runtime)))
                c.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(owner)))


class StorageOperationsTests(support.PostgresTests):
    # Reuse lifecycle helpers without rerunning the base suite in this module.
    def test_archives_overlap_and_missing_file_blocks_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'archive'; f.write_bytes(b'synthetic raw file')
            m=publish_archive(f,Path(tmp)/'objects')
            ev=self.ev()
            manifest=m|dict(url='https://example.test/a.gz',row_count=1,format='binary')
            self.store.register_archive(ev,manifest)
            self.assertEqual(len(self.store.archive_overlaps(ev)),1)
            ev['available_range']=dict(start_ms=ev['start_ms'],end_ms=ev['end_ms'],method='archive_manifest',source_url=manifest['url'],sha256=m['sha256'])
            r=self.record(); r['source']['sha256']=m['sha256']
            self.store.ingest([r],ev,job_key=self.symbol)
            self.assertTrue(self.store.coverage('bybit','linear',self.symbol,'trades','',ev['start_ms'],ev['end_ms'])['complete'])
            Path(m['storage_uri']).unlink()
            self.assertFalse(self.store.coverage('bybit','linear',self.symbol,'trades','',ev['start_ms'],ev['end_ms'])['complete'])

    def test_aggregate_comparison_and_stale_source_fingerprint(self):
        self.store.ingest([self.record()],self.ev(),job_key=self.symbol)
        scope=self.ev()
        result=self.store.save_aggregate(scope,dict(volume='0.01',trade_count=1,open='123.000000000000000001',high='123.000000000000000001',low='123.000000000000000001',close='123.000000000000000001'),algorithm_version='test-v1')
        self.assertTrue(self.store.check_aggregate(result)['matches'])
        r=self.record(); r['payload']['trade_id']='new'; r['timestamp_ms']+=1
        self.store.ingest([r],self.ev(),job_key=self.symbol)
        self.assertTrue(self.store.check_aggregate(result)['stale'])

    def test_metadata_versions_are_idempotent_and_provenanced(self):
        source=self.record()['source']
        self.store.save_instrument('bybit','linear',self.symbol,0,{'tick_size':'0.01'},source)
        self.store.save_instrument('bybit','linear',self.symbol,0,{'tick_size':'0.01'},source)
        self.store.save_instrument('bybit','linear',self.symbol,1000,{'tick_size':'0.001'},source)
        self.assertEqual(self.count('instrument_metadata'),2)

    def test_automatic_recovery_retries_download_then_resolves_only_verified_result(self):
        ev=self.ev(available_range=dict(start_ms=1700000000000,end_ms=1700000060000,
             method='operator_attestation',source_url='https://example.test/manifest',sha256='a'*64))
        self.store.ingest([self.record()],ev,job_key=self.symbol)
        self.store.mark_range(ev,'corrupt','synthetic recoverable damage')
        def failed(job): raise OSError('synthetic transport failure')
        result=self.store.recover(failed,'bybit','linear',self.symbol,'trades',retry_delay_ms=0)
        self.assertEqual(result[0]['state'],'retry')
        def fetch(job): return [self.record()],ev,{'cursor':'done'}
        result=self.store.recover(fetch,'bybit','linear',self.symbol,'trades',retry_delay_ms=0)
        self.assertEqual(result[0]['state'],'verified')
        self.assertEqual(self.store.recovery_jobs('bybit','linear',self.symbol,'trades'),[])

    def test_recovery_backoff_does_not_starve_later_due_ranges(self):
        ev=self.ev()
        self.store.mark_range(ev,'corrupt','first')
        self.store.mark_range(self.ev(start_ms=1700000060000,end_ms=1700000120000),'corrupt','second')
        def failed(job): raise OSError('transport')
        first=self.store.recover(failed,'bybit','linear',self.symbol,'trades',max_jobs=1,retry_delay_ms=60000)
        second=self.store.recover(failed,'bybit','linear',self.symbol,'trades',max_jobs=1,retry_delay_ms=60000)
        self.assertEqual(len(second),1)
        self.assertNotEqual(first[0]['job_key'],second[0]['job_key'])


# unittest inherits test methods; hide base tests from the derived operations case.
for _name in list(support.PostgresTests.__dict__):
    if _name.startswith('test_'): setattr(StorageOperationsTests,_name,None)

if __name__=='__main__': unittest.main()
