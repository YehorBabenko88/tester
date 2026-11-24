import json
from pathlib import Path
import tempfile
import unittest


class PhysicalJournalTests(unittest.TestCase):
    def scope(self,directory):
        from src.integration.physical_journal import PhysicalJournal
        root=Path(directory)
        return PhysicalJournal(root/'state/journal.json',root=root,installation_id='fixture-installation',host_fingerprint='fixture-host')

    def test_restart_dedup_and_three_failure_budget_persist(self):
        from src.integration.physical_journal import RepairBlocked
        with tempfile.TemporaryDirectory() as directory:
            journal=self.scope(directory)
            for i in range(3):
                self.assertTrue(journal.repair_result('attempt-'+str(i),'APP_CODE:SERVICE_START',success=False))
                journal=self.scope(directory)
            self.assertEqual(journal.state['phase'],'BLOCKED')
            self.assertTrue(journal.state['investigation_required'])
            self.assertFalse(journal.repair_result('attempt-2','APP_CODE:SERVICE_START',success=False))
            self.assertRaises(RepairBlocked,journal.repair_result,'attempt-3','APP_CODE:SERVICE_START',success=False)
            self.assertEqual(journal.state['failed_repairs']['APP_CODE:SERVICE_START'],3)

    def test_diagnostics_do_not_consume_repair_budget_and_no_false_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=self.scope(directory)
            journal.observe('preflight','PREFLIGHT','ENVIRONMENT:SCM_ACCESS_DENIED:5')
            self.assertEqual(journal.state['failed_repairs'],{})
            self.assertRaises(ValueError,journal.observe,'fake-accept','ACCEPTED','UNIT_TEST:PASS')
            self.assertRaises(ValueError,journal.observe,'secret','PREFLIGHT','postgresql://private:password@host')

    def test_foreign_owner_or_outside_path_refused_without_rewrite(self):
        from src.integration.physical_journal import PhysicalJournal,JournalConflict
        with tempfile.TemporaryDirectory() as directory:
            journal=self.scope(directory);before=journal.path.read_bytes()
            self.assertRaises(JournalConflict,PhysicalJournal,journal.path,root=directory,installation_id='foreign',host_fingerprint='fixture-host')
            self.assertEqual(journal.path.read_bytes(),before)
            self.assertRaises(ValueError,PhysicalJournal,Path(directory).parent/'outside.json',root=directory,installation_id='fixture-installation',host_fingerprint='fixture-host')

    def test_stale_writer_cannot_overwrite_new_checkpoint(self):
        from src.integration.physical_journal import JournalConflict
        with tempfile.TemporaryDirectory() as directory:
            first=self.scope(directory);stale=self.scope(directory)
            first.observe('first','SNAPSHOT','SNAPSHOT:SOURCE_CHECKED')
            self.assertRaises(JournalConflict,stale.observe,'stale','INSTALL','INSTALL:PLANNED')
            self.assertEqual(json.loads(first.path.read_text())['events'][-1]['operation_id'],'first')
