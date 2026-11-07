import unittest
from datetime import datetime,timezone
from unittest.mock import patch
from bybit import closed_before,tf_minutes,safe_symbol,Bybit
from engine import candle_slices,timestamp_ns

def ms(y,m,d,h=0,minute=0):
    return int(datetime(y,m,d,h,minute,tzinfo=timezone.utc).timestamp()*1000)

class BoundaryTests(unittest.TestCase):
    def test_closed_minute(self):
        self.assertEqual(closed_before("1",ms(2026,11,7,12,32)+32000),ms(2026,11,7,12,32))
    def test_week_utc_monday(self):
        self.assertEqual(closed_before("W",ms(2026,11,7,12)),ms(2026,11,2))
    def test_month_leap(self):
        self.assertEqual(closed_before("M",ms(2024,2,29,23)),ms(2024,2,1))
    def test_safe_symbols(self):
        self.assertEqual(safe_symbol("BTCUSDT"),"BTCUSDT")
        for bad in ("../etc","btc usdt","x/../../",""):
            with self.assertRaises(ValueError):safe_symbol(bad)
    def test_small_one_minute_windows(self):
        parts=list(candle_slices("1",ms(2026,11,1),ms(2026,11,3)))
        self.assertTrue(all((b-a)//60000<1000 for a,b in parts))
        self.assertEqual(parts[0][0],ms(2026,11,1))
        self.assertEqual(parts[-1][1],ms(2026,11,3))
        self.assertTrue(all(parts[i][1]==parts[i+1][0] for i in range(len(parts)-1)))
    def test_trade_era_timestamps(self):
        self.assertEqual(timestamp_ns("1700000000.123"),1700000000123000000)
        self.assertEqual(timestamp_ns("1700000000123"),1700000000123000000)
    def test_market_api_path_restricted(self):
        c=Bybit()
        with self.assertRaises(ValueError):
            c.api("/v5/order/create")

if __name__=="__main__":unittest.main()
