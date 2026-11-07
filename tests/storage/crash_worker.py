"""Test-only process harness. Credentials travel in environment, never stdout/argv."""
import os
import sys
from src.storage.store import Store
from tests.storage.test_models import trade
from tests.storage.test_postgres import evidence

mode=sys.argv[1]; symbol=os.environ['TESTER_CRASH_SYMBOL']; job=os.environ['TESTER_CRASH_JOB']
store=Store(os.environ['TESTER_CRASH_DSN'])
record=trade(symbol=symbol); record['payload']['trade_id']='second-confirmed-id'
ev=evidence(symbol=symbol)

def blocked_stream():
    yield record
    print('uncommitted',flush=True)
    sys.stdin.read()  # parent forcibly terminates this process with an open transaction

if mode=='before': store.ingest(blocked_stream(),ev,job,checkpoint={'offset':2})
else:
    print('started',flush=True)
    store.ingest([record],ev,job,checkpoint={'offset':2})
    print('committed',flush=True)
    if mode=='after': sys.stdin.read()
