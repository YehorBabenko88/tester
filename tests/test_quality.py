import unittest
from src.quality.candles import inspect

class CandleTests(unittest.TestCase):
    def test_closed(self):
        rows=[[60000,"1","3","1","2","10"],[120000,"2","4","2","3","20"]]
        result=inspect(rows,"1",300000)
        self.assertTrue(result['locally_valid'])
        self.assertFalse(result['complete'], 'Local candle validity cannot prove exchange history completeness')
    def test_gap(self):
        rows=[[60000,"1","3","1","2","10"],[180000,"2","4","2","3","20"]]
        self.assertEqual(inspect(rows,"1",300000)["gaps"],
                         [dict(start=120000,end=180000,classification='unknown')])
    def test_unclosed(self):
        self.assertFalse(inspect([[60000,"1","3","1","2","10"]],"1",120000)["complete"])

if __name__=="__main__":unittest.main()
