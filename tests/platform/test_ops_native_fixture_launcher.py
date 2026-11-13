"""Real Windows disposable launcher tests; never touch installed tester or PG5432."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.ops_native_admission import NativePin
from scripts.ops_native_windows_witness import NativeRestartBlocked
from scripts.ops_native_fixture_launcher import DisposableWin32Launcher


CHILD = """import json,os,pathlib,time
stop=pathlib.Path(os.environ['OPS_STOP_PATH'])
checkpoint=pathlib.Path(os.environ['OPS_CHECKPOINT_PATH'])
deadline=time.monotonic()+15
while not stop.is_file():
    if time.monotonic()>deadline:raise SystemExit(95)
    time.sleep(.02)
with checkpoint.open('x',encoding='utf-8') as output:
    json.dump({'schema':'ops-native-drain/1',
               'role':os.environ['OPS_ROLE'],
               'generation':int(os.environ['OPS_GENERATION']),
               'owner_marker':os.environ['OPS_OWNER_MARKER'],
               'config_sha256':os.environ['OPS_CONFIG_SHA256'],
               'drained':True},output)
    output.flush()
    os.fsync(output.fileno())
"""


@unittest.skipUnless(os.name == "nt", "NOT_RUN: Windows original handles required")
class FixtureLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.pin = NativePin("a"*40, "b"*64, str(root), "disposable-pg", "start-time")
        self.argv = (str(Path(sys.executable).resolve()), "-u", "-c", CHILD)
        self.launcher = DisposableWin32Launcher(
            root, self.pin,
            pg_probe=lambda: {"pg_system_identifier":"disposable-pg",
                              "pg_postmaster_start":"start-time"},
            leases_probe=lambda: {"job-one": "queued"})
        self.addCleanup(self.cleanup_cooperatively)

    def cleanup_cooperatively(self):
        root = Path(self.temp.name)
        for role in ("api","worker"):
            for generation in (0, 1):
                stop=root / f"native-{generation:04d}-{role}.stop.request"
                if not stop.exists():
                    try: stop.touch(exist_ok=True)
                    except OSError: pass
        for child in self.launcher._children:
            if child.poll() is None:
                try: child.wait(timeout=3)
                except subprocess.TimeoutExpired: pass  # never kill

    def test_two_exact_win32_generations_and_controller_crash_refusal(self):
        first=self.launcher.start_pair(api_argv=self.argv, worker_argv=self.argv)
        self.assertEqual(first.generation,0)
        self.assertNotEqual(first.api.birth.pid,first.worker.birth.pid)
        closure=self.launcher.stop_and_seal(first)
        self.assertTrue(closure["closed"])
        self.assertEqual((first.api.handle.poll(), first.worker.handle.poll()),(0,0))
        # A new controller with only retained PID/JSON is unable to adopt handles.
        foreign=DisposableWin32Launcher(
            Path(self.temp.name),self.pin,
            pg_probe=lambda: {"pg_system_identifier":"disposable-pg",
                              "pg_postmaster_start":"start-time"},
            leases_probe=lambda: {})
        with self.assertRaises(NativeRestartBlocked):
            foreign.start_pair(api_argv=self.argv, worker_argv=self.argv)
        second=self.launcher.start_pair(api_argv=self.argv,worker_argv=self.argv)
        self.assertEqual(second.generation,1)
        self.launcher.stop_and_seal(second)
        self.assertEqual((second.api.handle.poll(),second.worker.handle.poll()),(0,0))

    def test_missing_api_executable_rolls_back_real_worker_without_adoption(self):
        bad_api = (str(Path(self.temp.name).resolve() / "nonexistent-api.exe"),)
        with self.assertRaises(NativeRestartBlocked):
            self.launcher.start_pair(api_argv=bad_api, worker_argv=self.argv)
        self.assertEqual(len(self.launcher._children), 1)
        self.assertEqual(self.launcher._children[0].poll(), 0)
        record = Path(self.temp.name) / "native-generation-0000-rollback.json"
        self.assertTrue(record.is_file())
        import json
        receipt = json.loads(record.read_text())
        self.assertFalse(receipt["restart_permitted"])
        self.assertEqual(receipt["status"], "SEALED_PARTIAL")
        self.assertEqual(set(receipt["sealed_roles"]), {"worker"})
        with self.assertRaises(NativeRestartBlocked):
            self.launcher.start_pair(api_argv=self.argv, worker_argv=self.argv)

    def test_pg_incarnation_mismatch_blocks_before_spawn(self):
        self.launcher.pg_probe=lambda: {"pg_system_identifier":"foreign",
                                        "pg_postmaster_start":"start-time"}
        with self.assertRaises(NativeRestartBlocked):
            self.launcher.start_pair(api_argv=self.argv,worker_argv=self.argv)
        self.assertEqual(self.launcher._children,[])
        self.assertFalse((Path(self.temp.name)/"native-launch-generations"/"0000").exists())

    def test_active_lease_blocks_before_spawn(self):
        self.launcher.leases_probe=lambda: {"job-one":"running"}
        with self.assertRaises(NativeRestartBlocked):
            self.launcher.start_pair(api_argv=self.argv,worker_argv=self.argv)
        self.assertFalse(self.launcher._children)


if __name__=="__main__":
    unittest.main()
