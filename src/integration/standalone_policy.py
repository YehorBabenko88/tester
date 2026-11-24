"""Repository ownership check for tester's own configured providers.

This performs no imports, network I/O, shell/SQL execution or external discovery.
E/G must enforce it before import, keep the owned repo on their import path, and
check loaded origin before invoking a callable. It is not a Python sandbox.
"""
from dataclasses import dataclass
from pathlib import Path
import re


class StandaloneViolation(ValueError):pass


@dataclass(frozen=True)
class FactoryTarget:
    module:str
    symbol:str
    source_path:Path


def validate_factory_spec(spec,repository_root=None):
    root=Path(repository_root).resolve() if repository_root is not None else Path(__file__).resolve().parents[2]
    if not isinstance(spec,str) or len(spec)>300:
        raise StandaloneViolation('Invalid local provider specification')
    match=re.fullmatch(r'(src\.(?:ingest|storage|quality|analytics|api|strategy|backtest|control|integration)(?:\.[A-Za-z][A-Za-z0-9_]*)*):([A-Za-z][A-Za-z0-9_]*)',spec)
    if not match:raise StandaloneViolation('Provider must belong to a tester-owned component')
    module,symbol=match.groups();base=root.joinpath(*module.split('.'))
    candidates=[p for p in [base.with_suffix('.py'),base/'__init__.py'] if p.is_file()]
    if len(candidates)!=1:raise StandaloneViolation('Owned provider source is missing or ambiguous')
    source=candidates[0].resolve()
    if not source.is_relative_to(root):raise StandaloneViolation('Provider source escapes tester repository')
    return FactoryTarget(module,symbol,source)


def assert_loaded_origin(target,module):
    origin=getattr(module,'__file__',None)
    if not origin or Path(origin).resolve()!=target.source_path:
        raise StandaloneViolation('Imported provider does not match the validated owned source')
    return module
