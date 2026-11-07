import unittest
from src.trader_intelligence.core import analyze,normalize

def row(**changes):
    x=dict(source="authorized_export",trader_id="public-alias",event_id="e1",venue="bybit",category="linear",
           symbol="BTCUSDT",kind="DISCLOSED_EXECUTION",side="BUY",timestamp_ms=1000,
           observed_at_ms=2000,price="100.00",quantity="2.0",validity="DISCLOSED",
           source_ref="sha256:abc",revision="v1")
    x.update(changes);return x

class TraderTests(unittest.TestCase):
    def test_disclosed_overlay(self):
        a=analyze([row()],start_ms=0,end_ms=3000)
        self.assertEqual(a["disclosed_executions"],1)
        self.assertEqual(a["overlays"][0]["classification"],"DISCLOSED")
        self.assertEqual(a["performance_status"],"UNAVAILABLE")
    def test_no_deanonymization(self):
        with self.assertRaises(ValueError): normalize(row(kind="ANONYMOUS_MARKET_PRINT",validity="OBSERVED"))
    def test_inferred_distinct(self):
        a=analyze([row(kind="INFERRED_EVENT",validity="INFERRED")],start_ms=0,end_ms=3000)
        self.assertEqual(a["inferred_events"],1)
    def test_no_fake_precision(self):
        with self.assertRaises(ValueError): normalize(row(price="NaN"))
    def test_dedup(self):
        a=analyze([row(),row()],start_ms=0,end_ms=3000)
        self.assertEqual(a["events"],1)
    def test_conflict(self):
        with self.assertRaises(ValueError): analyze([row(),row(price="101")],start_ms=0,end_ms=3000)
    def test_as_of_prevents_lookahead(self):
        a=analyze([row()],start_ms=0,end_ms=3000,as_of_ms=1500)
        self.assertEqual(a["events"],0)
    def test_order_independent_digest(self):
        a=row();b=row(event_id="e2",timestamp_ms=1100)
        self.assertEqual(analyze([a,b],start_ms=0,end_ms=3000)["digest"],analyze([b,a],start_ms=0,end_ms=3000)["digest"])
    def test_anonymous_not_overlay(self):
        a=analyze([row(trader_id=None,kind="ANONYMOUS_MARKET_PRINT",validity="OBSERVED")],start_ms=0,end_ms=3000)
        self.assertEqual(a["overlays"],[])
    def test_future_publication(self):
        with self.assertRaises(ValueError):normalize(row(observed_at_ms=999))
if __name__=="__main__": unittest.main()
