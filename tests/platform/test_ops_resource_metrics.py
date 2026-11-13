import unittest
from unittest.mock import Mock
from datetime import datetime, timezone

from scripts.ops_resource_metrics import collect_pg_metrics, wal_growth

class Cursor:
    def __init__(self, row): self.row=row
    def fetchone(self): return self.row

class Conn:
    def __init__(self, *, queue=None, wal=None, identity=None, deny=False):
        self.queue=queue
        self.wal=wal
        self.identity=identity
        self.deny=deny
        self.statements=[]
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql):
        self.statements.append(sql)
        if self.deny and 'pg_stat_wal' in sql:
            raise PermissionError('WAL unavailable')
        if sql == 'SET TRANSACTION READ ONLY': return Cursor(None)
        if 'tester_platform.jobs' in sql: return Cursor(self.queue)
        if 'pg_stat_wal' in sql: return Cursor((self.wal,))
        if 'pg_control_system' in sql: return Cursor(self.identity)
        raise AssertionError(sql)

class MetricsTests(unittest.TestCase):
    def test_queue_wal_and_incarnation_read_only(self):
        birth=datetime(2026,11,13,tzinfo=timezone.utc)
        a=Conn(queue=(1,2,3,4,5))
        b=Conn(wal=12345, identity=('123',birth))
        sources=iter((a,b))
        sample=collect_pg_metrics('strict-disposable',
                 connector=lambda _:next(sources),guard=lambda d:d)
        self.assertEqual(sample['queue_depth'],6)
        self.assertEqual(sample['wal_bytes'],12345)
        self.assertEqual(sample['queue']['running'],2)
        self.assertEqual(sample['status'],'OBSERVED')
        self.assertTrue(all('UPDATE' not in sql and 'DELETE' not in sql for sql in a.statements+b.statements))
        self.assertEqual(sum(sql=='SET TRANSACTION READ ONLY' for sql in a.statements+b.statements),2)
        self.assertEqual(sample['unknown_fields'],['wal_delta_bytes'])

    def test_wal_unavailable_explicit_unknown_preserves_queue(self):
        a=Conn(queue=(0,1,0,0,0))
        b=Conn(deny=True)
        sources=iter((a,b))
        sample=collect_pg_metrics('strict-disposable',
                    connector=lambda _:next(sources),guard=lambda d:d)
        self.assertEqual(sample['queue_depth'],1)
        self.assertIsNone(sample['wal_bytes'])
        self.assertIn('wal_bytes',sample['unknown_fields'])
        self.assertEqual(sample['status'],'PARTIAL')

    def test_refuses_foreign_dsn_before_database(self):
        connector=Mock()
        with self.assertRaises(RuntimeError):
            collect_pg_metrics('foreign', connector=connector,guard=lambda d:(_ for _ in ()).throw(RuntimeError('forbidden')))
        connector.assert_not_called()

    def test_wal_growth_requires_same_incarnation(self):
        a=dict(pg_system_identifier='123',pg_postmaster_start='birth',wal_bytes=100)
        b={**a,'wal_bytes':145}
        self.assertEqual(wal_growth(a,b),{'status':'OBSERVED','wal_delta_bytes':45})
        self.assertIsNone(wal_growth(a,{**b,'pg_postmaster_start':'new'})['wal_delta_bytes'])
        self.assertEqual(wal_growth(b,a)['status'],'UNKNOWN_WAL_COUNTER')

if __name__=='__main__':unittest.main()
