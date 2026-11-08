"""Mock-only lifecycle guard tests: no live cluster, source, or subprocess."""

import hashlib
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from psycopg.conninfo import conninfo_to_dict

from src.integration import market_runtime as runtime


@pytest.mark.parametrize(
    "change",
    [
        {"host": "localhost"},
        {"port": "5432"},
        {"dbname": "tester_lab"},
        {"hostaddr": "192.0.2.1"},
        {"service": "ambient"},
        {"options": "-c role=other"},
    ],
)
def test_dsn_rejects_other_endpoint_or_ambient_options(change):
    from psycopg.conninfo import make_conninfo

    values = dict(
        host="127.0.0.1",
        port="55471",
        user="research_owner",
        password="private",
        dbname="tester_research_" + "a" * 32,
    )
    values.update(change)
    with pytest.raises(ValueError):
        runtime.canonical_dsn(make_conninfo(**values), {"database": "tester_research_" + "a" * 32})


def test_canonical_dsn_explicitly_pins_hostaddr():
    value = runtime.canonical_dsn(
        "host=127.0.0.1 port=55471 user=research_owner password=private dbname=tester_research_abc",
        {"database": "tester_research_abc"},
    )
    assert conninfo_to_dict(value)["hostaddr"] == "127.0.0.1"


def test_config_is_only_explicit_owned_catalog_composition(tmp_path):
    config = runtime.config_for(tmp_path / "source", tmp_path / "root", "private-dsn")
    assert config["database_url"] == config["market_catalog_database_url"] == "private-dsn"
    assert config["market_catalog_enabled"] is True
    assert config["port"] == 18767
    assert config["provider_factory"] == "src.integration.catalog_market:create_reader"
    assert config["handler_factory"] == "src.integration.catalog_market:create_handler"


def test_cleanup_never_kills_a_stalled_worker(tmp_path):
    worker = Mock()
    worker.wait.side_effect = subprocess.TimeoutExpired("owned-worker", 45)
    with pytest.raises(ValueError, match="cooperatively"):
        runtime.stop_worker(worker, tmp_path)
    assert (tmp_path / "stop.request").is_file()
    worker.terminate.assert_not_called()
    worker.kill.assert_not_called()
    worker.wait.assert_called_once_with(timeout=45)


def test_trust_export_contains_only_server_auth_windows_certificates(tmp_path, monkeypatch):
    enum = Mock(
        return_value=[
            (b"a", "x509_asn", True),
            (b"b", "x509_asn", {"1.3.6.1.5.5.7.3.1"}),
            (b"c", "x509_asn", {"unrelated"}),
            (b"d", "pkcs_7_asn", True),
        ]
    )
    monkeypatch.setattr(runtime.ssl, "enum_certificates", enum, raising=False)
    monkeypatch.setattr(
        runtime.ssl, "DER_cert_to_PEM_cert", lambda cert: "PUBLIC-" + cert.decode() + "\n"
    )
    runtime.export_trust(tmp_path)
    assert [call.args[0] for call in enum.call_args_list] == ["ROOT", "CA"]
    bundle = (tmp_path / "state/windows-ca.pem").read_bytes()
    assert bundle == b"PUBLIC-a\nPUBLIC-b\n"
    manifest = json.loads((tmp_path / "state/tls.json").read_text())
    assert manifest["sha256"] == hashlib.sha256(bundle).hexdigest()
    with pytest.raises(FileExistsError):
        runtime.export_trust(tmp_path)


def test_seed_refuses_existing_runtime_before_migrations(tmp_path, monkeypatch):
    (tmp_path / "runtime").mkdir()
    monkeypatch.setattr(runtime, "context", lambda *a: (tmp_path, tmp_path, {}, "private"))
    migration = Mock()
    monkeypatch.setattr("src.api.db.migrate", migration)
    with pytest.raises(ValueError, match="adoption refused"):
        runtime.seed(tmp_path, "a" * 40, "b" * 40)
    migration.assert_not_called()


def owner_fixture(tmp_path, monkeypatch, module="src.integration.market_runtime"):
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    owner = dict(
        schema="tester-market-process/1",
        root=str(root),
        source_root=str(source),
        pid=123,
        created=42,
        image=str(tmp_path / "python.exe"),
        application_source_sha="b" * 40,
        lab_source_sha="a" * 40,
    )
    (root / ".market-api.json").write_text(json.dumps(owner))
    args = [
        "python",
        "-m",
        module,
        "serve",
        "--root",
        str(root),
        "--lab-source-sha",
        "a" * 40,
        "--application-source-sha",
        "b" * 40,
    ]
    process = SimpleNamespace(
        pid=123,
        create_time=lambda: 42,
        exe=lambda: owner["image"],
        cwd=lambda: str(source),
        cmdline=lambda: args,
    )
    monkeypatch.setattr("psutil.Process", lambda pid: process)
    return root, source, args


def test_market_guard_refuses_research_module_or_wrong_arguments(tmp_path, monkeypatch):
    root, source, args = owner_fixture(tmp_path, monkeypatch, "src.integration.research_runtime")
    with pytest.raises(runtime.ApiOwnershipError, match="arguments_mismatch"):
        runtime.assert_api_process(root, "b" * 40, source, "a" * 40)
    args[2] = runtime.MODULE
    args[-1] = "c" * 40
    with pytest.raises(runtime.ApiOwnershipError, match="arguments_mismatch"):
        runtime.assert_api_process(root, "b" * 40, source, "a" * 40)


def test_market_guard_requires_os_accepted_tuple_and_rechecks_birth(tmp_path, monkeypatch):
    root, source, _ = owner_fixture(tmp_path, monkeypatch)
    probe = Mock()
    probe.__enter__ = Mock(return_value=probe)
    probe.__exit__ = Mock(return_value=False)
    probe.getsockname.return_value = ("127.0.0.1", 52000)
    monkeypatch.setattr(runtime.socket, "create_connection", Mock(return_value=probe))
    birth = Mock()
    monkeypatch.setattr(runtime, "_assert_api_birth", birth)

    def observe(process, client, check_birth):
        assert client == ("127.0.0.1", 52000)
        check_birth()
        return {"polls": 1}

    monkeypatch.setattr(runtime, "_wait_for_owned_connection", observe)
    result = runtime.assert_api_process(root, "b" * 40, source, "a" * 40)
    assert result["pid"] == 123 and birth.call_count == 2


def test_seed_runs_only_explicit_migrations_and_never_publishes_fake_rows(tmp_path, monkeypatch):
    marker = {"database": "tester_research_" + "a" * 32}
    monkeypatch.setattr(
        runtime, "context", lambda *a: (tmp_path / "source", tmp_path, marker, "owned-dsn")
    )
    events = []
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.execute.return_value.fetchall.return_value = []
    monkeypatch.setattr(runtime.psycopg, "connect", Mock(return_value=connection))
    monkeypatch.setattr("src.api.db.migrate", lambda dsn: events.append(("api", dsn)))
    monkeypatch.setattr(
        "src.storage.migrations.migrate", lambda conn: events.append(("storage", conn))
    )
    monkeypatch.setattr(
        "src.api.catalog_store.migrate_catalog", lambda dsn: events.append(("catalog", dsn))
    )
    monkeypatch.setattr(runtime, "export_trust", lambda root: events.append(("trust", root)))
    result = runtime.seed(tmp_path, "a" * 40, "b" * 40)
    assert [e[0] for e in events] == ["api", "storage", "catalog", "trust"]
    assert result["seeded_market_records"] == 0 and result["coverage"] == "UNKNOWN"
    statements = [call.args[0] for call in connection.execute.call_args_list]
    assert any("CREATE TABLE tester_lab.projections" in statement for statement in statements)
    assert not any("INSERT" in statement for statement in statements)
    assert "owned-dsn" not in json.dumps(result)


def test_worker_guard_requires_fixed_module_and_pinned_config(tmp_path, monkeypatch):
    root, source, _ = owner_fixture(tmp_path, monkeypatch)
    owner = json.loads((root / ".market-api.json").read_text())
    (root / ".market-worker.json").write_text(json.dumps(owner))
    args = [owner["image"], "-m", "src.api.worker"]
    environment = {"TESTER_CONFIG_FILE": str(root / "market-config.json")}
    process = SimpleNamespace(
        pid=123,
        create_time=lambda: 42,
        exe=lambda: owner["image"],
        cwd=lambda: str(source),
        cmdline=lambda: args,
        environ=lambda: environment,
    )
    monkeypatch.setattr("psutil.Process", lambda pid: process)
    monkeypatch.setattr(runtime, "_assert_api_birth", Mock())
    assert runtime.assert_worker_process(root, "b" * 40, source, "a" * 40)["pid"] == 123
    args[-1] = "unrelated.worker"
    with pytest.raises(ValueError, match="module/config"):
        runtime.assert_worker_process(root, "b" * 40, source, "a" * 40)
    args[-1] = "src.api.worker"
    environment["TESTER_CONFIG_FILE"] = str(tmp_path / "unrelated.json")
    with pytest.raises(ValueError, match="module/config"):
        runtime.assert_worker_process(root, "b" * 40, source, "a" * 40)
