"""Explicit experimental gate only. Does NOT enroll legacy writers or certify time.

Connection factory must return a fresh dedicated autocommit connection using
mapping rows. Helpers own/close it; no Store defaults call these helpers.
"""
from contextlib import contextmanager
import uuid
from psycopg.pq import TransactionStatus

# Deliberately global: every prototype epoch/scope shares a single gate. This is
# conservative serialization, not a claim that existing writers participate.
GATE='tester_visibility:experimental:common_gate:v1'
MUTATOR_TABLES=frozenset('''instruments instrument_metadata raw_records record_sources
source_archives download_jobs ingestion_checkpoints pagination_evidence coverage_intervals
coverage_resolutions quality_findings finding_resolutions derived_aggregates audit_events
ingestion_runs ingestion_run_jobs ingestion_run_evidence source_observations
lifecycle_objects lifecycle_object_evidence lifecycle_references backup_verifications
backup_evidence lifecycle_scans lifecycle_quarantine lifecycle_plans'''.split())


def _identifier(value):
    if not isinstance(value,str) or not value.strip() or len(value)>200:
        raise ValueError('invalid experimental epoch/scope identifier')
    return value


@contextmanager
def _gated_transaction(connect):
    c=connect(); locked=False
    try:
        if not c.autocommit or c.info.transaction_status!=TransactionStatus.IDLE:
            raise ValueError('fresh idle autocommit connection required; no prior snapshot')
        row=c.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked',(GATE,)).fetchone()
        locked=bool(row and row['locked'])
        if not locked: raise RuntimeError('experimental visibility gate busy; retry with a fresh connection')
        # Session gate is acquired before BEGIN, so a waiting old RR snapshot cannot
        # be reused here. try-lock avoids an unbounded blocking operation.
        with c.transaction():
            c.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            yield c
    finally:
        try:
            if locked: c.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))',(GATE,))
        finally:
            c.close()  # releases session gate even when transaction/connection fails


def create_unenrolled_epoch(connect,epoch_id):
    """Register an UNTRUSTED label only; duplicate IDs fail, including after restore.

    An external label is not a trusted restore enrollment. No success field can
    be enabled by this helper or the version-1 schema.
    """
    _identifier(epoch_id)
    with _gated_transaction(connect) as c:
        c.execute("INSERT INTO tester_visibility.epochs(epoch_id,status,success_enabled) VALUES(%s,'UNENROLLED',false)",(epoch_id,))
    return dict(epoch_id=epoch_id,status='UNENROLLED',success_enabled=False)


@contextmanager
def experimental_mutation(connect,epoch_id,scopes,mutator_tables):
    """Atomic prototype writes + generations + ledger for declared bounded scopes.

    This is a cooperative scaffold, NOT enforcement. Caller declares every affected
    scope/table and must not commit/rollback/change role inside the yielded block.
    Legacy/direct writes bypass it, so epochs remain UNENROLLED unconditionally.
    """
    _identifier(epoch_id)
    if not isinstance(scopes,(list,tuple)) or not 1<=len(scopes)<=64:
        raise ValueError('bounded nonempty scopes required')
    if not isinstance(mutator_tables,(list,tuple)) or not 1<=len(mutator_tables)<=32:
        raise ValueError('bounded nonempty mutator inventory required')
    scopes=sorted(set(_identifier(s) for s in scopes))
    if any(not isinstance(t,str) or t not in MUTATOR_TABLES for t in mutator_tables):
        raise ValueError('unknown mutator table')
    tables=sorted(set(mutator_tables))
    with _gated_transaction(connect) as c:
        epoch=c.execute('SELECT status,success_enabled FROM tester_visibility.epochs WHERE epoch_id=%s FOR UPDATE',(epoch_id,)).fetchone()
        if not epoch or epoch['status']!='UNENROLLED' or epoch['success_enabled'] is not False:
            raise RuntimeError('unsupported experimental epoch state')
        for scope in scopes:
            c.execute('''INSERT INTO tester_visibility.scope_generations(epoch_id,scope_key,generation) VALUES(%s,%s,1)
                ON CONFLICT(epoch_id,scope_key) DO UPDATE SET generation=tester_visibility.scope_generations.generation+1''',(epoch_id,scope))
        yield c
        c.execute('INSERT INTO tester_visibility.mutation_events(operation_id,epoch_id,scopes,mutator_tables) VALUES(%s,%s,%s,%s)',
                  (str(uuid.uuid4()),epoch_id,scopes,tables))
