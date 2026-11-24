"""Import ownership attestation for trusted local configuration, not a Python sandbox."""

import importlib
import importlib.util
import inspect
import sys
import threading
from dataclasses import dataclass
from pathlib import Path


def _trusted_policy():
    # Bootstrap from our anchored source, not by importing src.integration parents:
    # a foreign regular package can otherwise win over the owned namespace package.
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/integration/standalone_policy.py").resolve(strict=True)
    if not source.is_relative_to(root):
        raise ValueError("Ownership policy source escapes tester repository")
    name = "src.integration.standalone_policy"
    if name in sys.modules:
        module = sys.modules[name]
    else:
        spec = importlib.util.spec_from_file_location(name, source)
        if spec is None or spec.loader is None:
            raise ValueError("Ownership policy source cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # Required by the policy's dataclass metadata.
        try:
            spec.loader.exec_module(module)
        except BaseException:
            if sys.modules.get(name) is module:
                del sys.modules[name]
            raise
    try:
        origins = (module.__file__, module.__spec__.origin)
        if any(Path(origin).resolve(strict=True) != source for origin in origins):
            raise ValueError
    except (AttributeError, OSError, TypeError, ValueError):
        raise ValueError("Cached or loaded ownership policy has an invalid source origin") from None
    return module


_POLICY = _trusted_policy()
FactoryTarget = _POLICY.FactoryTarget
StandaloneViolation = _POLICY.StandaloneViolation
assert_loaded_origin = _POLICY.assert_loaded_origin
validate_factory_spec = _POLICY.validate_factory_spec

_LOCK = threading.RLock()


@dataclass(frozen=True)
class _Module:
    name: str
    source: Path | None
    package: Path | None


def _owned(path, root):
    try:
        resolved = Path(path).resolve(strict=True)
    except (OSError, ValueError, TypeError):
        raise StandaloneViolation("Owned import source is missing or invalid") from None
    if not resolved.is_relative_to(root):
        raise StandaloneViolation("Import source escapes tester repository")
    return resolved


def _plan(target, root):
    parts = target.module.split(".")
    result = []
    for i in range(1, len(parts)):
        directory = root.joinpath(*parts[:i])
        package = _owned(directory, root)
        if not package.is_dir() or directory.with_suffix(".py").exists():
            raise StandaloneViolation("Parent package is missing or ambiguous")
        initializer = directory / "__init__.py"
        source = _owned(initializer, root) if initializer.is_file() else None
        result.append(_Module(".".join(parts[:i]), source, package))
    package = target.source_path.parent if target.source_path.name == "__init__.py" else None
    result.append(_Module(target.module, target.source_path, package))
    return result


def _package_paths(paths, expected, root):
    if paths is None or {_owned(path, root) for path in paths} != {expected}:
        raise StandaloneViolation("Package path is foreign, missing or merged with another origin")


def _spec(spec, item, root):
    if spec is None:
        raise StandaloneViolation("Owned source cannot be resolved on the import path")
    if item.source is None:
        if spec.origin is not None:
            raise StandaloneViolation("Namespace package is shadowed")
    else:
        if _owned(spec.origin, root) != item.source:
            raise StandaloneViolation("Source is shadowed on the import path")
        filename = getattr(spec.loader, "get_filename", None)
        if filename is not None and _owned(filename(item.name), root) != item.source:
            raise StandaloneViolation("Loader source differs from validated origin")
    if item.package is not None:
        _package_paths(spec.submodule_search_locations, item.package, root)
    elif spec.submodule_search_locations is not None:
        raise StandaloneViolation("Module unexpectedly resolves as a package")


def _loaded(module, item, root):
    if module is None:
        raise StandaloneViolation("Owned import is blocked by a cached module")
    _spec(getattr(module, "__spec__", None), item, root)
    if item.source is not None:
        assert_loaded_origin(FactoryTarget(item.name, "", item.source), module)
        _owned(module.__file__, root)
    elif getattr(module, "__file__", None) is not None:
        raise StandaloneViolation("Namespace module has an unexpected origin")
    if item.package is not None:
        _package_paths(getattr(module, "__path__", None), item.package, root)


def _find_spec(name, path):
    # Unlike importlib.util.find_spec, this does not import parents. Consult the
    # actual finders so a foreign/shadowing origin is rejected before execution.
    for finder in list(sys.meta_path):
        find = getattr(finder, "find_spec", None)
        if find is None:
            raise StandaloneViolation("Import finder cannot attest a source origin")
        spec = find(name, path, None)
        if spec is not None:
            return spec
    return None


def _preflight(plan, root):
    path = None
    for item in plan:
        if item.name in sys.modules:
            _loaded(sys.modules[item.name], item, root)
        spec = _find_spec(item.name, path)
        _spec(spec, item, root)
        path = spec.submodule_search_locations


def owned_factory(spec, repository_root):
    root = Path(repository_root).resolve(strict=True)
    target = validate_factory_spec(spec, root)  # No configurable import before this point.
    with _LOCK:
        if root not in {Path(p or ".").resolve() for p in sys.path if isinstance(p, str)}:
            # Keep owned sources available for this provider's subsequent local imports.
            sys.path.append(str(root))
        plan = _plan(target, root)
        _preflight(plan, root)
        for item in plan:
            # An owned initializer can alter package paths. Check it before importing
            # its descendants, not just after the final configured module loads.
            _preflight(plan, root)
            module = importlib.import_module(item.name)
            _loaded(module, item, root)
        module = assert_loaded_origin(target, module)
        factory = getattr(module, target.symbol, None)
        if not callable(factory):
            raise StandaloneViolation("Configured owned symbol is not callable")
        try:
            origin = inspect.getsourcefile(factory)
        except (TypeError, OSError):
            origin = None
        declared = validate_factory_spec(
            f"{getattr(factory, '__module__', None)}:{target.symbol}", root
        )
        if _owned(origin, root) != declared.source_path:
            raise StandaloneViolation("Factory callable source does not match its owned module")
        # Owned-component re-exports are valid; a foreign callable alias is not.
        declaring_plan = _plan(declared, root)
        _preflight(declaring_plan, root)
        for item in declaring_plan:
            _loaded(sys.modules.get(item.name), item, root)
        return factory()
