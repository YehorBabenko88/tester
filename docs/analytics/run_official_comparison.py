"""Bounded opt-in official Bybit comparison. Not a synthetic test or B verification.

Run from repo root: python docs/analytics/run_official_comparison.py
Writes reproducible source snapshots and honest observational differences.
"""
import csv
import base64
import gzip
import hashlib
import io
import json
import sys
import urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from src.analytics import normalize_trade,aggregate,footprint,compare_candles
from src.analytics.exact import number,decimal

OUT=Path(__file__).with_name('evidence'); OUT.mkdir(exist_ok=True)
ARCHIVE='https://public.bybit.com/trading/XRPUSDT/XRPUSDT2023-01-01.csv.gz'
START=1672531200000; END=START+3600000
API=f'https://api.bybit.com/v5/market/kline?category=linear&symbol=XRPUSDT&interval=1&start={START}&end={END-1}&limit=1000'


def fetch(url,maximum):
    with urllib.request.urlopen(url,timeout=25) as r:
        data=r.read(maximum+1)
        if len(data)>maximum: raise ValueError('Download exceeds explicit byte limit')
        return data


def main():
    offline='--offline' in sys.argv
    previous=json.loads((OUT/'official-comparison.json').read_text(encoding='utf-8')) if offline else None
    report={'archive_url':ARCHIVE,'official_url':API,'start_ms':START,'end_ms':END,
        'evidence_kind':'live_official_sources_observational','verified_complete':False,
        'reason':'C does not independently certify B source coverage or archive taker-side semantics'}
    try:
        if offline:
            snapshot=OUT/'official-archive-selected.json'
            saved=snapshot.read_bytes() if snapshot.exists() else gzip.decompress(base64.b64decode((OUT/'official-archive-selected.json.gz.b64').read_text(encoding='ascii')))
            all_rows=json.loads(saved)
            report['archive_sha256']=previous['archive_sha256']
            report['archive_row_count']=previous['archive_row_count']
        else:
            data=fetch(ARCHIVE,4000000)
            report['archive_sha256']=hashlib.sha256(data).hexdigest()
            all_rows=list(csv.DictReader(io.StringIO(gzip.decompress(data).decode())))
            report['archive_row_count']=len(all_rows)
        selected=[r for r in all_rows if START<=number(r['timestamp'])*1000<END]
        report['selected_trade_count']=len(selected)
        (OUT/'official-archive-selected.json').write_text(json.dumps(selected,separators=(',',':')),encoding='utf-8')
        # Decimal lexical source precision is preserved per row; no fabricated ns.
        trades=[]
        for raw in selected:
            digits=len(raw['timestamp'].partition('.')[2])
            precision='s' if digits==0 else 'ms' if digits<=3 else 'us' if digits<=6 else 'ns'
            provenance=dict(source_kind='bybit_archive',source_ref=ARCHIVE,source_hash=report['archive_sha256'],
                ingestion_run='official-comparison-2023-01-01',timestamp_precision=precision,source_timestamp=raw['timestamp'])
            trades.append(normalize_trade(raw,category='linear',symbol='XRPUSDT',source_ref=provenance))
        calculated=aggregate(trades,interval='1',price_step='0.0001',as_of_ms=END)
        report['calculated_candle_count']=len(calculated)
        fp=footprint(trades,interval='1',price_step='0.0001')
        report['observed_taker_delta']=[{'start_ms':r['start_ms'],'delta':r['delta'],'availability':r['availability']} for r in fp]
        try:
            body=(OUT/'official-kline-response.json').read_bytes() if offline else fetch(API,250000)
            (OUT/'official-kline-response.json').write_bytes(body)
            response=json.loads(body)
            if response.get('retCode')!=0: raise ValueError('Bybit retCode '+str(response.get('retCode')))
            official=[]
            for row in response['result']['list']:
                ts=int(row[0])
                if not START<=ts<END: continue
                source=dict(source_kind='bybit_rest',source_ref=API,source_hash=hashlib.sha256(body).hexdigest(),
                    ingestion_run='official-comparison-2023-01-01',timestamp_precision='ms',source_timestamp=row[0])
                official.append(dict(schema_version='1.0',venue='bybit',category='linear',symbol='XRPUSDT',timestamp_ms=ts,
                    start_ms=ts,end_ms=ts+60000,interval='1',kind='official_trade',
                    **dict(zip(['open','high','low','close','volume'],row[1:6])),is_closed=True,
                    provenance=[source],availability={'status':'UNVERIFIED','reason':'Coverage not independently certified by B'},
                    coverage={'status':'UNKNOWN','start_ms':START,'end_ms':END,'evidence_refs':[API]}))
            gated=compare_candles(calculated,official,tolerance='0',rounding='none')
            report['gated_statuses']=sorted({r['status'] for r in gated})
            by_ts={r['start_ms']:r for r in official}
            diffs=[]
            for c in calculated:
                o=by_ts.get(c['start_ms'])
                if o:
                    for field in ['open','high','low','close','volume']:
                        diff=number(c[field])-number(o[field])
                        if diff: diffs.append({'start_ms':c['start_ms'],'field':field,'calculated':c[field],'official':o[field],'difference':decimal(diff)})
            report.update(official_candle_count=len(official),observed_numerical_differences=diffs,
                observed_comparison_count=sum(1 for c in calculated if c['start_ms'] in by_ts)*5)
        except Exception as exc:
            report['official_fetch_error']=type(exc).__name__+': '+str(exc)
    except Exception as exc:
        report['archive_fetch_error']=type(exc).__name__+': '+str(exc)
    (OUT/'official-comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='observed_taker_delta'},indent=2))


if __name__=='__main__': main()
