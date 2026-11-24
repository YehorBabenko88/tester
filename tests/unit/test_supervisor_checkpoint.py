import json
from pathlib import Path
import tempfile
import unittest
from src.integration.supervisor_checkpoint import task_key, remember_task, save_checkpoint, load_checkpoint, cycle_lock, ActiveCycle


class SupervisorCheckpointTests(unittest.TestCase):
    def checkpoint(self):return {'schema_version':1,'repository':'YehorBabenko88/tester','tasks':{}}
    def test_same_defect_and_head_is_not_reissued(self):
        value=self.checkpoint();key=task_key(13,'5'*40,'run-provenance')
        self.assertTrue(remember_task(value,key,comment_id=123,status='CHANGES_REQUESTED'))
        self.assertFalse(remember_task(value,key,comment_id=456,status='CHANGES_REQUESTED'))
        self.assertEqual(value['tasks'][key]['comment_id'],123)
        self.assertNotEqual(key,task_key(13,'6'*40,'run-provenance'))
    def test_atomic_round_trip_and_foreign_repository_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path=Path(tmp)/'checkpoint.json';save_checkpoint(path,self.checkpoint())
            self.assertEqual(load_checkpoint(path),self.checkpoint())
            value=self.checkpoint();value['repository']='different/repo'
            self.assertRaises(ValueError,save_checkpoint,path,value)
            self.assertEqual(load_checkpoint(path),self.checkpoint())
    def test_overlapping_cycle_is_blocked_and_owned_lock_released(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path=Path(tmp)/'cycle.lock'
            with cycle_lock(path,'cycle-1'):
                with self.assertRaises(ActiveCycle):
                    with cycle_lock(path,'cycle-2'):pass
                self.assertEqual(json.loads(path.read_text())['cycle_id'],'cycle-1')
            self.assertFalse(path.exists())
    def test_failed_cycle_releases_its_lock(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path=Path(tmp)/'cycle.lock'
            with self.assertRaises(RuntimeError):
                with cycle_lock(path,'cycle-1'):raise RuntimeError('fixture')
            self.assertFalse(path.exists())


if __name__=='__main__':unittest.main()

