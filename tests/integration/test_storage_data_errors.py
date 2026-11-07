"""Connected storage failures must not masquerade as a disconnected provider."""
import copy
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from src.api.config import Settings
from src.api.main import create_app
import test_storage_trade_reader as fixtures


class StorageDataErrorTests(unittest.TestCase):
    def request(self,mode,resource='trades'):
        rows=fixtures.TradeReaderTests().rows()
        if mode in ('provenance','integrity','unavailable'):
            rows[0]['provenance']['authentic']=False
            rows[0]['provenance']['private_diagnostic']='token-MUST-NOT-LEAK'
        if mode=='contract':rows[0]['payload']['price']=1.234
        class Store:
            def read(self,*args,**kwargs):return copy.deepcopy(rows[:kwargs['limit']])
            def coverage(self,*args):return dict(start=1700000000000,end=1700000060000,
                complete=False,verified=[],unknown=[[1700000000000,1700000060000]],
                corrupt=[[1700000000000,1700000060000]] if mode=='integrity' else [],
                unavailable=[[1700000000000,1700000060000]] if mode=='unavailable' else [])
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings(data_dir=directory,database_url='postgresql://fixture@127.0.0.1/tester_ci',
                provider_factory='src.integration.storage_reader:create_reader')
            with patch('src.api.config.load_settings',return_value=settings),patch('src.storage.store.Store',return_value=Store()):
                with TestClient(create_app(settings),base_url='http://127.0.0.1:8765') as client:
                    return client.get('/api/'+resource,params=dict(symbol='BTCUSDT',start_ms=1700000000000,end_ms=1700000060000,limit=1))

    def test_unverified_original_run_is_data_unavailable_not_disconnected(self):
        response=self.request('provenance')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['error']['code'],'data_unavailable')
        self.assertIn('provenance',response.json()['error']['message'].lower())
        self.assertNotIn('not connected',response.text)
        self.assertNotIn('token-MUST-NOT-LEAK',response.text)

    def test_bad_canonical_metadata_has_its_own_safe_reason(self):
        response=self.request('contract')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['error']['code'],'data_unavailable')
        self.assertIn('canonical',response.json()['error']['message'].lower())
        self.assertNotIn('1.234',response.text)

    def test_unconnected_resource_remains_provider_unavailable(self):
        response=self.request('valid','candles')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['error']['code'],'provider_unavailable')

    def test_known_integrity_block_is_not_downgraded_to_provenance_only(self):
        response=self.request('integrity')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['error']['code'],'data_unavailable')
        self.assertIn('integrity',response.json()['error']['message'].lower())

    def test_raw_diagnostics_are_not_valid_public_reasons(self):
        from src.api.providers import ProviderDataUnavailable
        self.assertRaises(ValueError,ProviderDataUnavailable,'token-MUST-NOT-LEAK')

    def test_unavailable_range_does_not_claim_corruption(self):
        response=self.request('unavailable')
        self.assertEqual(response.status_code,503)
        self.assertIn('marked unavailable',response.json()['error']['message'].lower())
        self.assertNotIn('integrity',response.text)
