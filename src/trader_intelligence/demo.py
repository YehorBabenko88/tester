"""Reproducible synthetic research scenario, no exchange requests."""
import argparse
import json
import os
from pathlib import Path
from .imports import import_bytes
from .service import Research, ENGINE_VERSION
from .store import Store


def fixture():
    base=dict(source='authorized_export',trader_id='synthetic-alias',event_id='entry',
        venue='fixture',category='linear',symbol='TESTUSDT',kind='DISCLOSED_EXECUTION',
        side='BUY',timestamp_ms=1000,observed_at_ms=2000,price='100.00',quantity='2.0',
        validity='DISCLOSED',source_ref='fixture:synthetic-not-public-trader',revision='v1')
    rows=[base,dict(base,event_id='partial-close',side='SELL',timestamp_ms=3000,
                    observed_at_ms=4000,price='120.00',quantity='1.0')]
    manifest=dict(source='authorized_export',source_revision='synthetic-demo-v1',
        permission='USER_AUTHORIZED',rights_ref='fixture:synthetic-owned',coverage='COMPLETE',
        flat_start=True,position_mode='ONE_WAY',instrument_model='LINEAR',synthetic=True)
    return import_bytes(json.dumps(rows).encode(),'json',manifest)


def scenario(store,tenant='synthetic-demo'):
    store.migrate()
    d=fixture(); rev=store.put(tenant,d); service=Research(store)
    q=dict(dataset_revision=rev,event_id='entry',source='authorized_export',trader_id='synthetic-alias',
           venue='fixture',category='linear',symbol='TESTUSDT',start_ms=0,end_ms=5000,as_of_ms=5000)
    plan=service.plan(tenant,q)
    # Only this built-in synthetic scenario self-approves, never an external/model request.
    result=service.run(tenant,'synthetic-demo-'+ENGINE_VERSION,plan,approved_digest=plan['plan_digest'])
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    result=scenario(Store(os.environ['TESTER_H_DSN']))
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True),encoding='utf-8')
    print(json.dumps({'analysis_run_id':result['analysis_run_id'],'artifact_digest':result['artifact_digest'],
                      'synthetic':True,'gross_model_pnl':result['reconstruction']['gross_realized_pnl']}))


if __name__=='__main__': main()
