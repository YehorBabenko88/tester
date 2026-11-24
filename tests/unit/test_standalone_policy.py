from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from src.integration.standalone_policy import validate_factory_spec,assert_loaded_origin,StandaloneViolation


class StandalonePolicyTests(unittest.TestCase):
    def fixture(self):
        tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);path=root/'src'/'integration'/'provider.py';path.parent.mkdir(parents=True)
        path.write_text('def factory(): return None\n',encoding='utf-8')
        return root,path
    def test_owned_factory_is_resolved_without_importing_code(self):
        root,path=self.fixture();target=validate_factory_spec('src.integration.provider:factory',root)
        self.assertEqual(target.source_path,path.resolve());self.assertEqual(target.symbol,'factory')
        assert_loaded_origin(target,SimpleNamespace(__file__=str(path)))
    def test_foreign_repository_factories_are_rejected(self):
        root,_=self.fixture()
        for spec in ['Unified.gateway:factory','Grid.client:factory','Strattester.api:factory',
                     'Math.api:factory','MetaAnalyse.provider:factory','Terminan.gateway:factory']:
            with self.subTest(spec=spec):self.assertRaises(StandaloneViolation,validate_factory_spec,spec,root)
    def test_urls_paths_traversal_and_code_are_not_factory_specs(self):
        root,_=self.fixture()
        for spec in ['../Unified/api.py:factory','https://localhost/factory','C:/other/provider.py:factory',
                     'src.integration.provider:factory()','src.integration.provider:__dict__']:
            self.assertRaises(StandaloneViolation,validate_factory_spec,spec,root)
    def test_missing_module_is_not_loaded_from_global_python_path(self):
        root,_=self.fixture()
        self.assertRaises(StandaloneViolation,validate_factory_spec,'src.api.foreign:factory',root)
    def test_loaded_module_origin_cannot_shadow_owned_factory(self):
        root,path=self.fixture();target=validate_factory_spec('src.integration.provider:factory',root)
        self.assertRaises(StandaloneViolation,assert_loaded_origin,target,SimpleNamespace(__file__=str(root/'foreign.py')))
    def test_ambiguous_package_module_is_not_guessed(self):
        root,_=self.fixture();package=root/'src'/'integration'/'provider';package.mkdir();(package/'__init__.py').write_text('')
        self.assertRaises(StandaloneViolation,validate_factory_spec,'src.integration.provider:factory',root)


if __name__=='__main__':unittest.main()
