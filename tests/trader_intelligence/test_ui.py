import importlib.util
import os
import unittest


@unittest.skipUnless(importlib.util.find_spec('streamlit') and os.getenv('TESTER_H_TEST_DSN'),
                     'Streamlit and isolated PG required')
class WorkspaceTests(unittest.TestCase):
    def test_select_approve_calculate_chart(self):
        from streamlit.testing.v1 import AppTest
        from unittest.mock import patch
        with patch.dict(os.environ,{'TESTER_H_DSN':os.environ['TESTER_H_TEST_DSN']}):
            app=AppTest.from_file('research_app.py').run(timeout=15)
            app.button[0].click().run(timeout=15)
            app.button[2].click().run(timeout=15)
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get('plotly_chart')),1)
            self.assertTrue(app.session_state['result']['synthetic'])
