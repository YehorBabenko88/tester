"""Physical subprocess tests, entirely under a disposable temporary directory."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace

from scripts.ops_dual_lifecycle import DisposableApiWorker, LifecycleBlocked

CHILD = '''import json,os,pathlib,time
stop=pathlib.Path(os.environ["OPS_STOP_PATH"])
while not stop.is_file():time.sleep(.02)
pathlib.Path(os.environ["OPS_CHECKPOINT_PATH"]).write_text(json.dumps({
"generation":int(os.environ["OPS_GENERATION"]),
"owner_marker":os.environ["OPS_OWNER_MARKER"],"inflight":"sealed"}))
'''

class DualProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.ctl=DisposableApiWorker(self.root, "disposable-dual-owned-marker-123456")
        self.argv=[str(Path(sys.executable).resolve()),'-u','-c',CHILD]
        self.addCleanup(self.cleanup_cooperatively)

    def cleanup_cooperatively(self):
        for ctl in (self.ctl.api,self.ctl.worker):
            for g,handle in ctl.handles.items():
                if handle.poll() is None:
                    marker=ctl.root/'ops-generations'/f'{g:04d}'/'stop.request'
                    try:marker.touch(exist_ok=True)
                    except OSError:pass
                    try:handle.wait(timeout=4)
                    except subprocess.TimeoutExpired:pass # never force kill

    def test_two_api_worker_generations(self):
        a=self.ctl.start_pair(self.argv,self.argv)
        self.assertNotEqual(a.api.pid,a.worker.pid)
        receipt=self.ctl.stop_pair_and_seal(a)
        self.assertEqual((receipt['api_exit_code'],receipt['worker_exit_code']),(0,0))
        b=self.ctl.start_pair(self.argv,self.argv)
        self.assertNotEqual((a.api.pid,a.worker.pid),(b.api.pid,b.worker.pid))
        self.ctl.stop_pair_and_seal(b)
        self.assertEqual(len(list((self.root/'pair-generations').iterdir())),2)

    def test_live_api_worker_blocks_restart(self):
        proof=self.ctl.start_pair(self.argv,self.argv)
        with self.assertRaises(LifecycleBlocked):self.ctl.start_pair(self.argv,self.argv)
        self.assertEqual(len(self.ctl.api.handles),1)
        self.assertEqual(len(self.ctl.worker.handles),1)
        self.ctl.stop_pair_and_seal(proof)

    def test_foreign_api_birth_or_worker_owner_refuses_both_stop_markers(self):
        proof=self.ctl.start_pair(self.argv,self.argv)
        for bad in (replace(proof,api=replace(proof.api,created=proof.api.created+1)),
                    replace(proof,worker=replace(proof.worker,marker='foreign'))):
            with self.assertRaises(LifecycleBlocked):self.ctl.stop_pair_and_seal(bad)
        for ctl in (self.ctl.api,self.ctl.worker):
            self.assertFalse((ctl.root/'ops-generations'/'0000'/'stop.request').exists())
        self.ctl.stop_pair_and_seal(proof)

    def test_previous_worker_closure_tamper_refuses_new_pair(self):
        proof=self.ctl.start_pair(self.argv,self.argv)
        self.ctl.stop_pair_and_seal(proof)
        closure=self.ctl.worker.root/'ops-generations'/'0000'/'closed.json'
        receipt=json.loads(closure.read_text());receipt['exit_code']=99
        closure.write_text(json.dumps(receipt))
        with self.assertRaises(LifecycleBlocked):self.ctl.start_pair(self.argv,self.argv)
        self.assertEqual(len(self.ctl.api.handles),1)

    def test_resources_both_processes_attested(self):
        proof=self.ctl.start_pair(self.argv,self.argv)
        sample=self.ctl.sample_resources(proof)
        self.assertGreater(sample['api']['rss_bytes'],0)
        self.assertGreater(sample['worker']['rss_bytes'],0)
        self.assertIsNone(sample['wal_bytes'])
        self.assertEqual(sample['postgresql'],'NOT_RUN')
        self.ctl.stop_pair_and_seal(proof)

if __name__=='__main__':unittest.main()

def _test_worker_exited_before_stop_blocks_api_side_effect(self):
    proof=self.ctl.start_pair(self.argv,self.argv)
    worker_stop=self.ctl.worker.root/'ops-generations'/'0000'/'stop.request'
    worker_stop.touch()  # cooperative worker exit, not force kill
    self.assertEqual(self.ctl.worker.handles[0].wait(timeout=4),0)
    with self.assertRaises(LifecycleBlocked): self.ctl.stop_pair_and_seal(proof)
    self.assertFalse((self.ctl.api.root/'ops-generations'/'0000'/'stop.request').exists())
    self.assertFalse((self.root/'pair-generations'/'0000'/'closed.json').exists())


def _test_foreign_controller_cannot_adopt_old_pids(self):
    proof=self.ctl.start_pair(self.argv,self.argv)
    self.ctl.stop_pair_and_seal(proof)
    foreign=DisposableApiWorker(self.root, 'disposable-dual-owned-marker-123456')
    with self.assertRaises(LifecycleBlocked): foreign.start_pair(self.argv,self.argv)
    self.assertFalse((self.root/'pair-generations'/'0001').exists())


def _test_tampered_pair_manifest_refuses_both_stop_requests(self):
    proof=self.ctl.start_pair(self.argv,self.argv)
    path=self.root/'pair-generations'/'0000'/'manifest.json'
    manifest=json.loads(path.read_text());manifest['worker']['marker']='foreign'
    path.write_text(json.dumps(manifest))
    with self.assertRaises(LifecycleBlocked): self.ctl.stop_pair_and_seal(proof)
    for ctl in (self.ctl.api,self.ctl.worker):
        self.assertFalse((ctl.root/'ops-generations'/'0000'/'stop.request').exists())

DualProcessTests.test_worker_exited_before_stop_blocks_api_side_effect=_test_worker_exited_before_stop_blocks_api_side_effect
DualProcessTests.test_foreign_controller_cannot_adopt_old_pids=_test_foreign_controller_cannot_adopt_old_pids
DualProcessTests.test_tampered_pair_manifest_refuses_both_stop_requests=_test_tampered_pair_manifest_refuses_both_stop_requests


def _test_changed_api_argv_refuses_without_new_worker(self):
    proof=self.ctl.start_pair(self.argv,self.argv)
    self.ctl.stop_pair_and_seal(proof)
    with self.assertRaises(LifecycleBlocked):
        self.ctl.start_pair(self.argv+["unexpected"],self.argv)
    self.assertEqual(len(self.ctl.worker.handles),1)
    self.assertFalse((self.root/'pair-generations'/'0001').exists())


def _test_corrupt_old_api_manifest_refuses_without_worker_spawn(self):
    proof=self.ctl.start_pair(self.argv,self.argv)
    self.ctl.stop_pair_and_seal(proof)
    path=self.ctl.api.root/'ops-generations'/'0000'/'manifest.json'
    value=json.loads(path.read_text())
    value['schema']='corrupt'
    path.write_text(json.dumps(value))
    with self.assertRaises(LifecycleBlocked):self.ctl.start_pair(self.argv,self.argv)
    self.assertEqual(len(self.ctl.worker.handles),1)
    self.assertFalse((self.root/'pair-generations'/'0001').exists())

DualProcessTests.test_changed_api_argv_refuses_without_new_worker=_test_changed_api_argv_refuses_without_new_worker
DualProcessTests.test_corrupt_old_api_manifest_refuses_without_worker_spawn=_test_corrupt_old_api_manifest_refuses_without_worker_spawn


def _test_pair_root_change_refuses_both_stop_requests(self):
    proof=self.ctl.start_pair(self.argv,self.argv)
    path=self.root/'pair-generations'/'0000'/'manifest.json'
    manifest=json.loads(path.read_text())
    manifest['root']='/foreign/root'
    path.write_text(json.dumps(manifest))
    with self.assertRaises(LifecycleBlocked): self.ctl.stop_pair_and_seal(proof)
    for ctl in (self.ctl.api,self.ctl.worker):
        self.assertFalse((ctl.root/'ops-generations'/'0000'/'stop.request').exists())

DualProcessTests.test_pair_root_change_refuses_both_stop_requests=_test_pair_root_change_refuses_both_stop_requests


def _test_failed_api_start_rolls_back_worker_and_refuses_restart(self):
    from unittest.mock import patch
    def fail_start(*args, **kwargs):
        raise RuntimeError('injected startup failure')
    with patch.object(self.ctl.api, 'start', side_effect=fail_start):
        with self.assertRaises(LifecycleBlocked):
            self.ctl.start_pair(self.argv, self.argv)
    rollback=self.root/'pair-generations'/'0000'/'rollback.json'
    result=json.loads(rollback.read_text())
    self.assertEqual(result['status'],'SEALED_PARTIAL')
    self.assertEqual(list(result['sealed_children']),['worker'])
    self.assertEqual(self.ctl.worker.handles[0].poll(),0)
    self.assertTrue((self.ctl.worker.root/'ops-generations'/'0000'/'closed.json').exists())
    with self.assertRaises(LifecycleBlocked):
        self.ctl.start_pair(self.argv, self.argv)
    self.assertFalse((self.root/'pair-generations'/'0001').exists())


def _test_failed_api_after_spawn_rolls_back_both(self):
    from unittest.mock import patch
    actual=self.ctl.api.start
    def spawned_then_failed(*args,**kwargs):
        actual(*args,**kwargs)
        raise RuntimeError('injected after-process-create failure')
    with patch.object(self.ctl.api,'start',side_effect=spawned_then_failed):
        with self.assertRaises(LifecycleBlocked):
            self.ctl.start_pair(self.argv,self.argv)
    receipt=json.loads((self.root/'pair-generations'/'0000'/'rollback.json').read_text())
    self.assertEqual(receipt['status'],'SEALED_PARTIAL')
    self.assertEqual(set(receipt['sealed_children']),{'api','worker'})
    self.assertEqual(self.ctl.api.handles[0].poll(),0)
    self.assertEqual(self.ctl.worker.handles[0].poll(),0)
    self.assertFalse((self.root/'pair-generations'/'0000'/'closed.json').exists())


def _test_rollback_failure_preserves_live_worker_and_blocks_restart(self):
    from unittest.mock import patch
    with patch.object(self.ctl.api,'start',side_effect=RuntimeError('api failed')):
        with patch.object(self.ctl.worker,'stop_and_seal',side_effect=RuntimeError('unverified worker')):
            with self.assertRaises(LifecycleBlocked):
                self.ctl.start_pair(self.argv,self.argv)
    data=json.loads((self.root/'pair-generations'/'0000'/'rollback.json').read_text())
    self.assertEqual(data['status'],'BLOCKED')
    self.assertEqual(data['unverified_children'],['worker'])
    self.assertIsNone(self.ctl.worker.handles[0].poll())
    with self.assertRaises(LifecycleBlocked):
        self.ctl.start_pair(self.argv,self.argv)

DualProcessTests.test_failed_api_start_rolls_back_worker_and_refuses_restart=_test_failed_api_start_rolls_back_worker_and_refuses_restart
DualProcessTests.test_failed_api_after_spawn_rolls_back_both=_test_failed_api_after_spawn_rolls_back_both
DualProcessTests.test_rollback_failure_preserves_live_worker_and_blocks_restart=_test_rollback_failure_preserves_live_worker_and_blocks_restart


def _test_activation_receipt_bound_to_pair_manifest(self):
    from scripts.ops_disposable_lifecycle import _digest
    proof=self.ctl.start_pair(self.argv,self.argv)
    p=self.root/'pair-generations'/'0000'
    ready=json.loads((p/'activated.request').read_text())
    manifest=json.loads((p/'manifest.json').read_text())
    self.assertEqual(ready['manifest_sha256'],_digest(manifest))
    self.assertEqual(ready['generation'],proof.generation)
    self.ctl.stop_pair_and_seal(proof)


def _test_tampered_activation_refuses_stop_before_child_side_effect(self):
    proof=self.ctl.start_pair(self.argv,self.argv)
    p=self.root/'pair-generations'/'0000'/'activated.request'
    data=json.loads(p.read_text());data['manifest_sha256']='0'*64
    p.write_text(json.dumps(data))
    with self.assertRaises(LifecycleBlocked): self.ctl.stop_pair_and_seal(proof)
    for ctl in (self.ctl.api,self.ctl.worker):
        self.assertFalse((ctl.root/'ops-generations'/'0000'/'stop.request').exists())

DualProcessTests.test_activation_receipt_bound_to_pair_manifest=_test_activation_receipt_bound_to_pair_manifest
DualProcessTests.test_tampered_activation_refuses_stop_before_child_side_effect=_test_tampered_activation_refuses_stop_before_child_side_effect
