"""Explicit ports: no market data is invented when a workstream is absent."""

import importlib
from typing import Protocol

from .models import Query


class ProviderUnavailable(Exception):
    pass


class Reader(Protocol):
    def read(self, resource: str, query: Query) -> dict: ...


class UnavailableReader:
    def read(self, resource: str, query: Query) -> dict:
        raise ProviderUnavailable(resource)


def load_factory(spec: str | None, default):
    if not spec:
        return default
    module, name = spec.split(":", 1)
    return getattr(importlib.import_module(module), name)()
