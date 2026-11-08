import unittest
from src.storage.knowledge_time import project_knowledge

class KnowledgeTimeTests(unittest.TestCase):
    def test_submillisecond_is_preserved_but_not_claimed_known(self):
        stamp='2026-11-07T12:00:00.000001+00:00'
        p=project_knowledge([{'kind':'observations','timestamps':{'source_recorded_at':stamp,'observation_recorded_at':stamp,'run_created_at':stamp}}])
        self.assertEqual(p['facts'][0]['timestamps']['source_recorded_at'],stamp)
        self.assertIsNone(p['quality_available_at_ms'])
        self.assertEqual(p['status'],'UNAVAILABLE')

    def test_backdated_fetch_and_event_are_not_knowledge(self):
        p=project_knowledge([{'kind':'rows','timestamps':{},'timestamp_ms':1,'fetched_at':'2000-01-01T00:00:00Z'}])
        self.assertIsNone(p['quality_available_at_ms'])
        self.assertIn('missing_or_unproven_fact_timing',p['reasons'])

    def test_later_links_corruption_resolution_remain_unproven(self):
        facts=[{'kind':k,'timestamps':{'created_at':'2026-11-07T12:00:00+00:00','resolved_at':'2026-11-07T13:00:00+00:00'}} for k in ('run_links','findings','intervals','archives')]
        p=project_knowledge(facts)
        self.assertEqual(p['facts'],facts)
        self.assertIsNone(p['quality_available_at_ms'])

    def test_empty_and_failure_have_no_availability(self):
        for reason in (None,'bytes_limit','observations_limit','database_read_failed'):
            p=project_knowledge([],reason)
            self.assertEqual(p['status'],'UNAVAILABLE')
            self.assertIsNone(p['quality_available_at_ms'])
            self.assertEqual(p['facts'],[])
            if reason: self.assertIn(reason,p['reasons'])


class BoundedPortTests(unittest.TestCase):
    def test_empty_snapshot_and_cap_failure_are_fail_closed(self):
        from unittest.mock import MagicMock, patch
        from src.storage.read_page import read_page, Unavailable
        store=MagicMock()
        connection=store.connect.return_value.__enter__.return_value
        connection.execute.return_value.fetchone.return_value={'isolation':'repeatable read','started_at':'2026-11-07T12:00:00+00:00','xmin':'1','clock_ms':2000000000000}
        with patch('src.storage.read_page.Budget.docs',return_value=iter(())):
            result=read_page(store,'trades','bybit','linear','BTCUSDT',0,100)
        self.assertEqual(result['knowledge_time']['status'],'UNAVAILABLE')
        self.assertIsNone(result['knowledge_time']['quality_available_at_ms'])
        self.assertFalse(result['coverage']['complete'])
        with patch('src.storage.read_page.Budget.docs',side_effect=Unavailable('observations_limit')):
            result=read_page(store,'trades','bybit','linear','BTCUSDT',0,100)
        self.assertEqual(result['coverage']['state'],'UNKNOWN')
        self.assertEqual(result['coverage']['proof_refs'],[])
        self.assertEqual(result['knowledge_time']['facts'],[])

    def test_projection_uses_existing_bounded_fetch_and_preserves_text(self):
        import json
        from unittest.mock import MagicMock
        from src.storage.read_page import Budget
        stamp='2026-11-07T12:00:00.000001+00:00'
        encoded=json.dumps({'source_id':'s','source_recorded_at':stamp,'observation_recorded_at':None,'run_created_at':stamp})
        c=MagicMock()
        c.cursor.return_value.__enter__.return_value.fetchone.side_effect=[{'n':1,'size':len(encoded),'encoded':encoded},None]
        b=Budget({'statement_timeout_ms':3000,'max_bytes':8192})
        list(b.docs(c,'SELECT doc,sort_key FROM fixture',(),1,'observations'))
        self.assertEqual(b.knowledge_facts[0]['timestamps']['source_recorded_at'],stamp)
        self.assertIsNone(b.knowledge_facts[0]['timestamps']['observation_recorded_at'])
        self.assertEqual(c.cursor.call_count,1)
        self.assertEqual(b.usage['observations'],1)

if __name__=='__main__': unittest.main()
