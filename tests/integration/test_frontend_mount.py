"""Actual E routes serving D's owned source assets; no browser mocks or market data."""
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from src.api.config import Settings
from src.api.main import create_app


class FrontendMountTests(unittest.TestCase):
    def client(self,directory):
        frontend=Path(__file__).resolve().parents[2]/'frontend'
        return TestClient(create_app(Settings(data_dir=directory,frontend_dir=frontend)),base_url='http://127.0.0.1:8765')

    def test_actual_frontend_and_module_are_served_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory,self.client(directory) as client:
            page=client.get('/')
            self.assertEqual(page.status_code,200)
            self.assertIn('app.js',page.text)
            self.assertNotIn('Frontend not installed',page.text)
            script=client.get('/app.js')
            self.assertEqual(script.status_code,200)
            self.assertEqual(script.content,(Path(__file__).resolve().parents[2]/'frontend/app.js').read_bytes())

    def test_missing_backend_is_explicit_503_not_static_html_or_empty_success(self):
        with tempfile.TemporaryDirectory() as directory,self.client(directory) as client:
            response=client.get('/api/instruments')
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.json()['error']['code'],'provider_unavailable')
            self.assertNotIn('data',response.json())
            self.assertEqual(client.get('/api/analytics').status_code,404)
