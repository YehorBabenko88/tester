"""Opt-in acceptance against an exclusively disposable loopback PostgreSQL cluster.

Requires BYBIT_TEST_POSTGRES=1, PGHOST=127.0.0.1, PGPORT=55449,
PGDATABASE=bybit_master_acceptance, PGUSER=acceptance_admin.
Never connects during ordinary test discovery. All market inputs are synthetic.
"""
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
import gzip
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid


ENABLED = os.getenv('BYBIT_TEST_POSTGRES') == '1'


@unittest.skipUnless(ENABLED, 'Set BYBIT_TEST_POSTGRES=1 for disposable PostgreSQL acceptance')
class PostgresAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        expected = dict(PGHOST='127.0.0.1', PGPORT='55449',
                        PGDATABASE='bybit_master_acceptance', PGUSER='acceptance_admin')
        if any(os.getenv(k) != v for k, v in expected.items()):
            raise RuntimeError('Refusing PostgreSQL test outside explicit disposable identity')
        import db
        import engine
        cls.db, cls.engine = db, engine
        if db.DBNAME != expected['PGDATABASE']:
            raise RuntimeError('Database module imported with a different identity')
        cls.prefix = 'FIX' + uuid.uuid4().hex[:10].upper()
        cls.archive_root = Path(__file__).resolve().parent.parent / 'pg-archive-fixtures' / cls.prefix
        db.initialize()
        db.initialize()

    def test_initialize_numeric_exactness_and_ten_synthetic_instruments(self):
        db = self.db
        price = '9007199254740993.001'
        volume = '0.00000000000000000001'
        for i in range(10):
            symbol = self.prefix + str(i) + 'USDT'
            row = ['1700000040000', price, price, price, price, volume, '1']
            self.assertEqual(db.save_candles('linear', symbol, 'trade', '1', [row], 1800000000000), 1)
            db.save_candles('linear', symbol, 'trade', '1', [row], 1800000000000)
            with db.connect() as c:
                result = c.execute('SELECT count(*),min(open),min(volume) FROM candles WHERE category=%s AND symbol=%s', ('linear', symbol)).fetchone()
            self.assertEqual(result, (1, Decimal(price), Decimal(volume)))
        with db.connect() as c:
            count = c.execute('SELECT count(DISTINCT symbol) FROM candles WHERE symbol LIKE %s', (self.prefix + '%',)).fetchone()[0]
        self.assertEqual(count, 10)

    def payload(self, bad=False):
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['timestamp', 'side', 'size', 'price', 'trdMatchID'])
        for i in range(1501):
            writer.writerow(['1700006400.123456789', 'Unknown' if bad and i == 1500 else ('Buy' if i % 2 == 0 else 'Sell'), '0.01', '100.25', 'fixture-' + str(i)])
        return gzip.compress(stream.getvalue().encode())

    def client(self, payload):
        class Response:
            headers = {'Content-Length': str(len(payload))}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def raise_for_status(self): pass
            def iter_content(self, size): yield payload
        class Session:
            calls = 0
            def get(self, *args, **kwargs):
                self.calls += 1
                return Response()
        class Client:
            s = Session()
            def archive_url(self, *args): return 'https://fixtures.invalid/synthetic.csv.gz'
        return Client()

    def test_archive_late_failure_rollback_retry_idempotence_and_clusters(self):
        db, engine = self.db, self.engine
        symbol = self.prefix + 'ARCHUSDT'
        day = date(2023, 11, 15)
        start = int(datetime(2023, 11, 15, tzinfo=timezone.utc).timestamp() * 1000)
        with patch.dict(os.environ, BYBIT_DATA_DIR=str(self.archive_root)):
            engine.download_archives(self.client(self.payload(bad=True)), 'linear', symbol, [day])
            with db.connect() as c:
                self.assertEqual(c.execute('SELECT count(*) FROM trades WHERE symbol=%s', (symbol,)).fetchone()[0], 0)
                self.assertEqual(c.execute('SELECT count(*) FROM trade_files WHERE symbol=%s', (symbol,)).fetchone()[0], 0)
            self.assertEqual(db.coverage_state('linear', symbol, 'archive', 'daily', start, start + engine.DAY), 'error')
            client = self.client(self.payload())
            engine.download_archives(client, 'linear', symbol, [day])
            self.assertEqual(db.coverage_state('linear', symbol, 'archive', 'daily', start, start + engine.DAY), 'complete')
            engine.download_archives(client, 'linear', symbol, [day])
            self.assertEqual(client.s.calls, 1, 'Verified cached retry must not redownload')
            with db.connect() as c:
                self.assertEqual(c.execute('SELECT count(*) FROM trades WHERE symbol=%s', (symbol,)).fetchone()[0], 1501)
                self.assertEqual(c.execute('SELECT row_count FROM trade_files WHERE symbol=%s', (symbol,)).fetchone()[0], 1501)
            engine.build_clusters('linear', symbol, ['1', 'W', 'M'])
            with db.connect() as c:
                rows = c.execute('SELECT timeframe,price,buy_volume,sell_volume,trade_count FROM clusters WHERE symbol=%s ORDER BY timeframe', (symbol,)).fetchall()
            self.assertEqual(len(rows), 3)
            for tf, price, buys, sells, count in rows:
                self.assertTrue(all(v.is_finite() for v in (price, buys, sells)))
                self.assertEqual((price, buys, sells, count), (Decimal('100.25'), Decimal('7.51'), Decimal('7.50'), 1501))


if __name__ == '__main__':
    unittest.main(verbosity=2)

