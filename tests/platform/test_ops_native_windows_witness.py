"""Pure tests for the opt-in native Windows two-handle exit protocol.

No Windows process starts, no sockets, no PC4 interaction and no kill.
"""
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from dataclasses import replace

from scripts.ops_native_windows_witness import (
    NativeBirth, NativeExitWitness, NativeRestartBlocked, observe_psutil,
)


class FakeHandle:
    def __init__(self, pid, *, exit_code=0, timeout=False):
        self.pid = pid
        self.exit_code = exit_code
        self.timeout = timeout
        self.stopped = False
    def poll(self): return self.exit_code if self.stopped else None
    def wait(self, timeout):
        if self.timeout: raise subprocess.TimeoutExpired('native-test', timeout)
        self.stopped = True
        return self.exit_code


class NativeWitnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.api = NativeBirth('api', 0, 121, 1234.5, 'C:/python.exe',
                               'C:/owned', ('python','-m','api'), 'limited',
                               'api-owner-marker-123456', 'a'*64)
        self.worker = NativeBirth('worker', 0, 122, 1235.5, 'C:/python.exe',
                                  'C:/owned', ('python','-m','worker'), 'limited',
                                  'worker-owner-marker-123456', 'a'*64)
        self.api_handle = FakeHandle(121)
        self.worker_handle = FakeHandle(122)
        self.observed = {'api': self.api, 'worker': self.worker}
        self.checkpoints = {}
        for birth in (self.api, self.worker):
            path = self.root / f"{birth.role}.checkpoint.json"
            path.write_text(json.dumps({
                "schema": "ops-native-drain/1", "role": birth.role,
                "generation": birth.generation, "owner_marker": birth.owner_marker,
                "config_sha256": birth.config_sha256, "drained": True,
            }), encoding="utf-8")
            self.checkpoints[birth.role] = path
        self.witness = NativeExitWitness(
            generation=0, api=self.api, worker=self.worker,
            api_handle=self.api_handle, worker_handle=self.worker_handle,
            observe=lambda birth, handle: self.observed[birth.role], root=self.root)

    def test_both_birth_bound_exits_and_seal(self):
        calls = []
        self.witness.request_stop(stop_for_role=lambda birth: calls.append(birth.role))
        self.assertEqual(calls, ['api','worker'])
        receipt = self.witness.seal_exits(checkpoint_paths=self.checkpoints)
        self.assertEqual((receipt['api']['exit_code'],receipt['worker']['exit_code']), (0,0))
        self.witness.require_restart_allowed(receipt)
        with self.assertRaises(NativeRestartBlocked):
            self.witness.request_stop(stop_for_role=lambda birth: None)

    def test_live_old_process_refuses_restart(self):
        with self.assertRaises(NativeRestartBlocked):
            self.witness.seal_exits(checkpoint_paths=self.checkpoints)
        self.assertFalse((self.root/'native-generation-0000-closed.json').exists())

    def test_foreign_worker_owner_refuses_before_any_stop(self):
        self.observed['worker'] = replace(self.worker, owner_marker='foreign')
        calls = []
        with self.assertRaises(NativeRestartBlocked):
            self.witness.request_stop(stop_for_role=lambda birth: calls.append(birth.role))
        self.assertEqual(calls, [])

    def test_stale_birth_and_executable_refused(self):
        for field, value in [('created', 9999.0), ('image','C:/foreign.exe'),
                             ('pid', 999), ('argv', ('python','-m','foreign'))]:
            with self.subTest(field=field):
                self.observed['api'] = replace(self.api, **{field:value})
                with self.assertRaises(NativeRestartBlocked):
                    self.witness.request_stop(stop_for_role=lambda birth: None)
                self.observed['api'] = self.api

    def test_nonzero_worker_exit_never_sealed(self):
        self.worker_handle.exit_code = 5
        self.witness.request_stop(stop_for_role=lambda birth: None)
        with self.assertRaises(NativeRestartBlocked):
            self.witness.seal_exits(checkpoint_paths=self.checkpoints)
        self.assertFalse((self.root/'native-generation-0000-closed.json').exists())

    def test_worker_timeout_never_force_stopped(self):
        self.worker_handle.timeout = True
        self.witness.request_stop(stop_for_role=lambda birth: None)
        with self.assertRaises(NativeRestartBlocked):
            self.witness.seal_exits(checkpoint_paths=self.checkpoints, timeout=.01)
        self.assertFalse(self.worker_handle.stopped)
        self.assertFalse((self.root/'native-generation-0000-closed.json').exists())

    def test_missing_original_handle_refuses(self):
        self.api_handle.pid = 999
        with self.assertRaises(NativeRestartBlocked):
            self.witness.request_stop(stop_for_role=lambda birth: None)

    def test_tampered_retained_closure_refuses(self):
        self.witness.request_stop(stop_for_role=lambda birth: None)
        receipt = self.witness.seal_exits(checkpoint_paths=self.checkpoints)
        path = self.root/'native-generation-0000-closed.json'
        saved = json.loads(path.read_text())
        saved['worker']['exit_code'] = 99
        path.write_text(json.dumps(saved))
        with self.assertRaises(NativeRestartBlocked):
            self.witness.require_restart_allowed(receipt)

    def test_constructor_refuses_same_pid_or_wrong_generation(self):
        for worker in (replace(self.worker, pid=self.api.pid),
                       replace(self.worker, generation=1),
                       replace(self.worker, config_sha256='b'*64)):
            with self.subTest(worker=worker):
                with self.assertRaises(NativeRestartBlocked):
                    NativeExitWitness(generation=0, api=self.api, worker=worker,
                        api_handle=self.api_handle, worker_handle=self.worker_handle,
                        observe=lambda birth, handle: birth, root=self.root)

    def test_observe_psutil_checks_native_identity(self):
        class FakeProc:
            pid = 121
            def create_time(self): return 1234.5
            def exe(self): return 'C:/python.exe'
            def cwd(self): return 'C:/owned'
            def cmdline(self): return ['python','-m','api']
            def username(self): return 'limited'
            def environ(self): return {
                'OPS_OWNER_MARKER':'api-owner-marker-123456',
                'OPS_CONFIG_SHA256':'a'*64,
            }
        self.assertEqual(observe_psutil(self.api,self.api_handle,lambda pid:FakeProc()),self.api)
        with self.assertRaises(NativeRestartBlocked):
            observe_psutil(self.api,self.api_handle,lambda pid:None)

    def test_missing_worker_checkpoint_refuses_closed(self):
        self.checkpoints["worker"].unlink()
        self.witness.request_stop(stop_for_role=lambda birth: None)
        with self.assertRaises(NativeRestartBlocked):
            self.witness.seal_exits(checkpoint_paths=self.checkpoints)
        self.assertFalse((self.root/'native-generation-0000-closed.json').exists())

    def test_tampered_drain_marker_refuses_closed(self):
        path = self.checkpoints["worker"]
        receipt = json.loads(path.read_text())
        receipt["owner_marker"] = "foreign"
        path.write_text(json.dumps(receipt))
        self.witness.request_stop(stop_for_role=lambda birth: None)
        with self.assertRaises(NativeRestartBlocked):
            self.witness.seal_exits(checkpoint_paths=self.checkpoints)
        self.assertFalse((self.root/'native-generation-0000-closed.json').exists())


if __name__ == '__main__':
    unittest.main()
