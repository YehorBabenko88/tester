import copy
import unittest
from src.integration.storage_api import IntegrationUnavailable


class StorageProvenanceTests(unittest.TestCase):
    def blob(self):
        return dict(authentic=True,unlinked_sources=[],observations=[dict(
            authentic_run=True,source_kind='archive',run_id='actual-acquisition',
            evidence_id='evidence-1',timestamp_precision='ns',
            source=dict(url='https://public.bybit.com/BTCUSDT/file.csv.gz',
                        sha256='a'*64,source_timestamp='1700000000.123456789'))])

    def convert(self,value):
        from src.integration.storage_api import provenance_from_storage
        return provenance_from_storage(value)

    def test_exact_run_hash_timestamp_and_all_observations_survive(self):
        value=self.blob();second=copy.deepcopy(value['observations'][0]);second['run_id']='second-acquisition'
        value['observations'].append(second);original=copy.deepcopy(value)
        result=self.convert(value)
        self.assertEqual([p['ingestion_run'] for p in result],['actual-acquisition','second-acquisition'])
        self.assertEqual(result[0]['source_timestamp'],'1700000000.123456789')
        self.assertEqual(result[0]['source_hash'],'a'*64)
        self.assertEqual(result[0]['source_kind'],'bybit_archive');self.assertEqual(value,original)

    def test_legacy_unlinked_or_noncaller_run_fails_closed(self):
        for change in ('authentic','unlinked','run'):
            value=self.blob()
            if change=='authentic':value['authentic']=False
            if change=='unlinked':value['unlinked_sources']=[{'source_id':'legacy'}]
            if change=='run':value['observations'][0]['authentic_run']=False
            with self.subTest(change=change):self.assertRaises(IntegrationUnavailable,self.convert,value)

    def test_unknown_import_and_missing_run_are_not_invented(self):
        for field,value in [('source_kind','import'),('source_kind','unknown'),('run_id',''),('evidence_id','')]:
            blob=self.blob();blob['observations'][0][field]=value
            with self.subTest(field=field,value=value):self.assertRaises(IntegrationUnavailable,self.convert,blob)

    def test_missing_original_timestamp_and_hash_remain_null(self):
        value=self.blob();value['observations'][0]['source']={'url':'https://api.bybit.com/v5/market/recent-trade'}
        value['observations'][0]['source_kind']='rest'
        result=self.convert(value)[0]
        self.assertIsNone(result['source_hash']);self.assertIsNone(result['source_timestamp'])
        self.assertEqual(result['source_kind'],'bybit_rest')

    def test_invalid_types_and_unbounded_observations_fail_closed(self):
        for value in (None,{},dict(authentic=True,unlinked_sources=[],observations=[])):
            with self.subTest(value=value):self.assertRaises(IntegrationUnavailable,self.convert,value)
        value=self.blob();value['observations']*=10001
        self.assertRaises(IntegrationUnavailable,self.convert,value)

    def test_bad_source_metadata_cannot_reach_common_contract(self):
        for field,value in [('url',''),('sha256','bad'),('source_timestamp',1.23)]:
            blob=self.blob();blob['observations'][0]['source'][field]=value
            with self.subTest(field=field):self.assertRaises(IntegrationUnavailable,self.convert,blob)

    def test_explicit_original_run_mismatch_is_not_silently_replaced(self):
        value=self.blob();value['observations'][0]['source']['ingestion_run']='original-acquisition'
        self.assertRaises(IntegrationUnavailable,self.convert,value)
        value['observations'][0]['source']['ingestion_run']='actual-acquisition'
        self.assertEqual(self.convert(value)[0]['ingestion_run'],'actual-acquisition')

    def test_strong_binding_requires_actual_original_declaration(self):
        from src.integration.storage_api import provenance_from_storage
        value=self.blob()
        self.assertRaises(IntegrationUnavailable,provenance_from_storage,value,require_declared_run=True)
        observation=value['observations'][0]
        observation.update(source_run_status='matched',source_ingestion_run='actual-acquisition')
        observation['source']['ingestion_run']='actual-acquisition'
        self.assertEqual(provenance_from_storage(value,require_declared_run=True)[0]['ingestion_run'],'actual-acquisition')
        for status in ('undeclared','invalid','mismatched'):
            observation['source_run_status']=status
            with self.subTest(status=status):
                self.assertRaises(IntegrationUnavailable,provenance_from_storage,value,require_declared_run=True)
