"""Reproduce defects in pinned initial C HEAD without modifying production."""
import importlib.util
from pathlib import Path
import unittest
from tests.analytics import test_initial_regressions as regression

if __name__=='__main__':
    path=Path(__file__).with_name('reference')/'initial_footprint.py'
    spec=importlib.util.spec_from_file_location('initial_footprint',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    regression.aggregate=module.aggregate
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(regression.InitialRegressions)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
