import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from scripts.ops_disposable_lifecycle import (DisposableLifecycle, LifecycleBlocked,
    observe_owned, resource_receipt)

CHILD = '''import json, os, pathlib, time
stop=pathlib.Path(os.environ["OPS_STOP_PATH"])
while not stop.exists(): time.sleep(.02)
pathlib.Path(os.environ["OPS_CHECKPOINT_PATH"]).write_text(json.dumps({
  "generation":int(os.environ["OPS_GENERATION"]),
  "owner_marker":os.environ["OPS_OWNER_MARKER"],"inflight":"durably-recorded"}))
'''

class ExactLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctl = DisposableLifecycle(Path(self.tmp.name), "disposable-owner-marker-12345")
        self.argv = [str(Path(sys.executable).resolve()), '-u', '-c', CHILD]
        self.started=[]
        self.addCleanup(self.cooperative_cleanup)

    def cooperative_cleanup(self):
        for g, p in enumerate(self.started):
            if p.poll() is None:
                path = Path(self.tmp.name)/'ops-generations'/f'{g:04d}'/'stop.request'
                try: path.touch(exist_ok=True)
                except OSError: pass
                try: p.wait(timeout=4)
                except subprocess.TimeoutExpired: pass  # never force kill

    def launch(self):
        owner=self.ctl.start(self.argv)
        self.started.append(self.ctl.handles[owner.generation])
        return owner

    def test_two_generations_same_root_real_cooperative_exit(self):
        first=self.launch()
        self.assertEqual(self.ctl.stop_and_seal(0, expected=first)['exit_code'],0)
        second=self.launch()
        self.assertNotEqual(first.pid, second.pid)
        self.assertEqual(self.ctl.stop_and_seal(1, expected=second)['exit_code'],0)
        root=Path(self.tmp.name)/'ops-generations'
        for g in (0,1):
            self.assertTrue((root/f'{g:04d}'/'manifest.json').exists())
            self.assertTrue((root/f'{g:04d}'/'closed.json').exists())
            self.assertEqual(json.loads((root/f'{g:04d}'/'checkpoint.json').read_text())['generation'],g)

    def test_live_restart_refused_without_prior_closure(self):
        self.launch()
        with self.assertRaises(LifecycleBlocked): self.ctl.start(self.argv)
        self.assertEqual(len(self.ctl.handles),1)

    def test_foreign_and_stale_identity_refused_before_stop_marker(self):
        owner=self.launch()
        cases=[replace(owner,pid=owner.pid+1), replace(owner,created=owner.created+10),
               replace(owner,image=owner.image+'-foreign'),
               replace(owner,username='foreign'), replace(owner,marker='foreign')]
        for fake in cases:
            with self.assertRaises(LifecycleBlocked): observe_owned(self.started[0],fake)
            with self.assertRaises(LifecycleBlocked): self.ctl.stop_and_seal(0,expected=fake)
        self.assertFalse((Path(self.tmp.name)/'ops-generations'/'0000'/'stop.request').exists())
        self.ctl.stop_and_seal(0)

    def test_occupied_stop_marker_refuses_even_when_owned(self):
        self.launch()
        path=Path(self.tmp.name)/'ops-generations'/'0000'/'stop.request'
        path.write_text('foreign')
        with self.assertRaises(LifecycleBlocked): self.ctl.stop_and_seal(0)

    def test_resource_unknowns_explicit(self):
        owner=self.launch()
        values=resource_receipt(self.started[0],owner)
        self.assertGreater(values['rss_bytes'],0)
        self.assertFalse(values['limits_enforced'])
        self.assertIsInstance(values['unknown_fields'],list)
        self.ctl.stop_and_seal(0)

if __name__=='__main__': unittest.main()

# Extra failure-mode regressions are attached to the existing suite.
def _test_tampered_manifest_refuses_stop(self):
    self.launch()
    path=Path(self.tmp.name)/'ops-generations'/'0000'/'manifest.json'
    manifest=json.loads(path.read_text())
    manifest['root']='/foreign/root'
    path.write_text(json.dumps(manifest))
    with self.assertRaises(LifecycleBlocked): self.ctl.stop_and_seal(0)
    self.assertFalse((path.parent/'stop.request').exists())


def _test_tampered_checkpoint_refuses_restart(self):
    self.launch()
    self.ctl.stop_and_seal(0)
    path=Path(self.tmp.name)/'ops-generations'/'0000'/'checkpoint.json'
    path.write_text('forged')
    with self.assertRaises(LifecycleBlocked): self.ctl.start(self.argv)
    self.assertFalse((path.parent.parent/'0001').exists())


def _test_source_change_refuses_restart(self):
    self.launch()
    self.ctl.stop_and_seal(0)
    with self.assertRaises(LifecycleBlocked): self.ctl.start([*self.argv,'--changed'])
    self.assertFalse((Path(self.tmp.name)/'ops-generations'/'0001').exists())


def _test_stop_timeout_does_not_kill(self):
    slow_script=CHILD.replace('pathlib.Path(os.environ["OPS_CHECKPOINT_PATH"]).write_text',
                              'time.sleep(.35)\npathlib.Path(os.environ["OPS_CHECKPOINT_PATH"]).write_text')
    self.argv=[str(Path(sys.executable).resolve()),'-u','-c',slow_script]
    owner=self.launch()
    with self.assertRaises(LifecycleBlocked): self.ctl.stop_and_seal(0,timeout=0.01)
    self.assertIsNone(self.started[0].poll())
    self.assertFalse((Path(self.tmp.name)/'ops-generations'/'0000'/'closed.json').exists())

ExactLifecycleTests.test_tampered_manifest_refuses_stop=_test_tampered_manifest_refuses_stop
ExactLifecycleTests.test_tampered_checkpoint_refuses_restart=_test_tampered_checkpoint_refuses_restart
ExactLifecycleTests.test_source_change_refuses_restart=_test_source_change_refuses_restart
ExactLifecycleTests.test_stop_timeout_does_not_kill=_test_stop_timeout_does_not_kill


def _test_ancestor_checkpoint_tamper_blocks_third_generation(self):
    self.launch()
    self.ctl.stop_and_seal(0)
    self.launch()
    self.ctl.stop_and_seal(1)
    root=Path(self.tmp.name)/'ops-generations'
    (root/'0000'/'checkpoint.json').write_text('tampered')
    with self.assertRaises(LifecycleBlocked): self.ctl.start(self.argv)
    self.assertFalse((root/'0002').exists())


def _test_unknown_history_file_refuses(self):
    self.launch()
    self.ctl.stop_and_seal(0)
    root=Path(self.tmp.name)/'ops-generations'
    (root/'0000'/'foreign.marker').write_text('unowned')
    with self.assertRaises(LifecycleBlocked): self.ctl.start(self.argv)
    self.assertFalse((root/'0001').exists())


def _test_symlinked_checkpoint_refuses(self):
    self.launch()
    self.ctl.stop_and_seal(0)
    root=Path(self.tmp.name)/'ops-generations'
    evidence=root/'0000'/'checkpoint.json'
    original=root/'original.json'
    evidence.rename(original)
    evidence.symlink_to(original)
    with self.assertRaises(LifecycleBlocked): self.ctl.start(self.argv)
    self.assertFalse((root/'0001').exists())


def _test_unknown_manifest_schema_refuses_stop(self):
    self.launch()
    root=Path(self.tmp.name)/'ops-generations'
    manifest=root/'0000'/'manifest.json'
    value=json.loads(manifest.read_text())
    value['schema']='unrecognized'
    manifest.write_text(json.dumps(value))
    with self.assertRaises(LifecycleBlocked): self.ctl.stop_and_seal(0)
    self.assertFalse((root/'0000'/'stop.request').exists())

ExactLifecycleTests.test_ancestor_checkpoint_tamper_blocks_third_generation = _test_ancestor_checkpoint_tamper_blocks_third_generation
ExactLifecycleTests.test_unknown_history_file_refuses = _test_unknown_history_file_refuses
ExactLifecycleTests.test_symlinked_checkpoint_refuses = _test_symlinked_checkpoint_refuses
ExactLifecycleTests.test_unknown_manifest_schema_refuses_stop = _test_unknown_manifest_schema_refuses_stop


def _test_changed_config_refuses_restart(self):
    fixed = {"OPS_JOB_ID": "fixture-one", "TEST_OPS_ISOLATED_DSN": "disposable-only"}
    owner = self.ctl.start(self.argv, environment=fixed)
    self.started.append(self.ctl.handles[0])
    self.ctl.stop_and_seal(0, expected=owner)
    wrong = dict(fixed, OPS_JOB_ID="foreign-job")
    with self.assertRaises(LifecycleBlocked): self.ctl.start(self.argv, environment=wrong)
    self.assertFalse((Path(self.tmp.name)/'ops-generations'/'0001').exists())

ExactLifecycleTests.test_changed_config_refuses_restart = _test_changed_config_refuses_restart
