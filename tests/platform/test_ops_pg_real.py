"""Opt-in real PostgreSQL lease-fencing test, host-isolated DB only.

Never connect to workstation PostgreSQL. CI must supply the exact disposable
endpoint and only the disposable job schema is created there.
"""
import os
import unittest
from uuid import uuid4


DSN = os.environ.get("TEST_OPS_ISOLATED_DSN", "")


@unittest.skipUnless(DSN, "NOT_RUN: no disposable PostgreSQL service/DSN")
class IsolatedPostgresLeaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from psycopg.conninfo import conninfo_to_dict
        d = conninfo_to_dict(DSN)
        if (d.get("host"), d.get("port"), d.get("dbname"), d.get("user")) != (
            "127.0.0.1", "55479", "ops_isolated", "ops_tester"
        ):
            raise RuntimeError("REFUSED: unexpected PostgreSQL endpoint")
        from src.api.db import migrate
        migrate(DSN)

    def setUp(self):
        from src.api.db import connect
        with connect(DSN) as conn:
            conn.execute("DELETE FROM tester_platform.jobs")
            conn.execute("UPDATE tester_platform.control SET draining=false,max_running=2 WHERE singleton")

    def make_job(self, state="queued"):
        from psycopg.types.json import Jsonb
        from src.api.db import connect
        job_id = uuid4()
        request = dict(category="linear", symbol="BTCUSDT", source="bybit",
                       kind="candles", interval="1", start_ms=0, end_ms=60000)
        with connect(DSN) as conn:
            conn.execute("INSERT INTO tester_platform.jobs(id,request,state) VALUES(%s,%s,%s)",
                         (job_id, Jsonb(request), state))
        return job_id

    def test_claim_fences_old_lease_and_checkpoints(self):
        from src.api.jobs import JobQueue, LeaseLost
        from src.api.db import connect
        from scripts.ops_pg_claim import claim_existing
        uid = self.make_job()
        old = claim_existing(DSN, str(uid), lease_seconds=5)
        self.assertEqual(old.id, uid)
        self.assertIsNotNone(old.lease_token)
        self.assertIsNone(claim_existing(DSN, str(uid), lease_seconds=5))
        queue = JobQueue(DSN)
        self.assertTrue(queue.active(uid, old.lease_token))
        with connect(DSN) as conn:
            conn.execute("UPDATE tester_platform.jobs SET lease_until=now()-interval '2 second' WHERE id=%s", (uid,))
        fresh = claim_existing(DSN, str(uid), lease_seconds=5)
        self.assertNotEqual(old.lease_token, fresh.lease_token)
        self.assertFalse(queue.active(uid, old.lease_token))
        with self.assertRaises(LeaseLost):
            queue.checkpoint(uid, old.lease_token, {"stale": True}, 0.1)
        queue.checkpoint(uid, fresh.lease_token, {"recovered": True}, 0.5)
        queue.finish(uid, fresh.lease_token)
        self.assertEqual(queue.get(uid).state, "completed")
        self.assertIsNone(claim_existing(DSN, str(uid)))

    def test_paused_job_not_claimed(self):
        from scripts.ops_pg_claim import claim_existing
        uid = self.make_job("paused")
        self.assertIsNone(claim_existing(DSN, str(uid)))

    def test_running_lease_snapshot_is_advisory(self):
        from scripts.ops_pg_claim import claim_existing
        from scripts.ops_pg_snapshot import durable_job_snapshot
        from src.api.db import connect
        uid = self.make_job()
        lease = claim_existing(DSN, str(uid), lease_seconds=5)
        states, expired = durable_job_snapshot(DSN, [str(uid)])
        self.assertEqual(states[str(uid)], "running")
        self.assertNotIn(str(uid), expired)
        self.assertIsNone(claim_existing(DSN, str(uid)))
        with connect(DSN) as conn:
            conn.execute("UPDATE tester_platform.jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (uid,))
        states, expired = durable_job_snapshot(DSN, [str(uid)])
        self.assertIn(str(uid), expired)
        fresh = claim_existing(DSN, str(uid))
        self.assertNotEqual(lease.lease_token, fresh.lease_token)


if __name__ == "__main__":
    unittest.main()
