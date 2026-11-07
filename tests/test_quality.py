import unittest
from src.quality.candles import inspect

class CandleTests(unittest.TestCase):
    def test_closed(self):
        rows=[[60000,"1","3","1","2","10"],[120000,"2","4","2","3","20"]]
        self.assertTrue(inspect(rows,"1",300000)["complete"])
    def test_gap(self):
        rows=[[60000,"1","3","1","2","10"],[180000,"2","4","2","3","20"]]
        self.assertEqual(inspect(rows,"1",300000)["gaps"][0]["count"],1)
    def test_unclosed(self):
        self.assertFalse(inspect([[60000,"1","3","1","2","10"]],"1",120000)["complete"])

if __name__=="__main__":unittest.main()
