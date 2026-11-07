import gzip
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import engine

A=1700000100000  # 5min boundary

class ObservationIntegrity(unittest.TestCase):
    def run_records(self,kind,items):
        client=MagicMock()
        client.api.return_value={'list':items}
        with patch.object(engine.db,'coverage_state',return_value=None), \
             patch.object(engine.db,'coverage_set') as coverage, \
             patch.object(engine.db,'save_observations',side_effect=lambda *a:len(a[-1])) as save, \
             patch.object(engine,'utc_ms',return_value=A+10*engine.DAY):
            engine.download_observations(client,'linear','BTCUSDT',kind,'5min',A,A+engine.DAY)
            return coverage.call_args.args[6],save

    def test_sparse_regular_history_is_retryable(self):
        for kind,field in [('oi','openInterest'),('ratio','buyRatio')]:
            with self.subTest(kind=kind):
                state,_=self.run_records(kind,[{'timestamp':str(A),field:'0.5'}])
                self.assertEqual(state,'gaps')

    def test_invalid_observation_values_never_saved(self):
        for kind,field,value in [('oi','openInterest','Infinity'),('oi','openInterest','-1'),('ratio','buyRatio','1.01'),('ratio','buyRatio','NaN')]:
            with self.subTest(value=value):
                state,save=self.run_records(kind,[{'timestamp':str(A),field:value}])
                self.assertEqual(state,'error')
                save.assert_not_called()

    def test_funding_schedule_is_unknown(self):
        state,_=self.run_records('funding',[{'fundingRateTimestamp':str(A),'fundingRate':'0.01'}])
        self.assertEqual(state,'unknown')

    def test_full_regular_history_complete(self):
        items=[{'timestamp':str(t),'buyRatio':'0.5'} for t in range(A,A+engine.DAY,300000)]
        self.assertEqual(self.run_records('ratio',items)[0],'complete')

    def test_off_grid_timestamp_rejected(self):
        state,save=self.run_records('oi',[{'timestamp':str(A+1),'openInterest':'1'}])
        self.assertEqual(state,'error')
        save.assert_not_called()

    def test_legacy_complete_observation_is_rechecked(self):
        client=MagicMock();client.api.return_value={'list':[]}
        with patch.object(engine.db,'coverage_state',return_value='complete'), \
             patch.object(engine.db,'coverage_set'),patch.object(engine.db,'save_observations',return_value=0):
            engine.download_observations(client,'linear','BTCUSDT','oi','5min',A,A+engine.DAY)
        client.api.assert_called()

class ArchiveIntegrity(unittest.TestCase):
    def test_database_failure_is_explicit_without_unlocked_coverage_write(self):
        with patch.object(engine.db,'connect',side_effect=OSError('database offline')), \
             patch.object(engine.db,'coverage_set') as coverage:
            with self.assertRaisesRegex(RuntimeError,'database publication failed'):
                engine.download_archives(MagicMock(),'linear','BTCUSDT',[date(2023,11,14)])
        coverage.assert_not_called()

    def test_error_coverage_write_failure_is_explicit(self):
        conn=MagicMock();conn.__enter__.return_value=conn
        conn.execute.side_effect=[MagicMock(),OSError('query failed'),OSError('coverage failed')]
        with patch.object(engine.db,'connect',return_value=conn), \
             patch.object(engine.db,'coverage_set') as coverage:
            with self.assertRaisesRegex(RuntimeError,'coverage failed'):
                engine.download_archives(MagicMock(),'linear','BTCUSDT',[date(2023,11,14)])
        coverage.assert_not_called()

    def test_error_coverage_is_written_before_lock_release(self):
        conn,coverage=self.run_archive(self.body(price='Infinity'))
        self.assertTrue(any('INSERT INTO coverage' in call.args[0] and "'error'" in call.args[0]
                            for call in conn.execute.call_args_list),
                        'Import errors must publish coverage in the still-locked outer transaction')
        self.assertTrue(conn.transaction.called,'Import needs an inner savepoint')
        coverage.assert_not_called()

    def test_corrupt_cache_is_downloaded_again(self):
        connection=MagicMock();connection.__enter__.return_value=connection
        connection.cursor.return_value.__enter__.return_value=connection
        expected=self.body()
        def execute(sql,args=None):
            result=MagicMock()
            result.fetchone.return_value=(False,) if 'IS DISTINCT FROM' in sql else (1,)
            if 'JOIN coverage' in sql:result.fetchone.return_value=('not-the-corrupted-file-hash',)
            return result
        connection.execute.side_effect=execute
        client=MagicMock();client.s.get.return_value.__enter__.return_value.iter_content.return_value=[expected]
        client.s.get.return_value.__enter__.return_value.headers={}
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, \
             patch.dict(engine.os.environ,{'BYBIT_DATA_DIR':directory}), \
             patch.object(engine.db,'connect',return_value=connection),patch.object(engine.db,'coverage_set'):
            dest=Path(directory)/'linear'/'BTCUSDT'/'2023-11-14.csv.gz'
            dest.parent.mkdir(parents=True);dest.write_bytes(b'corrupted')
            engine.download_archives(client,'linear','BTCUSDT',[date(2023,11,14)])
            client.s.get.assert_called_once()
            self.assertEqual(dest.read_bytes(),expected)

    def run_archive(self,body,conflict=False,rename_failure=False):
        connection=MagicMock()
        connection.__enter__.return_value=connection
        connection.cursor.return_value.__enter__.return_value=connection
        # New code asks for conflicting payload existence, then staged/stored counts.
        def execute(sql,args=None):
            result=MagicMock()
            result.fetchone.return_value=(conflict,) if 'IS DISTINCT FROM' in sql else (1,)
            if 'INSERT INTO coverage' in sql and "'error'" in sql:
                self.assertEqual(connection.__exit__.call_count,0,'Lock released before error coverage')
            return result
        connection.execute.side_effect=execute
        client=MagicMock()
        response=client.s.get.return_value.__enter__.return_value
        response.headers={}
        response.iter_content.return_value=[body]
        client.archive_url.return_value='https://public.bybit.com/example.csv.gz'
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory, \
             patch.dict(engine.os.environ,{'BYBIT_DATA_DIR':directory}), \
             patch.object(engine.db,'coverage_state',return_value=None), \
             patch.object(engine.db,'coverage_set') as coverage, \
             patch.object(engine.db,'connect',return_value=connection):
            if rename_failure:
                with patch.object(Path,'replace',side_effect=OSError('disk failure')):
                    engine.download_archives(client,'linear','BTCUSDT',[date(2023,11,14)])
            else:
                engine.download_archives(client,'linear','BTCUSDT',[date(2023,11,14)])
        return connection,coverage

    def body(self,price='2',rows=1):
        return gzip.compress(('timestamp,side,size,price,trdMatchID\n'+''.join(f'1700000000,Buy,1,{price},id{i}\n' for i in range(rows))).encode())

    def test_bad_crc_rolls_back_every_batch(self):
        body=bytearray(self.body(rows=1501));body[-8]^=255
        conn,coverage=self.run_archive(bytes(body))
        self.assertEqual(conn.commit.call_count,0)
        self.assertTrue(any(call.args[0] is not None for call in conn.transaction.return_value.__exit__.call_args_list))
        self.assertTrue(any("'error'" in call.args[0] for call in conn.execute.call_args_list))
        coverage.assert_not_called()

    def test_changed_id_payload_fails_closed(self):
        conn,coverage=self.run_archive(self.body(),conflict=True)
        errors=[call for call in conn.execute.call_args_list if "'error'" in call.args[0]]
        self.assertEqual(len(errors),1)
        self.assertIn('payload',errors[0].args[1][-1].lower())

    def test_nonfinite_trade_is_rejected(self):
        conn,coverage=self.run_archive(self.body(price='Infinity'))
        self.assertTrue(any("'error'" in call.args[0] for call in conn.execute.call_args_list))

    def test_publish_uses_lock_and_one_transaction(self):
        conn,coverage=self.run_archive(self.body())
        statements=[str(c.args[0]) for c in conn.execute.call_args_list]
        self.assertTrue(any('pg_advisory_xact_lock' in q for q in statements))
        self.assertTrue(any('INSERT INTO trade_files' in q for q in statements))
        self.assertTrue(any('INSERT INTO coverage' in q for q in statements))
        self.assertEqual(conn.commit.call_count,0)
        coverage.assert_not_called()

    def test_disk_failure_is_error(self):
        conn,coverage=self.run_archive(self.body(),rename_failure=True)
        self.assertTrue(any("'error'" in call.args[0] for call in conn.execute.call_args_list))

    def test_aggregation_requires_manifest_and_complete_coverage(self):
        conn=MagicMock();conn.__enter__.return_value=conn
        with patch.object(engine.db,'connect',return_value=conn):
            engine.build_clusters('linear','BTCUSDT',['1'])
        sql=conn.execute.call_args_list[1].args[0]
        self.assertIn('trade_files',sql)
        self.assertIn("state='complete'",sql)

if __name__=='__main__': unittest.main()

