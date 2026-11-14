import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts.ops_pg_snapshot import durable_job_snapshot, SnapshotRefusal

class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, query, params):
        self.calls.append((query, params))
    def fetchall(self): return self.rows

class FakeConnection:
    def __init__(self, rows):
        self.cur = FakeCursor(rows)
        self.statements = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def transaction(self): return self
    def execute(self, sql): self.statements.append(sql)
    def cursor(self, **kwargs): return self.cur

class PgSnapshotTests(unittest.TestCase):
    def test_expired_running_and_paused_preserved(self):
        a, b = uuid4(), uuid4()
        conn = FakeConnection([{"id": a, "state": "running", "expired": True},
                               {"id": b, "state": "paused", "expired": False}])
        with patch("scripts.ops_pg_snapshot.connect", return_value=conn):
            states, expired = durable_job_snapshot("disposable", [str(a), str(b)])
        self.assertEqual(states[str(b)], "paused")
        self.assertEqual(expired, frozenset({str(a)}))
        self.assertTrue(any("READ ONLY" in sql for sql in conn.statements))

    def test_missing_durable_job_refuses(self):
        with patch("scripts.ops_pg_snapshot.connect", return_value=FakeConnection([])):
            with self.assertRaises(SnapshotRefusal):
                durable_job_snapshot("disposable", [str(uuid4())])

    def test_duplicate_ids_refuse_before_connect(self):
        uid = str(uuid4())
        with patch("scripts.ops_pg_snapshot.connect") as connect:
            with self.assertRaises(SnapshotRefusal):
                durable_job_snapshot("disposable", [uid, uid])
            connect.assert_not_called()

    def test_invalid_uuid_refuses_before_connect(self):
        with patch("scripts.ops_pg_snapshot.connect") as connect:
            with self.assertRaises(SnapshotRefusal):
                durable_job_snapshot("disposable", ["not-a-uuid"])
            connect.assert_not_called()

if __name__ == "__main__":
    unittest.main()
