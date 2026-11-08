import hashlib
import importlib
import json
import uuid
from types import SimpleNamespace

import pytest


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / "lab"
    sha, tree = "a" * 40, "b" * 40
    source = root / "releases" / sha / "src/api/lab_identity.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"published identity module")
    (root / "runtime").mkdir()
    (root / "state").mkdir()
    ident, instance = str(uuid.uuid4()), uuid.uuid4().hex
    services = dict(
        app="TesterLabApp_" + uuid.UUID(ident).hex, postgres="TesterLabPG_" + uuid.UUID(ident).hex
    )
    files = {"src/api/lab_identity.py": hashlib.sha256(source.read_bytes()).hexdigest()}
    marker = dict(
        schema="tester-lab/v1",
        root=str(root),
        installation_id=ident,
        repository="YehorBabenko88/tester",
        active_sha=sha,
        ports=dict(api=18766, postgres=55470),
        services=services,
        releases={sha: files},
    )
    payload = dict(
        schema_version="tester-lab-payload/v1", source_sha=sha, tree_sha=tree, files=files
    )
    report = dict(
        source_sha=sha, installation_id=ident, service=services["app"], instance_id=instance
    )
    for path, value in [
        (root / ".tester-lab.json", marker),
        (root / "state/deploy-payload.json", payload),
        (root / "runtime/service.json", report),
    ]:
        path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setenv("TESTER_SERVICE_INSTANCE", instance)
    settings = SimpleNamespace(
        data_dir=root / "runtime",
        host="127.0.0.1",
        port=18766,
        dsn="host=127.0.0.1 port=55470 dbname=tester_lab user=app password=PRIVATE",
        provider_factory="src.integration.lab_market:create_reader",
        handler_factory="src.integration.lab_market:create_handler",
    )
    return settings, source, root, marker, payload, report


def api():
    return importlib.import_module("src.api.lab_identity")


def test_installed_identity_binds_release_and_runtime_without_secrets(installed):
    settings, source, root, marker, payload, report = installed
    result = api().read_lab_identity(settings, _source_file=source)
    data = result["data"]
    assert data["source_sha"] == marker["active_sha"]
    assert data["tree_sha"] == payload["tree_sha"]
    assert (
        data["deploy_manifest_sha256"]
        == hashlib.sha256((root / "state/deploy-payload.json").read_bytes()).hexdigest()
    )
    assert data["instance_id"] == report["instance_id"]
    assert data["installation_id"] == marker["installation_id"]
    assert data["services"] == marker["services"]
    assert data["api_endpoint"] == "http://127.0.0.1:18766"
    assert data["postgres_port"] == 55470
    assert data["provenance"] == "host_reported"
    assert data["scm_state"] == "not_independently_verified"
    encoded = json.dumps(result)
    assert "PRIVATE" not in encoded and str(root) not in encoded


@pytest.mark.parametrize(
    "mutation",
    [
        "source",
        "instance",
        "installation",
        "manifest",
        "inventory",
        "tree",
        "module",
        "optin",
        "port",
        "dsn",
        "missing",
        "oversize",
    ],
)
def test_identity_fails_closed_on_unbound_or_invalid_metadata(installed, monkeypatch, mutation):
    settings, source, root, marker, payload, report = installed
    if mutation == "source":
        source = root / "other/src/api/lab_identity.py"
    elif mutation == "instance":
        monkeypatch.setenv("TESTER_SERVICE_INSTANCE", uuid.uuid4().hex)
    elif mutation == "installation":
        report["installation_id"] = str(uuid.uuid4())
    elif mutation == "manifest":
        payload["source_sha"] = "c" * 40
    elif mutation == "inventory":
        payload["files"] = {}
    elif mutation == "tree":
        payload["tree_sha"] = "invalid"
    elif mutation == "module":
        source.write_text("changed")
    elif mutation == "optin":
        settings.provider_factory = None
    elif mutation == "port":
        settings.dsn = "host=127.0.0.1 port=5432 dbname=user_db"
    elif mutation == "dsn":
        settings.dsn = "not a connection string PRIVATE"
    for path, value in [
        (root / "runtime/service.json", report),
        (root / "state/deploy-payload.json", payload),
    ]:
        path.write_text(json.dumps(value), encoding="utf-8")
    if mutation == "missing":
        (root / "runtime/service.json").unlink()
    elif mutation == "oversize":
        (root / "runtime/service.json").write_bytes(b" " * 70000)
    with pytest.raises(api().LabIdentityUnavailable) as exc:
        api().read_lab_identity(settings, _source_file=source)
    assert str(exc.value) == "Installed lab identity cannot be verified"


def test_identity_rejects_reparse_metadata(installed):
    settings, source, root, *_ = installed
    target = root / "runtime/service.json"
    elsewhere = root.parent / "elsewhere.json"
    target.rename(elsewhere)
    try:
        target.symlink_to(elsewhere)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(api().LabIdentityUnavailable):
        api().read_lab_identity(settings, _source_file=source)


def test_identity_is_operational_and_does_not_change_approved_market_openapi():
    from fastapi.testclient import TestClient

    from src.api.config import Settings
    from src.api.main import create_app

    app = create_app(Settings())
    assert "/api/lab/identity" not in app.openapi()["paths"]
    response = TestClient(app, base_url="http://127.0.0.1").get("/api/lab/identity")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "lab_identity_unavailable"
