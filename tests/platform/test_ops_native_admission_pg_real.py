"""Disposable PG16 native admission test: real DB transactions, no PC4."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

DSN = os.environ.get("TEST_OPS_ISOLATED_DSN", "")


@unittest.skipUnless(DSN, "NOT_RUN: disposable PostgreSQL not configured")
class NativePgAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.ops_pg_isolation import require_isolated_dsn
        from src.api.db import migrate
        require_isolated_dsn(DSN)
        migrate(DSN)

    def test_pg_incarnation_and_active_lease_block_native_admission(self):
        from psycopg.types.json import Jsonb
        from src.api.db import connect
        from scripts.ops_admission_pg import read_admission_pg, safe_lease_states
        from scripts.ops_native_admission import NativeAdmission, NativePin
        from scripts.ops_native_windows_witness import NativeRestartBlocked
        from scripts.ops_pg_claim import claim_existing

        uid = uuid4()
        with connect(DSN) as conn:
            conn.execute("DELETE FROM tester_platform.jobs")
            conn.execute("UPDATE tester_platform.control SET draining=false,max_running=2 WHERE singleton")
            conn.execute("INSERT INTO tester_platform.jobs(id,request) VALUES (%s,%s)", (
                uid, Jsonb(dict(category="linear",symbol="BTCUSDT",source="bybit",
                                kind="candles",interval="1",start_ms=0,end_ms=60000))))
        try:
            before = read_admission_pg(DSN)
            self.assertEqual(before["active_running"], 0)
            with TemporaryDirectory() as td:
                root = Path(td).resolve()
                pin = NativePin("a"*40, "b"*64, str(root),
                                before["pg_system_identifier"], before["pg_postmaster_start"])
                admission = NativeAdmission(root, pin)
                pg = lambda: read_admission_pg(DSN)
                leases = lambda: safe_lease_states(pg())
                path = admission.reserve(0, previous_exit_validator=lambda _: False,
                                         pg_probe=pg, leases_probe=leases)
                self.assertTrue((path/"reservation.json").is_file())
                lease = claim_existing(DSN, str(uid))
                self.assertIsNotNone(lease)
                with self.assertRaises(NativeRestartBlocked):
                    admission.reserve(1, previous_exit_validator=lambda _: True,
                                      pg_probe=pg, leases_probe=leases)
                # A prior partial reservation is never reusable even if DB is free.
                with connect(DSN) as conn:
                    conn.execute("UPDATE tester_platform.jobs SET state='queued',"
                                 "lease_token=NULL,lease_until=NULL WHERE id=%s", (uid,))
                with self.assertRaises(NativeRestartBlocked):
                    admission.reserve(1, previous_exit_validator=lambda _: True,
                                      pg_probe=pg, leases_probe=leases)
                self.assertEqual(read_admission_pg(DSN)["pg_system_identifier"],
                                 before["pg_system_identifier"])
                self.assertEqual(read_admission_pg(DSN)["pg_postmaster_start"],
                                 before["pg_postmaster_start"])
        finally:
            with connect(DSN) as conn:
                conn.execute("DELETE FROM tester_platform.jobs WHERE id=%s", (uid,))


if __name__ == "__main__":
    unittest.main()
