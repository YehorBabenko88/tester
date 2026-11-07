"""Real PostgreSQL only; no fake database success. Opt in with TESTER_H_TEST_DSN."""
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from test_core import row
from test_research import dataset
from src.trader_intelligence.store import Store
from src.trader_intelligence.service import Research


@unittest.skipUnless(os.getenv('TESTER_H_TEST_DSN'),'isolated PostgreSQL DSN required')
class PostgresTests(unittest.TestCase):
    def test_store_cannot_bypass_import_admission(self):
        from copy import deepcopy
        from src.trader_intelligence.imports import digest
        for mutate in (lambda r:r.update(email='private@example.com'),
                       lambda r:r.update(source='unconsented_source')):
            d=deepcopy(self.d); mutate(d['events'][0])
            d['dataset_revision']=digest({k:v for k,v in d.items() if k!='dataset_revision'})
            with self.assertRaises(ValueError): self.store.put('new-'+uuid.uuid4().hex,d)

    def setUp(self):
        self.store=Store(os.environ['TESTER_H_TEST_DSN'])
        self.store.migrate()
        self.tenant='fixture-'+uuid.uuid4().hex
        self.d=dataset([row(),row(event_id='close',side='SELL',quantity='1',price='120',timestamp_ms=2100,observed_at_ms=2200)])
        self.rev=self.store.put(self.tenant,self.d)
        self.service=Research(self.store)
        self.query=dict(dataset_revision=self.rev,event_id='e1',source='authorized_export',
                        trader_id='public-alias',venue='bybit',category='linear',symbol='BTCUSDT',
                        start_ms=0,end_ms=3000,as_of_ms=3000)

    def run_analysis(self,request='r1',query=None):
        plan=self.service.plan(self.tenant,query or self.query)
        return self.service.run(self.tenant,request,plan,approved_digest=plan['plan_digest'])

    def test_selected_trade_to_artifact_ack_restart(self):
        r=self.run_analysis()
        self.assertEqual(r['reconstruction']['gross_realized_pnl'],'20.00')
        self.assertEqual(len(r['overlays']),2)
        s=Research(Store(os.environ['TESTER_H_TEST_DSN']))
        self.assertEqual(s.read_run(self.tenant,'r1'),r)
        self.assertEqual(s.ack(self.tenant,'r1',r['artifact_digest'],'fixture-renderer-v1')['status'],'RENDERED')

    def test_concurrent_idempotent_and_conflict(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.run_analysis(),range(12)))
        self.assertEqual(len({r['artifact_digest'] for r in results}),1)
        q=dict(self.query,as_of_ms=2500)
        with self.assertRaises(ValueError): self.run_analysis(query=q)

    def test_revoke_invalidates_reads_runs_and_ack(self):
        r=self.run_analysis()
        self.store.revoke(self.tenant,'authorized_export')
        for action in (lambda:self.service.plan(self.tenant,self.query),
                       lambda:self.service.read_run(self.tenant,'r1'),
                       lambda:self.service.ack(self.tenant,'r1',r['artifact_digest'],'v1')):
            with self.assertRaises(PermissionError): action()

    def test_tenant_and_approval_denied(self):
        with self.assertRaises(LookupError): self.service.plan('other',self.query)
        p=self.service.plan(self.tenant,self.query)
        with self.assertRaises(PermissionError): self.service.run(self.tenant,'r1',p,approved_digest='wrong')
        with self.assertRaises(ValueError): self.service.plan(self.tenant,dict(self.query,sql='SELECT 1'))

    def test_as_of_incomplete_does_not_reconstruct(self):
        r=self.run_analysis(query=dict(self.query,as_of_ms=2100))
        self.assertEqual(r['reconstruction']['status'],'UNKNOWN')
        self.assertEqual(len(r['overlays']),1)

    def test_ten_instruments_parallel(self):
        d=dataset([row(symbol='TEST'+str(i)) for i in range(10)],source_revision='ten')
        rev=self.store.put(self.tenant,d)
        def one(i):
            return self.run_analysis('instrument'+str(i),dict(self.query,dataset_revision=rev,symbol='TEST'+str(i)))
        with ThreadPoolExecutor(max_workers=10) as pool: results=list(pool.map(one,range(10)))
        self.assertTrue(all(len(r['overlays'])==1 for r in results))

    def test_failed_transaction_leaves_no_request(self):
        p=self.service.plan(self.tenant,self.query)
        with self.assertRaises(RuntimeError):
            with self.store.connection() as c:
                self.store.save_run(c,self.tenant,'aborted','d',self.rev,{'fixture':True})
                raise RuntimeError('simulate interruption before commit')
        with self.assertRaises(LookupError): self.service.read_run(self.tenant,'aborted')
        self.assertIsNotNone(self.service.run(self.tenant,'aborted',p,approved_digest=p['plan_digest']))

    def test_duplicate_import_and_evidence_conflict(self):
        self.assertEqual(self.store.put(self.tenant,self.d),self.rev)
        changed=dataset([row(price='999')])
        with self.assertRaises(ValueError): self.store.put(self.tenant,changed)

    def test_page_bounds_and_digest_ack(self):
        p=self.service.events(self.tenant,self.query,limit=1)
        q=self.service.events(self.tenant,self.query,limit=1,cursor=p['next_cursor'])
        self.assertEqual(len(p['data'])+len(q['data']),2)
        self.assertIsNone(q['next_cursor'])
        with self.assertRaises(ValueError): self.service.events(self.tenant,self.query,limit=501)
        self.run_analysis()
        with self.assertRaises(ValueError): self.service.ack(self.tenant,'r1','bad','v1')
