"""Real Win32 original process-handle acceptance, Windows hosted runner only.

Runs two disposable Python subprocesses, never installed services or PC4.
Cooperative stop only, zero force-kill/terminate calls.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from scripts.ops_native_windows_witness import NativeBirth, NativeExitWitness, NativeRestartBlocked, observe_psutil,
    request_native_scoped_stop, sample_native_resources
from scripts.ops_win32_handle import WindowsOriginalHandle

CHILD = """import json,os,time,pathlib
stop=pathlib.Path(os.environ['OPS_STOP_PATH'])
out=pathlib.Path(os.environ['OPS_CHECKPOINT_PATH'])
deadline=time.monotonic()+18
while not stop.exists():
    if time.monotonic()>deadline:raise SystemExit(97)
    time.sleep(.02)
with out.open('x',encoding='utf-8') as stream:
    json.dump({'schema':'ops-native-drain/1', 'role':os.environ['OPS_ROLE'],
               'generation':int(os.environ['OPS_GENERATION']),
               'owner_marker':os.environ['OPS_OWNER_MARKER'],
               'config_sha256':os.environ['OPS_CONFIG_SHA256'], 'drained':True},stream)
    stream.flush()
    os.fsync(stream.fileno())
"""


@unittest.skipUnless(os.name == 'nt', 'NOT_RUN: requires native Windows')
class OriginalWin32ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.children = []
        self.stops = []
        self.addCleanup(self.cooperative_cleanup)

    def cooperative_cleanup(self):
        for path in self.stops:
            if not path.exists():
                try: path.touch(exist_ok=True)
                except OSError: pass
        for child in self.children:
            if child.poll() is None:
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired: pass  # no force-kill

    def launch(self, role, generation):
        import psutil
        stop = self.root / f'native-{generation:04d}-{role}.stop.request'
        checkpoint = self.root / f'{role}-{generation}.checkpoint.json'
        self.stops.append(stop)
        marker = f'win32-fixture-{role}-generation-{generation}-123456'
        env = dict(os.environ, OPS_OWNER_MARKER=marker,
                   OPS_CONFIG_SHA256='a'*64, OPS_STOP_PATH=str(stop),
                   OPS_CHECKPOINT_PATH=str(checkpoint), OPS_ROLE=role,
                   OPS_GENERATION=str(generation))
        executable = str(Path(sys.executable).resolve())
        child = subprocess.Popen(
            [executable, '-u', '-c', CHILD], cwd=self.root, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.children.append(child)
        original = WindowsOriginalHandle(child)
        process = psutil.Process(child.pid)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            try:
                if (process.environ().get('OPS_OWNER_MARKER') == marker
                        and process.cmdline() and original.poll() is None):
                    break
            except (psutil.Error, OSError):
                pass
            time.sleep(.025)
        else:
            raise AssertionError('Owned child did not enroll before deadline')
        birth = NativeBirth(role, generation, process.pid, process.create_time(),
                            process.exe(), process.cwd(), tuple(process.cmdline()),
                            process.username(), marker, 'a'*64)
        return birth, original, checkpoint

    def test_two_real_windows_generations_and_original_handles(self):
        import psutil
        resources = []
        for generation in (0, 1):
            api, ah, api_checkpoint = self.launch('api', generation)
            worker, wh, worker_checkpoint = self.launch('worker', generation)
            witness = NativeExitWitness(
                generation=generation, api=api, worker=worker,
                api_handle=ah, worker_handle=wh,
                observe=lambda b, h: observe_psutil(b, h, psutil.Process),
                root=self.root)
            with self.assertRaises(NativeRestartBlocked):
                witness.require_restart_allowed({})
            resources.append({
                "generation": generation,
                "api": sample_native_resources(api, ah, psutil.Process),
                "worker": sample_native_resources(worker, wh, psutil.Process),
            })
            witness.request_stop(
                stop_for_role=lambda birth: request_native_scoped_stop(self.root, birth)
            )
            receipt = witness.seal_exits(checkpoint_paths={"api": api_checkpoint,
                                                         "worker": worker_checkpoint}, timeout=6)
            witness.require_restart_allowed(receipt)
            self.assertEqual((ah.poll(), wh.poll()), (0,0))
            self.assertTrue(api_checkpoint.exists())
            self.assertTrue(worker_checkpoint.exists())
            self.assertEqual(json.loads(api_checkpoint.read_text())['generation'], generation)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            Path("ops-native-win32-evidence.json").write_text(json.dumps({
                "schema": "ops-native-win32-fixture/1",
                "process_generations": 2,
                "api_worker_original_win32_handles": True,
                "external_clean_exit_zero_each": True,
                "retained_checkpoints_hash_verified": True,
                "samples": resources,
                "pg_integration": "NOT_RUN_ON_WINDOWS",
                "installed_pc4": "NOT_RUN",
                "hard_limits_enforced": False,
            }, indent=2, sort_keys=True), encoding="utf-8")

    def test_stale_birth_refuses_before_stop(self):
        import psutil
        from dataclasses import replace
        api, ah, _ = self.launch('api', 0)
        worker, wh, _ = self.launch('worker', 0)
        wrong = replace(api, created=api.created+12)
        witness = NativeExitWitness(
            generation=0, api=wrong, worker=worker,
            api_handle=ah, worker_handle=wh,
            observe=lambda b,h: observe_psutil(b,h,psutil.Process),
            root=self.root)
        invoked = []
        with self.assertRaises(NativeRestartBlocked):
            witness.request_stop(stop_for_role=lambda birth: invoked.append(birth.role))
        self.assertEqual(invoked, [])


if __name__=='__main__':
    unittest.main()
