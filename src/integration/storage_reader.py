"""tester-owned B -> E trade reader; unsupported resources stay unavailable.

This first binding deliberately does not certify COMPLETE: B's active interval
report and historical evidence list need a separately bounded proof resolver.
No ingestion, migrations, analytics or trading run on reads or factory startup.
"""
from .storage_api import IntegrationUnavailable,provenance_from_storage,read_trades


class StorageTradeReader:
    def __init__(self,store,*,unavailable_error=IntegrationUnavailable,data_error=None):
        self.store=store
        self.unavailable_error=unavailable_error
        self.data_error=data_error or unavailable_error

    def read(self,resource,query):
        if resource!='trades' or not query.symbol:
            raise self.unavailable_error('Storage trade resource/instrument is not connected')
        # Query-local state: concurrent requests cannot share source observations.
        originals={}
        backend=self.store
        integrity_blocked=False
        range_unavailable=False

        def data_failure(reason):
            if integrity_blocked:reason='storage_integrity_unavailable'
            elif range_unavailable:reason='storage_range_unavailable'
            return self.data_error(reason)

        def source_provenance(key):
            try:
                return provenance_from_storage(originals.get(key),require_declared_run=True)
            except IntegrationUnavailable as error:
                raise data_failure('source_provenance_unavailable') from error

        class PageStore:
            def read(self,*args,**kwargs):
                rows=backend.read(*args,**kwargs)
                for row in rows:
                    originals[row['record_key']]=row.get('provenance')
                return rows

            def coverage(self,*args):
                nonlocal integrity_blocked,range_unavailable
                report=dict(backend.coverage(*args))
                integrity_blocked=bool(report.get('corrupt'))
                range_unavailable=bool(report.get('unavailable'))
                # An interval percentage alone cannot authenticate evidence refs.
                report.pop('evidence_refs',None)
                return report

        try:
            return read_trades(PageStore(),venue=query.venue,category=query.category,symbol=query.symbol,
                start_ms=query.start_ms,end_ms=query.end_ms,limit=query.limit,after=query.cursor,
                provenance_resolver=source_provenance)
        except (IntegrationUnavailable,KeyError,TypeError) as error:
            raise data_failure('storage_contract_unavailable') from error
        except ValueError as error:
            raise data_failure('continuation_unavailable') from error


def create_reader():
    """Explicit opt-in E provider factory using only tester configuration/modules.

    Requires an application DSN and separately migrated B storage. Never installs
    a service or creates schema/data on behalf of a read request.
    """
    from src.api.config import load_settings
    from src.api.providers import ProviderDataUnavailable,ProviderUnavailable
    from src.storage.store import Store
    settings=load_settings()
    if not settings.dsn:
        raise ProviderUnavailable('Dedicated tester database configuration is required')
    return StorageTradeReader(Store(settings.dsn),unavailable_error=ProviderUnavailable,data_error=ProviderDataUnavailable)
