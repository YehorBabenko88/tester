"""Independent fixture checks, authored outside the repository test suite."""
import os
import uuid
import psycopg
from psycopg import sql
from src.storage.migrations import migrate
from src.storage.store import Store


def main():
    admin = os.environ['TESTER_TEST_ADMIN_DSN']
    database = 'tester_b_independent_' + uuid.uuid4().hex[:12]
    created = False
    try:
        with psycopg.connect(admin, autocommit=True) as c:
            c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
            created = True
        dsn = psycopg.conninfo.make_conninfo(admin, dbname=database)
        with psycopg.connect(dsn) as c:
            migrate(c)
        store = Store(dsn)
        start, end = 1700000000000, 1700000060000
        def fixtures(run):
            record = dict(schema_version='1.0',venue='bybit',category='linear',symbol='INDEPENDENTUSDT',dataset='trades',interval='',timestamp_ms=start+123,
                payload=dict(trade_id='original-independent-1',price='123.000000000000000001',size='0.01',side='Buy'),
                source=dict(url='https://example.test/independent',fetched_at='2026-11-07T12:00:00Z',source_kind='rest',ingestion_run=run))
            evidence = dict(venue='bybit',category='linear',symbol='INDEPENDENTUSDT',dataset='trades',interval='',start_ms=start,end_ms=end,status='EXHAUSTED',ingestion_run=run,
                pages=[dict(params={'start':start,'end':end-1},cursor=None,next_cursor=None,count=1)],
                available_range=dict(start_ms=start,end_ms=end,method='operator_attestation',source_url='https://example.test/range',sha256='a'*64))
            return record,evidence
        def page(**kwargs):
            return store.read_page('trades','bybit','linear','INDEPENDENTUSDT',start,end,**kwargs)
        record,evidence = fixtures('independent-run-a')
        result = store.ingest([record],evidence,'independent-job-a',run_id='independent-run-a')
        positive = page()
        assert positive['outcome']=='OK' and positive['coverage']['complete'], positive
        assert positive['coverage']['proof_refs'] and len(positive['rows'])==1
        print('PASS independent strict matched positive proof')
        record2,evidence2 = fixtures('independent-run-b')
        store.ingest([record2],evidence2,'independent-job-b',run_id='independent-run-b')
        capped = page(max_observations=1)
        assert capped['outcome']=='RESOURCE_EXHAUSTED' and capped['rows']==[] and capped['coverage']['proof_refs']==[], capped
        print('PASS independent observation fanout fails closed')
        finding = store.mark_range(evidence,'corrupt','independent later incident',code='independent_damage')
        with psycopg.connect(dsn) as c:
            c.execute('INSERT INTO tester_storage.finding_resolutions(finding_id,evidence_id) VALUES(%s,%s)',(finding,result['evidence_id']))
            c.execute('INSERT INTO tester_storage.coverage_resolutions(coverage_id,evidence_id) VALUES(%s,%s)',(finding,result['evidence_id']))
        damaged = page()
        assert damaged['outcome']=='OK' and not damaged['coverage']['complete'] and damaged['coverage']['proof_refs']==[], damaged
        print('PASS independent old proof cannot resolve later damage')
        print('PASS all 3 independent actual-PostgreSQL fixture checks')
    finally:
        if created:
            with psycopg.connect(admin,autocommit=True) as c:
                c.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))


if __name__ == "__main__":
    main()
