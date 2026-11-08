import unittest
from datetime import datetime, timezone
from src.strategy.levels import LevelTracker, six_month_start


def bars(values):
    return [{'start_ms':i*60000,'end_ms':(i+1)*60000,'open':str(c),'close':str(c),
             'high':str(h),'low':str(l)} for i,(h,l,c) in enumerate(values)]


class LevelTests(unittest.TestCase):
    def test_pivot_confirmation_has_no_earlier_visibility(self):
        seq=bars([(9,7,8),(10,8,9),(9,7,8),(8,6,7)])
        t=LevelTracker(left=1,right=1,min_touches=1)
        self.assertEqual(t.push(seq[0]),[])
        self.assertEqual(t.push(seq[1]),[])
        events=t.push(seq[2]); highs=[e for e in events if e['kind']=='pivot_high']
        self.assertEqual(highs[0]['known_at_ms'],180000)
        self.assertEqual(highs[0]['pivot_ms'],60000)
        self.assertEqual(t.active()[0]['price'],'10')

    def test_breakout_retest_false_break_and_invalidation(self):
        t=LevelTracker(left=1,right=1,min_touches=2,tolerance_bps='10',break_mode='close',confirmation_bars=1)
        seq=bars([(9,7,8),(10,8,9),(9,7,8),(10,8,9),(11,9,10.5),(11,9.99,10.4),(11,9,9.5)])
        events=[]
        for b in seq: events.extend(t.push(b))
        kinds=[e['kind'] for e in events if e.get('price')=='10']
        self.assertIn('confirmed',kinds)
        self.assertIn('breakout',kinds)
        self.assertIn('retest',kinds)
        self.assertIn('false_breakout',kinds)
        self.assertFalse(any(l['price']=='10' for l in t.active()))

    def test_wick_break_and_ties(self):
        t=LevelTracker(left=1,right=1,min_touches=1,break_mode='wick')
        events=[]
        for b in bars([(9,7,8),(10,8,9),(9,7,8),(10.5,8,9)]): events.extend(t.push(b))
        self.assertTrue(any(e['kind']=='breakout' for e in events))
        t=LevelTracker(left=1,right=1,min_touches=1)
        for b in bars([(9,7,8),(10,8,9),(10,7,8)]): t.push(b)
        self.assertEqual(t.active(),[])

    def test_six_calendar_months_leap_and_expiration(self):
        dt=datetime(2024,8,31,tzinfo=timezone.utc)
        expected=int(datetime(2024,2,29,tzinfo=timezone.utc).timestamp()*1000)
        self.assertEqual(six_month_start(int(dt.timestamp()*1000)),expected)
        t=LevelTracker(left=1,right=1,min_touches=1,window_days=1)
        seq=bars([(9,7,8),(10,8,9),(9,7,8)])
        for b in seq:t.push(b)
        b=bars([(9,7,8)])[0]; b['start_ms']=2*86400000; b['end_ms']=b['start_ms']+60000
        t.push(b)
        self.assertEqual(t.active(),[])


if __name__=='__main__':unittest.main()
