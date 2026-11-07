"""tester-owned B -> E trade reader; unsupported resources stay unavailable.

This first binding deliberately does not certify COMPLETE: B's active interval
report and historical evidence list need a separately bounded proof resolver.
No ingestion, migrations, analytics or trading run on reads or factory startup.
"""
from .storage_api import IntegrationUnavailable,provenance_from_storage,read_trades


class StorageTradeReader:
    def __init__(self,store,*,unavailable_error=IntegrationUnavailable):
        self.store=store
        self.unavailable_error=unavailable_error

    def read(self,resource,query):
        if resource!='trades' or not query.symbol:
            raise self.unavailable_error('Storage trade resource/instrument is not connected')
        # Query-local state: concurrent requests cannot share source observations.
        originals={}
        backend=self.store

        class PageStore:
            def read(self,*args,**kwargs):
                rows=backend.read(*args,**kwargs)
                for row in rows:
                    originals[row['record_key']]=row.get('provenance')
                return rows

            def coverage(self,*args):
                report=dict(backend.coverage(*args))
                # An interval percentage alone cannot authenticate evidence refs.
                report.pop('evidence_refs',None)
                return report

        try:
            return read_trades(PageStore(),venue=query.venue,category=query.category,symbol=query.symbol,
                start_ms=query.start_ms,end_ms=query.end_ms,limit=query.limit,after=query.cursor,
                provenance_resolver=lambda key:provenance_from_storage(originals.get(key),require_declared_run=True))
        except (IntegrationUnavailable,ValueError,KeyError,TypeError) as error:
            raise self.unavailable_error('Original storage metadata or cursor cannot be served') from error


def create_reader():
    """Explicit opt-in E provider factory using only tester configuration/modules.

    Requires an application DSN and separately migrated B storage. Never installs
    a service or creates schema/data on behalf of a read request.
    """
    from src.api.config import load_settings
    from src.api.providers import ProviderUnavailable
    from src.storage.store import Store
    settings=load_settings()
    if not settings.dsn:
        raise ProviderUnavailable('Dedicated tester database configuration is required')
    return StorageTradeReader(Store(settings.dsn),unavailable_error=ProviderUnavailable)
