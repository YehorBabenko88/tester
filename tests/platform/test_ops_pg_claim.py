import unittest
from unittest.mock import patch
from types import SimpleNamespace
from uuid import uuid4

from scripts.ops_pg_claim import claim_existing, ClaimRefusal


class FakeCursor:
    def __init__(self, control, active, claimed):
        self.results = [control, {"active": active}, {"id": uuid4(), "lease_token": uuid4()} if claimed else None]
        self.sql = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params=None): self.sql.append((sql, params))
    def fetchone(self): return self.results.pop(0)


class FakeConnection:
    def __init__(self, cursor): self.cur = cursor
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self, **kwargs): return self.cur


class ClaimTests(unittest.TestCase):
    def test_claim_uses_atomic_sql_lease_fence(self):
        cur = FakeCursor({"draining": False, "max_running": 2}, 0, True)
        with patch("scripts.ops_pg_claim.connect", return_value=FakeConnection(cur)), \
             patch("scripts.ops_pg_claim.Lease.model_validate", side_effect=lambda row: SimpleNamespace(**row)):
            lease = claim_existing("disposable", str(uuid4()))
        self.assertIsNotNone(lease.lease_token)
        update = cur.sql[-1][0]
        self.assertIn("lease_until < now()", update)
        self.assertIn("state='queued'", update)
        self.assertIn("RETURNING *", update)

    def test_draining_refuses_without_update(self):
        cur = FakeCursor({"draining": True, "max_running": 2}, 0, True)
        with patch("scripts.ops_pg_claim.connect", return_value=FakeConnection(cur)):
            self.assertIsNone(claim_existing("disposable", str(uuid4())))
        self.assertEqual(len(cur.sql), 1)

    def test_capacity_refuses_without_update(self):
        cur = FakeCursor({"draining": False, "max_running": 1}, 1, True)
        with patch("scripts.ops_pg_claim.connect", return_value=FakeConnection(cur)):
            self.assertIsNone(claim_existing("disposable", str(uuid4())))
        self.assertEqual(len(cur.sql), 2)

    def test_lost_claim_returns_none(self):
        cur = FakeCursor({"draining": False, "max_running": 2}, 0, False)
        with patch("scripts.ops_pg_claim.connect", return_value=FakeConnection(cur)):
            self.assertIsNone(claim_existing("disposable", str(uuid4())))

    def test_invalid_id_never_connects(self):
        with patch("scripts.ops_pg_claim.connect") as connect:
            with self.assertRaises(ClaimRefusal):
                claim_existing("disposable", "invalid")
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
