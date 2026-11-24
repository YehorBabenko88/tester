import copy
import tempfile
import unittest
from pathlib import Path
from test_dsl import config
from test_engine import snapshot
from src.backtest import run
from src.backtest.research import grid_search, optimize, walk_forward, split_snapshot, compare
from src.backtest.artifacts import save_artifact, load_artifact


class ResearchTests(unittest.TestCase):
    def test_grid_reproducible_bounded_and_original_unchanged(self):
        c=config(); original=copy.deepcopy(c); s=snapshot()
        grid={'risk.stop_loss':['0.01','0.05'],'risk.take_profit':['0.1','0.2']}
        a=grid_search(c,s,grid,seed=7)
        self.assertEqual(a,grid_search(c,s,dict(reversed(list(grid.items()))),seed=7))
        self.assertEqual(len(a['candidates']),4)
        self.assertEqual(c,original)
        with self.assertRaises(ValueError): grid_search(c,s,{'__class__':['x']})
        with self.assertRaises(ValueError): grid_search(c,s,{'risk.stop_loss':['0.1']*300})

    def test_chronological_out_of_sample_and_walk_forward(self):
        c=config(); s=snapshot([('100','101','99','100')]*18)
        a=optimize(c,s,{'risk.stop_loss':['0.01','0.05']},train_bars=6,validation_bars=6)
        self.assertEqual(a['segments'],{'train':[0,360000],'validation':[360000,720000],'test':[720000,1080000]})
        self.assertEqual(a['out_of_sample']['processed_bars'],6)
        wf=walk_forward(c,s,{'risk.stop_loss':['0.05']},train_bars=6,validation_bars=3,test_bars=3)
        self.assertEqual(len(wf['folds']),3)
        windows=[f['segments']['test'] for f in wf['folds']]
        self.assertEqual(windows,[[540000,720000],[720000,900000],[900000,1080000]])
        changed=copy.deepcopy(s)
        for b in changed['candles'][12:]:b['high']='120'
        b=optimize(c,changed,{'risk.stop_loss':['0.01','0.05']},train_bars=6,validation_bars=6)
        self.assertEqual(a['winner_strategy_hash'],b['winner_strategy_hash'])

    def test_split_no_leaked_funding_or_future_bars(self):
        s=snapshot(); part=split_snapshot(s,0,2)
        self.assertEqual(part['end_ms'],120000)
        self.assertEqual(len(part['candles']),2)
        with self.assertRaises(ValueError): split_snapshot(s,2,2)

    def test_artifact_integrity_and_comparison(self):
        c=config(); s=snapshot(); r=run(c,s)
        with tempfile.TemporaryDirectory() as directory:
            path=save_artifact(r,c,s,directory)
            loaded=load_artifact(path)
            self.assertEqual(loaded['result'],r)
            self.assertEqual(path,save_artifact(r,c,s,directory))
            text=Path(path).read_text(); Path(path).write_text(text.replace('fixture:hand-calculated','changed'))
            with self.assertRaises(ValueError): load_artifact(path)
        self.assertEqual(compare([r,r])[0]['run_id'],r['run_id'])


if __name__=='__main__':unittest.main()
