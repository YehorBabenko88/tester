"""Explicit ports: no market data is invented when a workstream is absent."""

from pathlib import Path
from typing import Protocol

from .models import Query
from .owned_imports import owned_factory

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class ProviderUnavailable(Exception):
    pass


class ProviderDataUnavailable(ProviderUnavailable):
    """Connected provider refused data; only fixed safe reasons may reach HTTP."""

    MESSAGES = {
        "source_provenance_unavailable": "Original acquisition provenance could not be verified",
        "storage_contract_unavailable": "Stored records do not satisfy canonical metadata requirements",
        "storage_integrity_unavailable": "Storage integrity checks block requested history",
        "storage_range_unavailable": "Requested history is marked unavailable in storage",
        "continuation_unavailable": "Stored continuation cannot be verified for the requested history",
    }

    def __init__(self, reason_code):
        if reason_code not in self.MESSAGES:
            raise ValueError("Unsupported data availability reason")
        self.reason_code = reason_code
        super().__init__(self.MESSAGES[reason_code])


class Reader(Protocol):
    def read(self, resource: str, query: Query) -> dict: ...


class UnavailableReader:
    def read(self, resource: str, query: Query) -> dict:
        raise ProviderUnavailable(resource)


def load_factory(spec: str | None, default):
    if spec is None:
        return default
    return owned_factory(spec, REPOSITORY_ROOT)
