import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock
from src.storage.visibility_foundation import experimental_mutation, create_unenrolled_epoch, MUTATOR_TABLES

class FoundationTests(unittest.TestCase):
    def connection(self):
        c=MagicMock(); c.autocommit=True
        from psycopg.pq import TransactionStatus
        c.info.transaction_status=TransactionStatus.IDLE
        c.execute.return_value.fetchone.return_value={'locked':True,'status':'UNENROLLED','success_enabled':False}
        return c

    def test_gate_is_before_transaction_and_unlock_after(self):
        c=self.connection(); events=[]
        c.execute.side_effect=lambda sql,args=None: events.append(sql) or MagicMock(fetchone=lambda:{'locked':True,'status':'UNENROLLED','success_enabled':False})
        @contextmanager
        def tx():
            events.append('BEGIN'); yield; events.append('COMMIT')
        c.transaction.side_effect=tx
        with experimental_mutation(lambda:c,'external-epoch',['b','a'],['source_observations']) as got:
            self.assertIs(got,c); events.append('BODY')
        self.assertLess(next(i for i,e in enumerate(events) if 'pg_try_advisory_lock' in e),events.index('BEGIN'))
        self.assertLess(events.index('BODY'),events.index('COMMIT'))
        self.assertLess(events.index('COMMIT'),next(i for i,e in enumerate(events) if 'pg_advisory_unlock' in e))

    def test_old_snapshot_or_autocommit_false_refused_before_lock(self):
        from psycopg.pq import TransactionStatus
        for auto,status in ((False,TransactionStatus.IDLE),(True,TransactionStatus.INTRANS)):
            c=self.connection();c.autocommit=auto;c.info.transaction_status=status
            with self.assertRaises(ValueError):
                with experimental_mutation(lambda:c,'epoch',['a'],['raw_records']): pass
            c.execute.assert_not_called()

    def test_busy_gate_does_not_start_transaction(self):
        c=self.connection();c.execute.return_value.fetchone.return_value={'locked':False}
        with self.assertRaises(RuntimeError):
            with experimental_mutation(lambda:c,'epoch',['a'],['quality_findings']): pass
        c.transaction.assert_not_called()

    def test_body_crash_escapes_transaction_and_releases_gate(self):
        c=self.connection()
        with self.assertRaisesRegex(RuntimeError,'crash'):
            with experimental_mutation(lambda:c,'epoch',['a'],['finding_resolutions']): raise RuntimeError('crash')
        self.assertIs(c.transaction.return_value.__exit__.call_args.args[0],RuntimeError)
        self.assertIn('pg_advisory_unlock',c.execute.call_args.args[0])

    def test_generation_and_ledger_in_same_transaction_sorted_scopes(self):
        c=self.connection()
        with experimental_mutation(lambda:c,'epoch',['b','a','b'],['ingestion_run_evidence']): pass
        calls=c.execute.call_args_list
        inserts=[call for call in calls if 'INSERT INTO tester_visibility.scope_generations' in call.args[0]]
        self.assertEqual([call.args[1] for call in inserts],[('epoch','a'),('epoch','b')])
        self.assertTrue(any('mutation_events' in call.args[0] for call in calls))
        self.assertFalse(any('ENROLLED' in call.args[0] and "status='ENROLLED'" in call.args[0] for call in calls))

    def test_bad_scope_kind_or_epoch_rejected_without_connection(self):
        factory=MagicMock()
        for epoch,scopes,kinds in (('', ['a'],['raw_records']),('e',[],['raw_records']),('e',['a'],['unknown']),('e',['a'],[])):
            with self.assertRaises(ValueError):
                with experimental_mutation(factory,epoch,scopes,kinds): pass
        factory.assert_not_called()

    def test_epoch_creation_always_unenrolled(self):
        c=self.connection();create_unenrolled_epoch(lambda:c,'external-epoch')
        q=[call for call in c.execute.call_args_list if 'INSERT INTO tester_visibility.epochs' in call.args[0]][0]
        self.assertIn("'UNENROLLED',false",q.args[0])

    def test_inventory_covers_all_existing_storage_tables(self):
        import re
        from src.storage.migrations import MIGRATIONS
        existing=set()
        for path in MIGRATIONS.glob('*.sql'):
            existing.update(re.findall(r'CREATE TABLE tester_storage\.([a-z_]+)',path.read_text(encoding='utf-8-sig')))
        self.assertEqual(existing,MUTATOR_TABLES)

    def test_missing_or_enabled_epoch_rejected(self):
        for epoch in (None,{'status':'ENROLLED','success_enabled':True}):
            c=self.connection()
            c.execute.side_effect=[MagicMock(fetchone=lambda:{'locked':True}),MagicMock(),MagicMock(fetchone=lambda:epoch),MagicMock()]
            with self.assertRaises(RuntimeError):
                with experimental_mutation(lambda:c,'epoch',['a'],['raw_records']): self.fail('must not yield')
            c.close.assert_called_once()

    def test_ledger_failure_rolls_back_via_same_transaction(self):
        c=self.connection()
        def execute(sql,args=None):
            if 'mutation_events' in sql: raise RuntimeError('ledger failed')
            return MagicMock(fetchone=lambda:{'locked':True,'status':'UNENROLLED','success_enabled':False})
        c.execute.side_effect=execute
        with self.assertRaisesRegex(RuntimeError,'ledger failed'):
            with experimental_mutation(lambda:c,'epoch',['a','b'],['lifecycle_quarantine']): pass
        self.assertIs(c.transaction.return_value.__exit__.call_args.args[0],RuntimeError)
        c.close.assert_called_once()

    def test_schema_cannot_enable_success_or_bounds_and_has_no_grants(self):
        from src.storage.migrations import MIGRATIONS
        sql=(MIGRATIONS/'007_visibility_foundation.sql').read_text(encoding='utf-8-sig')
        for constraint in ("CHECK(status='UNENROLLED')",'CHECK(success_enabled=false)',
                           'CHECK(capture_observed_by_ms IS NULL)',
                           'CHECK(receipt_publication_observed_by_ms IS NULL)'):
            self.assertIn(constraint,sql)
        self.assertNotIn('GRANT ',sql)
        self.assertNotIn('ALTER TABLE tester_storage.',sql)

    def test_caps_reject_before_opening_connection(self):
        factory=MagicMock()
        for scopes,kinds in ((['a']*65,['raw_records']),(['a'],['raw_records']*33),(['a'*201],['raw_records'])):
            with self.assertRaises(ValueError):
                with experimental_mutation(factory,'epoch',scopes,kinds): pass
        factory.assert_not_called()

    def test_gate_error_closes_owned_connection_without_transaction(self):
        c=self.connection();c.execute.side_effect=RuntimeError('connection failed')
        with self.assertRaisesRegex(RuntimeError,'connection failed'):
            with experimental_mutation(lambda:c,'epoch',['a'],['raw_records']): pass
        c.transaction.assert_not_called();c.close.assert_called_once()

if __name__=='__main__': unittest.main()
