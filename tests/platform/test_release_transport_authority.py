"""D/E owner-bound public Transport scope tests (no network or host mutation)."""

import types
import uuid
from pathlib import Path

import pytest

from src.api import release_transport_authority as auth
from src.api.release_security import ReleaseSecurityError


def _fixture(tmp_path):
    ident = str(uuid.uuid4())
    root = tmp_path / ("TesterReleaseTest_" + uuid.UUID(ident).hex)
    source_sha = "a" * 40
    marker = dict(
        schema="tester-release-install/v1",
        mode="test",
        root=str(root),
        installation_id=ident,
        source_sha=source_sha,
        services={
            "app": "TesterReleaseApp_" + uuid.UUID(ident).hex,
            "postgres": "TesterReleasePG_" + uuid.UUID(ident).hex,
        },
        ports={"api": 18766, "postgres": 55432},
    )
    config = dict(
        database_url=(
            "host=127.0.0.1 port=55432 dbname=tester user=tester_app "
            "password=fixture require_auth=scram-sha-256"
        ),
        host="127.0.0.1",
        port=18766,
        data_dir=str(root / "runtime"),
        frontend_dir=str(root / "releases" / source_sha / "frontend"),
        public_transport_policy_enabled=True,
        public_transport_installation_id=ident,
    )
    return marker, config


def test_release_authority_rejects_caller_selected_production_root_before_io(tmp_path):
    with pytest.raises(ReleaseSecurityError, match="Caller-supplied"):
        auth.release_transport_authority(root=tmp_path)


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("public_transport_installation_id", str(uuid.UUID(int=2))),
        ("public_transport_policy_enabled", False),
        ("database_url", (
            "host=127.0.0.1 port=5432 dbname=tester user=tester_app "
            "password=fixture require_auth=scram-sha-256"
        )),
        ("database_url", (
            "host=127.0.0.1 port=55433 dbname=tester user=tester_app "
            "password=fixture require_auth=scram-sha-256"
        )),
        ("database_url", (
            "host=127.0.0.1 port=55432 dbname=postgres user=tester_app "
            "password=fixture require_auth=scram-sha-256"
        )),
        ("database_url", (
            "host=127.0.0.1 port=55432 dbname=tester user=postgres "
            "password=fixture require_auth=scram-sha-256"
        )),
    ],
)
def test_rejected_scope_does_not_connect_or_open_http(tmp_path, monkeypatch, field, replacement):
    marker, config = _fixture(tmp_path)
    config[field] = replacement
    monkeypatch.setattr(auth, "_trusted_release_context", lambda *a, **kw: (marker, config))
    calls = []

    class Policy:
        def __init__(self, *args):
            calls.append("init")

        def read(self, endpoint):
            calls.append("read")
            return "CLEAR"

    monkeypatch.setattr("src.api.transport_policy.PostgresTransportPolicy", Policy)
    with pytest.raises((ReleaseSecurityError, ValueError)):
        auth.release_transport_authority(root=marker["root"], allow_test=True)
    assert calls == []  # Before any policy I/O; never any HTTP by this module.


def test_owner_verified_scope_uses_existing_policy_read_once(tmp_path, monkeypatch):
    marker, config = _fixture(tmp_path)
    monkeypatch.setattr(auth, "_trusted_release_context", lambda *a, **kw: (marker, config))
    calls = []

    class Policy:
        def __init__(self, dsn, ident):
            calls.append(("construct", ident, dsn))

        def read(self, endpoint):
            calls.append(("read", endpoint))
            return "PENDING"  # Still bound; network remains stopped by policy.

    monkeypatch.setattr("src.api.transport_policy.PostgresTransportPolicy", Policy)
    authority = auth.release_transport_authority(root=marker["root"], allow_test=True)
    assert authority.installation_id == marker["installation_id"]
    assert authority.source_sha == marker["source_sha"]
    assert authority.db_identity == ("127.0.0.1", 55432, "tester", "tester_app")
    assert authority.service_identity[:2] == (
        marker["services"]["app"], marker["services"]["postgres"]
    )
    assert authority.policy_state_at_check == "PENDING"
    assert [call[0] for call in calls] == ["construct", "read"]
    assert calls[-1][1] == "api.bybit.com/v5/market/kline"
    assert "fixture" not in repr(authority)  # No accidental DSN credential logging.


def test_owner_policy_row_missing_fails_closed(tmp_path, monkeypatch):
    marker, config = _fixture(tmp_path)
    monkeypatch.setattr(auth, "_trusted_release_context", lambda *a, **kw: (marker, config))

    class Policy:
        def __init__(self, *args):
            pass

        def read(self, endpoint):
            from src.api.transport_policy import PublicPolicyUnavailable

            raise PublicPolicyUnavailable("policy enrollment denied")

    monkeypatch.setattr("src.api.transport_policy.PostgresTransportPolicy", Policy)
    with pytest.raises(Exception, match="policy enrollment denied"):
        auth.release_transport_authority(root=marker["root"], allow_test=True)


def test_bounded_store_binding_requires_exact_owner_dsn(tmp_path):
    """Source-backed readback cannot silently swap in another PostgreSQL role."""
    from src.storage.store import Store

    marker, config = _fixture(tmp_path)
    authority = auth.ReleaseTransportAuthority(
        installation_id=marker["installation_id"],
        source_sha=marker["source_sha"],
        root=marker["root"],
        dsn=config["database_url"],
        db_identity=("127.0.0.1", 55432, "tester", "tester_app"),
        service_identity=("a", "p", "app_sid", "pg_sid"),
        policy_state_at_check="CLEAR",
    )
    correct = Store(config["database_url"])
    original, identity = authority.checked_readback_binding(correct)
    assert original is correct
    assert identity == {
        "verified": True,
        "installation_id": marker["installation_id"],
        "source_sha": marker["source_sha"],
    }
    wrong = Store(config["database_url"].replace("port=55432", "port=55433"))
    with pytest.raises(ReleaseSecurityError, match="owner-bound"):
        authority.checked_readback_binding(wrong)
    with pytest.raises(ReleaseSecurityError, match="Canonical storage"):
        authority.checked_readback_binding(object())
