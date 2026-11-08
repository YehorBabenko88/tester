"""Opt-in Issue #8 gate for the original A head; failures are expected evidence.

Run: python contract_checks/check_a.py --json ../contract-a-result.json
No prospective adapter API is invented: absent capabilities fail explicitly.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

PINNED_SHA='43d0126d28c95f8f5f6362e866bd6e6d50a5e7bb'
SOURCE_PATH='src/ingest/archive.py'
REPO=Path(__file__).resolve().parents[1]
SOURCE=subprocess.check_output(['git','show',f'{PINNED_SHA}:{SOURCE_PATH}'],cwd=REPO)
adapter=types.ModuleType('pinned_a_archive')
exec(compile(SOURCE,f'{PINNED_SHA}:{SOURCE_PATH}','exec'),adapter.__dict__)

class Response:
    def __init__(self,body):self.body=body
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def raise_for_status(self):pass
    def iter_content(self,chunk_size):yield self.body

class Session:
    def __init__(self,body):self.body=body
    def get(self,*args,**kwargs):return Response(self.body)

class AContractGate(unittest.TestCase):
    def download(self,body=None):
        if body is None:
            # Source actually provides nine decimal places; precision must not be
            # reconstructed from a millisecond timestamp or a binary float.
            body=gzip.compress(b'timestamp,side,size,price,trdMatchID\n1700000000.123456789,Buy,0.00000001,123.4500,t1\n')
        directory=tempfile.TemporaryDirectory(dir=REPO)
        self.addCleanup(directory.cleanup)
        path=Path(directory.name)/'BTCUSDT2023-11-14.csv.gz'
        url=adapter.archive_url('trading','BTCUSDT',path.name)
        return adapter.download(url,path,session=Session(body)),body,url

    def test_download_integrity_metadata_exists(self):
        value,body,url=self.download()
        self.assertEqual(value['source'],url)
        self.assertEqual(value['sha256'],hashlib.sha256(body).hexdigest())
        self.assertEqual(value['bytes'],len(body))

    def test_schema_version_required(self):
        value,_,_=self.download()
        self.assertIn('schema_version',value,'Issue #8: exported download metadata has no mandatory schema_version')

    def test_provenance_context_required(self):
        value,_,_=self.download()
        # Existing source/hash are checked separately; they do not supply run,
        # source-kind or precision context required by the approved contract.
        self.assertIn('provenance',value,
            'Issue #8: download exposes URL/hash but no provenance context (source kind, ingestion run, source timestamp precision)')

    def test_successful_html_does_not_replace_archive(self):
        directory=tempfile.TemporaryDirectory(dir=REPO);self.addCleanup(directory.cleanup)
        path=Path(directory.name)/'archive.csv.gz';previous=gzip.compress(b'previous archive')
        path.write_bytes(previous)
        try:
            adapter.download('https://public.bybit.com/trading/BTCUSDT/archive.csv.gz',path,Session(b'<html>upstream failure</html>'))
        except (ValueError,OSError,EOFError):
            pass
        self.assertEqual(path.read_bytes(),previous,'HTTP 200 HTML replaced the previous archive instead of failing closed')

    def test_rest_pagination_evidence_capability(self):
        exports=sorted(name for name,value in vars(adapter).items()
            if callable(value) and getattr(value,'__module__',None)==adapter.__name__)
        self.fail('UNAVAILABLE: original A exports '+repr(exports)+
            '; no REST source adapter/pagination evidence export exists to test request boundaries, cursors, response counts, terminal condition or empty-page non-completeness')

    def test_exact_source_timestamp_ms_capability(self):
        self.fail('UNAVAILABLE: original A exports bytes downloader and URL builder only; no record adapter exists to assert mandatory integer timestamp_ms=1700000000123 from source seconds 1700000000.123456789')

    def test_genuine_source_timestamp_ns_capability(self):
        self.fail('UNAVAILABLE: original A has no record adapter/precision provenance export; cannot assert source-provided timestamp_ns=1700000000123456789 without float round-trip, or ensure a millisecond-only source does not claim nanosecond precision')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json',type=Path)
    args=parser.parse_args()
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(AContractGate)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'gate':'A','issue':8,'schema_contract':'v1','pinned_sha':PINNED_SHA,
        'source_path':SOURCE_PATH,'source_sha256':hashlib.sha256(SOURCE).hexdigest(),
        'tests_run':result.testsRun,'passed':result.wasSuccessful(),
        'failures':[{'test':test.id(),'traceback':detail} for test,detail in result.failures],
        'errors':[{'test':test.id(),'traceback':detail} for test,detail in result.errors],
        'skipped':[{'test':test.id(),'reason':reason} for test,reason in result.skipped]}
    if args.json:
        args.json.parent.mkdir(parents=True,exist_ok=True)
        args.json.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('gate','pinned_sha','tests_run','passed')}))
    return 0 if result.wasSuccessful() else 1

if __name__=='__main__':sys.exit(main())

