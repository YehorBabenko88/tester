import importlib
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import types

import pytest
from canonical_fixtures import envelope
from fastapi.testclient import TestClient

from src.api import providers
from src.api.config import Settings
from src.api.main import create_app


@pytest.fixture
def owned(tmp_path, monkeypatch):
    root = tmp_path / "tester"
    (root / "src/integration").mkdir(parents=True)
    (root / "src/__init__.py").write_text("")
    (root / "src/integration/__init__.py").write_text("")
    marker = tmp_path / "called"
    imported = tmp_path / "imported"
    source = root / "src/integration/owned.py"
    source.write_text(
        f"from pathlib import Path\nPath({str(imported)!r}).write_text('owned')\n"
        f"def build():\n    Path({str(marker)!r}).write_text('called')\n"
        "    return {'provider': 'tester-owned'}\n"
    )
    saved = {
        key: value
        for key, value in sys.modules.copy().items()
        if key == "src" or key.startswith("src.")
    }
    for key in saved:
        sys.modules.pop(key, None)
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setattr(providers, "REPOSITORY_ROOT", root, raising=False)
    importlib.invalidate_caches()
    yield root, marker, imported, source
    for key in list(sys.modules):
        if key == "src" or key.startswith("src."):
            sys.modules.pop(key, None)
    sys.modules.update(saved)
    importlib.invalidate_caches()


def test_owned_integration_factory_and_none_default(owned):
    root, marker, imported, source = owned
    assert providers.load_factory("src.integration.owned:build", None) == {
        "provider": "tester-owned"
    }
    assert marker.read_text() == "called"
    assert imported.read_text() == "owned"
    default = object()
    assert providers.load_factory(None, default) is default


@pytest.mark.parametrize(
    "spec",
    [
        "os:system",
        "foreign.gateway:build",
        "https://host/f.py:build",
        "../foreign/gateway.py:build",
        r"C:\foreign\gateway.py:build",
        "",
        "src.integration.absent:build",
    ],
)
def test_rejected_specs_do_not_import_or_invoke(owned, spec):
    root, marker, imported, source = owned
    with pytest.raises(ValueError):
        providers.load_factory(spec, None)
    assert not marker.exists()
    assert not imported.exists()


def test_ambiguous_source_rejected_before_import(owned):
    root, marker, imported, source = owned
    (source.parent / "owned").mkdir()
    (source.parent / "owned/__init__.py").write_text(source.read_text())
    with pytest.raises(ValueError):
        providers.load_factory("src.integration.owned:build", None)
    assert not marker.exists() and not imported.exists()


def test_foreign_import_path_shadow_rejected_before_parent_exec(owned, tmp_path, monkeypatch):
    root, marker, imported, source = owned
    foreign = tmp_path / "foreign"
    (foreign / "src/integration").mkdir(parents=True)
    (foreign / "src/__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()"
    )
    (foreign / "src/integration/__init__.py").write_text("")
    (foreign / "src/integration/owned.py").write_text(source.read_text())
    monkeypatch.syspath_prepend(str(foreign))
    with pytest.raises(ValueError):
        providers.load_factory("src.integration.owned:build", None)
    assert not marker.exists() and not imported.exists()


def test_cached_foreign_target_not_invoked(owned, tmp_path):
    root, marker, imported, source = owned
    foreign = tmp_path / "foreign.py"
    foreign.write_text("")
    module = types.ModuleType("src.integration.owned")
    module.__file__ = str(foreign)
    module.__spec__ = importlib.util.spec_from_file_location(module.__name__, foreign)
    module.build = lambda: marker.touch()
    sys.modules[module.__name__] = module
    with pytest.raises(ValueError):
        providers.load_factory("src.integration.owned:build", None)
    assert not marker.exists() and not imported.exists()


def test_loaded_origin_changed_by_module_does_not_invoke_factory(owned, tmp_path):
    root, marker, imported, source = owned
    original = source.read_text()
    source.write_text(f"__file__={str(tmp_path / 'foreign.py')!r}\n" + original)
    with pytest.raises(ValueError):
        providers.load_factory("src.integration.owned:build", None)
    assert imported.exists()  # Owned module may execute; rejected factory may not.
    assert not marker.exists()


def test_escaped_source_rejected_before_import(owned, tmp_path):
    root, marker, imported, source = owned
    foreign = tmp_path / "foreign.py"
    foreign.write_text(source.read_text())
    source.unlink()
    try:
        source.symlink_to(foreign)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    with pytest.raises(ValueError):
        providers.load_factory("src.integration.owned:build", None)
    assert not marker.exists() and not imported.exists()


def test_parent_path_rewritten_by_owned_initializer_is_rejected(owned, tmp_path):
    root, marker, imported, source = owned
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "owned.py").write_text(source.read_text())
    (root / "src/integration/__init__.py").write_text(f"__path__=[{str(foreign)!r}]\n")
    with pytest.raises(ValueError):
        providers.load_factory("src.integration.owned:build", None)
    assert not marker.exists() and not imported.exists()


def test_cached_spec_cannot_disagree_with_loaded_file(owned, tmp_path):
    root, marker, imported, source = owned
    foreign = tmp_path / "foreign.py"
    foreign.write_text("")
    module = types.ModuleType("src.integration.owned")
    module.__file__ = str(source)
    module.__spec__ = importlib.util.spec_from_file_location(module.__name__, foreign)
    module.build = lambda: marker.touch()
    sys.modules[module.__name__] = module
    with pytest.raises(ValueError):
        providers.load_factory("src.integration.owned:build", None)
    assert not marker.exists() and not imported.exists()


def test_cached_merged_parent_namespace_is_rejected(owned, tmp_path):
    root, marker, imported, source = owned
    (root / "src/__init__.py").unlink()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    module = types.ModuleType("src")
    module.__spec__ = importlib.machinery.ModuleSpec("src", None, is_package=True)
    module.__path__ = [str(root / "src"), str(foreign)]
    module.__spec__.submodule_search_locations = module.__path__
    sys.modules["src"] = module
    with pytest.raises(ValueError):
        providers.load_factory("src.integration.owned:build", None)
    assert not marker.exists() and not imported.exists()


def test_owned_root_is_kept_on_import_path(owned, monkeypatch):
    root, marker, imported, source = owned
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(root)])
    assert providers.load_factory("src.integration.owned:build", None) == {
        "provider": "tester-owned"
    }
    assert str(root) in sys.path
    assert marker.exists()


def test_owned_package_factory_is_supported(owned):
    root, marker, imported, source = owned
    package = root / "src/integration/local_package"
    package.mkdir()
    (package / "__init__.py").write_text(source.read_text())
    assert providers.load_factory("src.integration.local_package:build", None) == {
        "provider": "tester-owned"
    }
    assert marker.exists()


def test_owned_integration_factory_through_http_app(owned):
    root, marker, imported, source = owned
    value = envelope()
    value["next_cursor"] = None
    source.write_text(
        "class Reader:\n    def read(self, resource, query):\n"
        f"        return {value!r}\n"
        "def build():\n    return Reader()\n"
    )
    settings = Settings(data_dir=root / "runtime", provider_factory="src.integration.owned:build")
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as client:
        response = client.get(
            "/api/trades",
            params={"symbol": "BTCUSDT", "start_ms": 1700000000000, "end_ms": 1700000060000},
        )
        assert response.status_code == 200
        assert response.json() == value


def test_non_callable_owned_symbol_is_rejected(owned):
    root, marker, imported, source = owned
    source.write_text("build = 1\n")
    with pytest.raises(ValueError):
        providers.load_factory("src.integration.owned:build", None)
    assert not marker.exists()


def test_reexport_from_another_owned_module_is_supported(owned):
    root, marker, imported, source = owned
    helper = source.parent / "helper.py"
    helper.write_text(source.read_text())
    source.write_text("from .helper import build\n")
    assert providers.load_factory("src.integration.owned:build", None) == {
        "provider": "tester-owned"
    }
    assert marker.exists()


def test_foreign_callable_reexport_is_not_invoked(owned, tmp_path, monkeypatch):
    root, marker, imported, source = owned
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "outside.py").write_text(
        f"from pathlib import Path\ndef build():\n    Path({str(marker)!r}).touch()\n"
    )
    monkeypatch.syspath_prepend(str(foreign))
    source.write_text("from outside import build\n")
    with pytest.raises(ValueError):
        providers.load_factory("src.integration.owned:build", None)
    assert not marker.exists()
    sys.modules.pop("outside", None)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction coverage")
def test_windows_junction_escape_without_symlink_privilege(owned, tmp_path):
    root, marker, imported, source = owned
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell unavailable")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "__init__.py").write_text(source.read_text())
    environment = os.environ.copy()
    environment.update(
        TESTER_LINK_SOURCE=str(source.parent / "escaped"), TESTER_LINK_TARGET=str(foreign)
    )
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-Command",
            "$ErrorActionPreference='Stop'; [void](New-Item -ItemType Junction "
            "-Path $env:TESTER_LINK_SOURCE -Target $env:TESTER_LINK_TARGET)",
        ],
        env=environment,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, "Fixture junction creation failed"
    with pytest.raises(ValueError):
        providers.load_factory("src.integration.escaped:build", None)
    assert not marker.exists() and not imported.exists()
