"""Explicit ports: no market data is invented when a workstream is absent."""

from pathlib import Path
from typing import Protocol

from .models import Query
from .owned_imports import owned_factory

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class ProviderUnavailable(Exception):
    pass


class Reader(Protocol):
    def read(self, resource: str, query: Query) -> dict: ...


class UnavailableReader:
    def read(self, resource: str, query: Query) -> dict:
        raise ProviderUnavailable(resource)


def load_factory(spec: str | None, default):
    if spec is None:
        return default
    return owned_factory(spec, REPOSITORY_ROOT)
