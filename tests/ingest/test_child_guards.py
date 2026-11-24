import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock,MagicMock
from tests.ingest import pinned_roundtrip as harness

class ChildGuardTests(unittest.TestCase):
    def test_both_modes_guard_before_work(self):
        for restart in (False,True):
            with self.subTest(restart=restart), patch.object(harness,'guard_child_context',side_effect=AssertionError('rejected')), patch.object(harness.unittest.defaultTestLoader,'loadTestsFromTestCase') as suite:
                with self.assertRaisesRegex(AssertionError,'rejected'):
                    harness.child_main(Path('unused'),restart)
                suite.assert_not_called()

    def test_guard_rejects_before_mutation(self):
        for failure in ('valid','external_dsn','pin','fixed_pin','hash','origin','restart_database','restart_handoff','restart_storage','restart_integrity'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); snapshot=root/'snapshot';snapshot.mkdir()
                names=['src.ingest.archive','src.ingest.journal','src.ingest.handoff','src.ingest.rest','src.storage.models','src.storage.store','src.storage.migrations','contracts.v1.validate']
                modules={};files={}
                for name in names:
                    relative=name.replace('.','/')+'.py';source=snapshot/relative
                    source.parent.mkdir(parents=True,exist_ok=True);source.write_text('fixture')
                    modules[name]=SimpleNamespace(__file__=str(source),Store=Mock())
                    files[relative]={'sha256':harness.hashlib.sha256(source.read_bytes()).hexdigest()}
                pins={'producer_sha':harness.PRODUCER_SHA,'storage_sha':harness.STORAGE_SHA,'contract_sha':harness.CONTRACT_SHA,'production_files':files}
                # Expected manifest is independently held by parent, not child-controlled pins.json.
                harness.write_json(root/'pins.json',pins)
                harness.write_json(root/'parent-context.json',{'pins_sha256':harness.hashlib.sha256((root/'pins.json').read_bytes()).hexdigest(),'restart_sha256':'expected'})
                fixture=root/'fixtures/test_replay_and_journal_restart';fixture.mkdir(parents=True)
                state={'database':'pair_'+'a'*32,'storage_root':str(fixture),'handoff':str(fixture/'equivalent/reopened.json'),'counts':{}}
                harness.write_json(root/'restart-state.json',state)
                if failure=='pin':pins['storage_sha']='wrong';harness.write_json(root/'pins.json',pins)
                if failure=='fixed_pin':
                    pins['storage_sha']='wrong';harness.write_json(root/'pins.json',pins)
                    harness.write_json(root/'parent-context.json',{'pins_sha256':harness.hashlib.sha256((root/'pins.json').read_bytes()).hexdigest()})
                if failure=='hash':source.write_text('altered')
                if failure=='origin':modules['src.storage.store'].__file__=str(root/'outside.py')
                if failure.startswith('restart_'):
                    if failure=='restart_database':state['database']='postgres'
                    if failure=='restart_handoff':state['handoff']=str(root/'outside.json')
                    if failure=='restart_storage':state['storage_root']=str(root)
                    harness.write_json(root/'restart-state.json',state)
                pg=MagicMock(); conn=pg.connect.return_value.__enter__.return_value
                conn.execute.return_value.fetchone.return_value=[str(root/('external' if failure=='external_dsn' else 'pgdata'))]
                restart_digest=harness.hashlib.sha256((root/'restart-state.json').read_bytes()).hexdigest()
                if failure=='restart_integrity':restart_digest='incorrect'
                with patch.dict('sys.modules',{'psycopg':pg}),patch.dict('os.environ',{'PINNED_PAIR_DSN':'fixture','PINNED_PAIR_PINS_SHA256':json.loads((root/'parent-context.json').read_text())['pins_sha256'],'PINNED_PAIR_RESTART_SHA256':restart_digest}),patch.object(harness.importlib,'import_module',side_effect=modules.__getitem__):
                    if failure=='valid':
                        for restart in (False,True):harness.guard_child_context(root,verify_restart=restart)
                    else:
                        for restart in ((True,) if failure.startswith('restart_') else (False,True)):
                            with self.subTest(restart=restart),patch.object(harness.unittest.defaultTestLoader,'loadTestsFromTestCase') as suite:
                                with self.assertRaises((AssertionError,ValueError)):
                                    harness.child_main(root,verify_restart=restart)
                                suite.assert_not_called()
                for call in conn.execute.call_args_list:self.assertEqual(call.args,('SHOW data_directory',))
                for module in modules.values():module.Store.assert_not_called()
