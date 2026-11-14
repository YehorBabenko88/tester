"""One full child/Pg lease/restart scenario against a disposable CI PostgreSQL service."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from uuid import uuid4


DSN = os.environ.get("TEST_OPS_ISOLATED_DSN", "")


@unittest.skipUnless(DSN, "NOT_RUN: isolated PostgreSQL service not configured")
class IsolatedFullRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from scripts.ops_pg_isolation import require_isolated_dsn
        require_isolated_dsn(DSN)
        from src.api.db import migrate
        migrate(DSN)

    def test_inflight_lease_checkpoint_then_same_root_restart(self):
        from psycopg.types.json import Jsonb
        from src.api.db import connect
        from src.api.jobs import JobQueue
        from scripts.ops_dual_lifecycle import DisposableApiWorker

        job_id = uuid4()
        with connect(DSN) as conn:
            conn.execute("DELETE FROM tester_platform.jobs")
            conn.execute("UPDATE tester_platform.control SET draining=false,max_running=2 WHERE singleton")
            conn.execute("INSERT INTO tester_platform.jobs(id,request) VALUES (%s,%s)", (
                job_id, Jsonb(dict(category="linear", symbol="BTCUSDT", source="bybit",
                                   kind="candles", interval="1", start_ms=0, end_ms=60000))
            ))
        with connect(DSN) as conn:
            pg_before = conn.execute(
                "SELECT (pg_control_system()).system_identifier::text,pg_postmaster_start_time()"
            ).fetchone()
        with tempfile.TemporaryDirectory() as directory:
            runtime = DisposableApiWorker(Path(directory), marker="ci-owned-postgres-lifecycle-123456")
            executable = str(Path(sys.executable).resolve())
            api_argv = [executable, "-m", "scripts.ops_disposable_api"]
            worker_argv = [executable, "-m", "scripts.ops_pg_disposable_worker"]
            repo_root = str(Path(__file__).resolve().parents[2])
            env = dict(os.environ, OPS_JOB_ID=str(job_id),
                       PYTHONPATH=repo_root + os.pathsep + os.environ.get("PYTHONPATH", ""))
            try:
                first = runtime.start_pair(api_argv, worker_argv, environment=env)
                previous = self._await_claim(job_id, 1)
                self.assertEqual(previous["state"], "running")
                seal1 = runtime.stop_pair_and_seal(first)
                self.assertEqual((seal1["api_exit_code"], seal1["worker_exit_code"]), (0, 0))
                self.assertEqual(JobQueue(DSN).get(job_id).state, "queued")
                self.assertEqual(JobQueue(DSN).get(job_id).checkpoint, {"generation": 0})
                second = runtime.start_pair(api_argv, worker_argv, environment=env)
                current = self._await_claim(job_id, 2)
                self.assertNotEqual(previous["lease_token"], current["lease_token"])
                self.assertEqual(JobQueue(DSN).get(job_id).checkpoint, {"generation": 0})
                self.assertFalse(JobQueue(DSN).active(job_id, previous["lease_token"]))
                seal2 = runtime.stop_pair_and_seal(second)
                self.assertEqual((seal2["api_exit_code"], seal2["worker_exit_code"]), (0, 0))
                with connect(DSN) as conn:
                    pg_after = conn.execute(
                        "SELECT (pg_control_system()).system_identifier::text,pg_postmaster_start_time()"
                    ).fetchone()
                self.assertEqual(pg_before, pg_after, "PostgreSQL incarnation changed")
                final = JobQueue(DSN).get(job_id)
                self.assertEqual(final.state, "completed")
                self.assertEqual(final.attempts, 2)
                self.assertEqual(final.checkpoint, {"generation": 1})
                for generation in (0, 1):
                    for role in ("api", "worker"):
                        p = Path(directory)/role/"ops-generations"/f"{generation:04d}"
                        self.assertTrue((p/"checkpoint.json").is_file())
                        self.assertTrue((p/"closed.json").is_file())
                    self.assertTrue((Path(directory)/"pair-generations"/f"{generation:04d}"/"closed.json").is_file())
            finally:
                # Cooperative-only cleanup of exact enrolled children; never kill.
                for role in (runtime.api, runtime.worker):
                    for generation, handle in role.handles.items():
                        if handle.poll() is None:
                            stop = role.root/"ops-generations"/f"{generation:04d}"/"stop.request"
                            try: stop.touch(exist_ok=True)
                            except OSError: pass
                            try: handle.wait(timeout=3)
                            except Exception: pass
                with connect(DSN) as conn:
                    conn.execute("DELETE FROM tester_platform.jobs WHERE id=%s", (job_id,))

    @staticmethod
    def _await_claim(job_id, attempts):
        from src.api.db import connect
        deadline = time.monotonic()+8
        while time.monotonic()<deadline:
            with connect(DSN) as conn:
                row=conn.execute("SELECT state,attempts,lease_token FROM tester_platform.jobs WHERE id=%s",(job_id,)).fetchone()
            if row and row[0]=="running" and row[1]==attempts and row[2] is not None:
                return dict(state=row[0], attempts=row[1], lease_token=row[2])
            time.sleep(.04)
        raise AssertionError("Isolated child did not claim the durable job")

if __name__ == "__main__":
    unittest.main()
