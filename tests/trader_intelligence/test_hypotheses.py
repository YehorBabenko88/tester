import unittest
import os
import json
from pathlib import Path
from src.trader_intelligence.hypotheses import entry_context, simulation_plan, simulate


class ContextTests(unittest.TestCase):
    def test_no_future_information(self):
        event={'timestamp_ms':1000,'venue':'bybit','category':'spot','symbol':'BTCUSDT'}
        points=[dict(event,available_at_ms=900,metric='close',value='100',evidence_ref='fixture:1'),
                dict(event,available_at_ms=1100,metric='close',value='120',evidence_ref='fixture:2')]
        result=entry_context(event,points)
        self.assertEqual(len(result['data']),1)
        self.assertEqual(result['data'][0]['value'],'100')
        self.assertIn('historical_l2',result['unknown'])

    def test_simulation_requires_exact_approval(self):
        p=simulation_plan('run','e'*64,{'entry':'candidate'},{'dataset':'fixture'},'a'*40)
        called=[]
        with self.assertRaises(PermissionError):
            simulate(p,'bad',lambda *args:called.append(args))
        self.assertEqual(called,[])

    @unittest.skipUnless(os.getenv('TESTER_H_F_ROOT'),'pinned F checkout required')
    def test_real_f_engine_candidate_is_separate_artifact(self):
        import src
        import subprocess
        root=Path(os.environ['TESTER_H_F_ROOT']).resolve()
        revision=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
        src.__path__.append(str(root/'src'))
        from src.backtest.engine import run
        strategy=json.loads((root/'docs/strategy/example.strategy.json').read_text())
        snapshot=json.loads((root/'docs/strategy/example.snapshot.json').read_text())
        p=simulation_plan('fixture-run','e'*64,strategy,snapshot,revision)
        r=simulate(p,p['plan_digest'],run)
        self.assertEqual(r['classification'],'INFERRED')
        self.assertEqual(r['parent_artifact_digest'],'e'*64)
        self.assertEqual(r['engine_revision'],revision)
        self.assertTrue(r['result']['run_id'])


if __name__=='__main__': unittest.main()
