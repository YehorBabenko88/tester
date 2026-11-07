import gzip
import io
import json
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import Mock

import requests
from src.ingest.archive import ArchiveLoader, discover, validate,iter_trade_records
from src.ingest.transport import Transport
from src.ingest.journal import Journal
from src.ingest.rest import Backfill,API,expected_times
from src.ingest.catalog import volatility

def fake_api():
    api=Mock(); api.server_time_ms.return_value=2000000000000
    return api


def response(body=b'', status=200, headers=None):
    r = requests.Response()
    r.status_code = status
    r._content = body
    r.raw = io.BytesIO(body)
    r.headers.update(headers or {})
    r.url = 'https://public.bybit.com/trading/X/X2024-01-01.csv.gz'
    return r


class PipelineTests(unittest.TestCase):
    def test_trade_contract_keeps_exchange_id_and_precision(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'trades.csv.gz'
            path.write_bytes(gzip.compress(b'timestamp,symbol,side,size,price,trdMatchID\n1704067200.123456789,X,Buy,0.00000001,42000.01,original-id\n'))
            record=next(iter_trade_records(path,'linear','X',{'url':'https://public.bybit.com/trading/X/a.csv.gz'}))
            self.assertEqual(record['schema_version'],'1.0')
            self.assertEqual(record['trade_id'],'original-id')
            self.assertEqual(record['id_origin'],'exchange')
            self.assertEqual(record['timestamp_ms'],1704067200123)
            self.assertEqual(record['timestamp_ns'],1704067200123456789)
            self.assertEqual(record['timestamp_precision'],'ns')
            self.assertEqual(record['payload']['size'],'0.00000001')

    def test_trade_id_fallback_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'trades.csv.gz'
            path.write_bytes(gzip.compress(b'timestamp,symbol,side,size,price\n1704067200.123,X,Buy,1,2\n'))
            with self.assertRaises(ValueError): list(iter_trade_records(path,'linear','X',{'url':'url'}))

    def test_trade_staging_identity_uses_exchange_id(self):
        with tempfile.TemporaryDirectory() as d:
            j=Journal(Path(d)/'j.db')
            base={'schema_version':'1.0','venue':'bybit','category':'linear','symbol':'X','dataset':'trades','interval':'','timestamp_ms':1,'payload':{},'source':{}}
            j.save('a','observed',{},[{**base,'trade_id':'one'},{**base,'trade_id':'two'}])
            j.save('a','observed',{},[{**base,'trade_id':'one','timestamp_ms':2}])
            count=j.db.execute('SELECT count(*) FROM records').fetchone()[0]
            j.close()
            self.assertEqual(count,2)

    def test_archive_nanosecond_timestamp_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'a.csv.gz'
            path.write_bytes(gzip.compress(b'timestamp,id,price\n1704067200123456789,id,2\n'))
            self.assertEqual(validate(path)['first_timestamp_ms'],1704067200123)

    def test_bybit_application_rate_limit_retries(self):
        t=Mock()
        t.get.side_effect=[response(b'{"retCode":10006,"retMsg":"Too many"}'),response(b'{"retCode":0,"result":{"list":[]}}')]
        self.assertEqual(API(t).call('/kline',{})[0],{'list':[]})
        t.defer.assert_called_once()

    def test_calendar_month_spacing(self):
        self.assertEqual(list(expected_times(1704067200000,1711929600000,'M')),[1704067200000,1706745600000,1709251200000])

    def test_headerless_mt4_keeps_first_row(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'mt4.csv.gz'
            path.write_bytes(gzip.compress(b'2020.04.01 00:00,1,2,1,2,4\n2020.04.01 00:15,2,3,1,2,5\n'))
            info=validate(path)
            self.assertEqual(info['rows'],2)
            self.assertFalse(info['header_present'])
            self.assertEqual(info['first_timestamp_ms'],1585699200000)

    def test_corrupt_remote_archive_never_published(self):
        with tempfile.TemporaryDirectory() as d:
            t=Mock(); t.get.side_effect=lambda *a,**k:response(gzip.compress(b'timestamp,price\n1,2\n')[:-3])
            j=Journal(Path(d)/'j.db'); self.addCleanup(j.close)
            path=Path(d)/'a.csv.gz'
            with self.assertRaises((EOFError,OSError,ValueError)):
                ArchiveLoader(j,t).download('https://public.bybit.com/trading/X/a.csv.gz',path)
            self.assertFalse(path.exists()); self.assertEqual(t.get.call_count,3)
            j.close()

    def test_instrument_cursor_pagination(self):
        from src.ingest.catalog import instruments
        api=fake_api()
        api.call.side_effect=[({'list':[{'symbol':'X'}],'nextPageCursor':'next'},'url1'),({'list':[{'symbol':'Y'}]},'url2')]
        self.assertEqual([r['symbol'] for r in instruments(api,'linear')],['X','Y'])

    def test_retry_network_and_rate_limit(self):
        session = Mock()
        session.request.side_effect = [requests.ConnectionError(), response(status=429, headers={'Retry-After':'2'}), response(b'{}')]
        sleep = Mock()
        t = Transport(session=session, sleep=sleep, rate=100000)
        self.assertEqual(t.get('https://public.bybit.com/').status_code, 200)
        self.assertGreaterEqual(sum(x.args[0] for x in sleep.call_args_list), 2)

    def test_permanent_error_not_retried(self):
        session = Mock()
        session.request.return_value = response(status=404)
        with self.assertRaises(requests.HTTPError):
            Transport(session=session).get('https://public.bybit.com/')
        self.assertEqual(session.request.call_count, 1)

    def test_discovery_rejects_external_links_and_extracts_dates(self):
        t = Mock()
        t.get.return_value = response(b'<a href="X2024-01-01.csv.gz">ok</a><a href="https://evil/a.csv.gz">bad</a><a href="../x.csv.gz">bad</a>')
        files = discover('https://public.bybit.com/trading/X/', t)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['date'], '2024-01-01')

    def test_archive_corruption_restart_and_provenance(self):
        body = gzip.compress(b'timestamp,symbol,price\n1,X,2\n')
        with tempfile.TemporaryDirectory() as d:
            t = Mock()
            t.get.return_value = response(body, headers={'Content-Length': str(len(body))})
            j = Journal(Path(d)/'jobs.db')
            loader = ArchiveLoader(j, t)
            path = Path(d)/'a.csv.gz'
            first = loader.download('https://public.bybit.com/trading/X/a.csv.gz', path)
            self.assertEqual(first['rows'], 1)
            self.assertEqual(len(first['sha256']), 64)
            self.assertEqual(loader.download(first['url'], path)['sha256'], first['sha256'])
            self.assertEqual(t.get.call_count, 1)
            path.write_bytes(b'broken')
            loader.download(first['url'], path)
            self.assertEqual(t.get.call_count, 2)
            path.write_bytes(body[:-4])
            with self.assertRaises((ValueError, EOFError, OSError)):
                validate(path)
            j.close()

    def test_resume_requires_verified_content_range(self):
        body = gzip.compress(b'timestamp,price\n1,2\n')
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'a.csv.gz'
            part = path.with_name(path.name+'.partial')
            part.write_bytes(body[:10])
            part.with_name(part.name+'.json').write_text(json.dumps({'url':'https://public.bybit.com/trading/X/a.csv.gz','etag':'"v1"'}))
            t = Mock()
            # A 200 to a Range probe means restart, never append.
            t.get.side_effect = [response(body), response(body, headers={'Content-Length':str(len(body))})]
            j = Journal(Path(d)/'j.db')
            ArchiveLoader(j,t).download('https://public.bybit.com/trading/X/a.csv.gz',path)
            self.assertEqual(path.read_bytes(),body)
            j.close()

    def test_rest_pages_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            api = fake_api()
            api.call.side_effect = [({'list':[['120000','1','1','1','1','1','1'],['60000','1','1','1','1','1','1']]},'url'), ({'list':[['0','1','1','1','1','1','1']]},'url')]
            j = Journal(Path(d)/'j.db')
            b = Backfill(api,j)
            result = b.run('linear','X','kline','1',0,180000,limit=2)
            self.assertEqual(result['records'],3)
            b.run('linear','X','kline','1',0,180000,limit=2)
            self.assertEqual(api.call.call_count,2)
            j.close()

    def test_empty_window_is_reprobed_not_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            api=fake_api()
            api.call.return_value=({'list':[]},'url')
            j=Journal(Path(d)/'j.db')
            out=Backfill(api,j).run('linear','X','kline','1',0,180000)
            self.assertEqual(out['status'],'unverified')
            self.assertGreaterEqual(api.call.call_count,3)
            j.close()

    def test_volatility_requires_contiguous_closed_window(self):
        rows=[[str(i*60000),'1','1','1',str(100+i),'1','1'] for i in range(61)]
        self.assertIsNotNone(volatility(rows,3600000,3600000))
        self.assertIsNone(volatility(rows[1:],3600000,3600000))
        self.assertIsNone(volatility(rows,3599999,3600000))

    def test_monthly_archive_has_explicit_range(self):
        t=Mock(); t.get.return_value=response(b'<a href="X-2022-11.csv.gz">monthly</a>')
        item=discover('https://public.bybit.com/spot/X/',t)[0]
        self.assertEqual(item['start_date'],'2022-11-01')
        self.assertEqual(item['end_date_exclusive'],'2022-12-01')

    def test_valid_range_resume(self):
        body=gzip.compress(b'timestamp,price\n1,2\n')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'a.csv.gz'; part=path.with_name(path.name+'.partial')
            part.write_bytes(body[:10])
            url='https://public.bybit.com/trading/X/a.csv.gz'
            part.with_name(part.name+'.json').write_text(json.dumps({'url':url,'etag':'"v1"'}))
            t=Mock()
            t.get.side_effect=[response(body[:1],206,{'Content-Range':f'bytes 0-0/{len(body)}','ETag':'"v1"'}),response(body[10:],206,{'Content-Range':f'bytes 10-{len(body)-1}/{len(body)}','ETag':'"v1"'})]
            j=Journal(Path(d)/'j.db')
            ArchiveLoader(j,t).download(url,path)
            self.assertEqual(path.read_bytes(),body)
            self.assertEqual(t.get.call_args.kwargs['headers']['If-Range'],'"v1"')
            j.close()

    def test_repeated_cursor_fails_instead_of_silent_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            api=fake_api(); api.call.return_value=({'list':[{'timestamp':'0','openInterest':'2'}],'nextPageCursor':'same'},'url')
            j=Journal(Path(d)/'j.db')
            with self.assertRaisesRegex(ValueError,'Repeated'):
                Backfill(api,j).run('linear','X','open_interest','5min',0,600000)
            j.close()

    def test_unknown_dataset_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as d:
            api=fake_api(); j=Journal(Path(d)/'j.db')
            with self.assertRaises(ValueError): Backfill(api,j).run('inverse','X','premium','1',0,60000)
            api.call.assert_not_called(); j.close()

    def test_cancel_has_no_network_and_is_resumable(self):
        with tempfile.TemporaryDirectory() as d:
            api=fake_api(); j=Journal(Path(d)/'j.db')
            self.assertEqual(Backfill(api,j).run('linear','X','kline','1',0,60000,cancel=lambda:True)['status'],'paused')
            api.call.assert_not_called(); j.close()

    def test_open_candle_not_cached_as_final(self):
        with tempfile.TemporaryDirectory() as d:
            api=fake_api(); api.server_time_ms.return_value=30000
            api.call.return_value=({'list':[['0','1','1','1','1','1','1']]},'url')
            j=Journal(Path(d)/'j.db'); b=Backfill(api,j)
            self.assertEqual(b.run('linear','X','kline','1',0,60000)['status'],'unverified')
            self.assertEqual(j.db.execute('SELECT count(*) FROM records').fetchone()[0],0)
            api.server_time_ms.return_value=60000
            self.assertEqual(b.run('linear','X','kline','1',0,60000)['records'],1)
            j.close()

    def test_capped_empty_retry_is_paginated(self):
        with tempfile.TemporaryDirectory() as d:
            api=fake_api(); first=[True]
            def call(path,params):
                if first.pop() if first else False: return {'list':[]},'url'
                times=[t for t in [90,80,70,60,50,40,30,20,10] if params['startTime']<=t<=params['endTime']]
                return {'list':[{'fundingRateTimestamp':str(t),'fundingRate':'1'} for t in times[:params['limit']]]},'url'
            api.call.side_effect=call
            j=Journal(Path(d)/'j.db')
            self.assertEqual(Backfill(api,j).run('linear','X','funding','',0,100,limit=2)['records'],9)
            j.close()

    def test_funding_empty_older_range_stays_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            api=fake_api()
            def call(path,params):
                rows=[{'fundingRateTimestamp':str(t),'fundingRate':'1'} for t in [90,80] if params['startTime']<=t<=params['endTime']]
                return {'list':rows},'url'
            api.call.side_effect=call
            j=Journal(Path(d)/'j.db'); self.addCleanup(j.close)
            result=Backfill(api,j).run('linear','X','funding','',0,100,limit=2)
            self.assertEqual(result['status'],'unverified')
            j.close()

    def test_persisted_funding_rows_do_not_prove_empty_ranges(self):
        with tempfile.TemporaryDirectory() as d:
            api=fake_api(); api.call.return_value=({'list':[]},'url')
            j=Journal(Path(d)/'j.db')
            records=[{'schema_version':1,'venue':'bybit','category':'linear','symbol':'X','dataset':'funding','interval':'','timestamp_ms':t,'payload':{'fundingRateTimestamp':str(t),'fundingRate':'1'},'source':{}} for t in [20,80]]
            j.save('paused','paused',{},records)
            result=Backfill(api,j).run('linear','X','funding','',0,100)
            j.close()
            self.assertEqual(result['status'],'unverified')

    def test_gap_repairs_advance_across_restarts(self):
        with tempfile.TemporaryDirectory() as d:
            api=fake_api()
            def call(path,params):
                a,b=params['start'],params['end']+1
                rows=[[str(a),'1','1','1','1','1','1']] if b-a==60000 else []
                return {'list':rows},'url'
            api.call.side_effect=call
            j=Journal(Path(d)/'j.db'); b=Backfill(api,j)
            self.assertEqual(b.run('linear','X','kline','1',0,120*60000)['records'],100)
            self.assertEqual(b.run('linear','X','kline','1',0,120*60000)['records'],120)
            j.close()


if __name__=='__main__': unittest.main()
