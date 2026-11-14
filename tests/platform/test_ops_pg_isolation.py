import os
import unittest
from unittest.mock import patch

try:
    import psycopg  # noqa: F401
    _HAS_PSYCOPG = True
except ImportError:
    _HAS_PSYCOPG = False

from scripts.ops_pg_isolation import require_isolated_dsn

BASE = 'host=127.0.0.1 port=55479 dbname=ops_isolated user=ops_tester password=isolated-only'


@unittest.skipUnless(_HAS_PSYCOPG, 'NOT_RUN: psycopg unavailable in local sandbox')
class IsolatedDsnPolicyTests(unittest.TestCase):
    def test_exact_disposable_dsn_accepted(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(require_isolated_dsn(BASE), BASE)

    def test_non_disposable_port_and_hostaddr_refused(self):
        for dsn in (BASE.replace('55479', '5432'), BASE+' hostaddr=127.0.0.2',
                    BASE+' options=-csearch_path=public', BASE+' service=foreign'):
            with self.subTest(dsn=dsn), patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(RuntimeError):
                    require_isolated_dsn(dsn)

    def test_environment_override_refused(self):
        for key in ('PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS'):
            with self.subTest(variable=key), patch.dict(os.environ, {key:'foreign'}, clear=True):
                with self.assertRaises(RuntimeError):
                    require_isolated_dsn(BASE)

if __name__ == '__main__': unittest.main()
