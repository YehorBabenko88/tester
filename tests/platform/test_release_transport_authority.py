"""D/E owner-bound public Transport scope tests (no network or host mutation)."""

import uuid

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
    monkeypatch.setattr(auth, "_trusted_release_context", lambda *a, **kw: (marker, config, ("b" * 64, "123456789012345")))
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
    monkeypatch.setattr(auth, "_trusted_release_context", lambda *a, **kw: (marker, config, ("b" * 64, "123456789012345")))
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
    assert authority.marker_sha256 == "b" * 64
    assert authority.pg_system_identifier == "123456789012345"
    assert authority.db_hostaddr == "127.0.0.1"
    assert [call[0] for call in calls] == ["construct", "read"]
    assert calls[-1][1] == "api.bybit.com/v5/market/kline"
    assert "fixture" not in repr(authority)  # No accidental DSN credential logging.


def test_owner_policy_row_missing_fails_closed(tmp_path, monkeypatch):
    marker, config = _fixture(tmp_path)
    monkeypatch.setattr(auth, "_trusted_release_context", lambda *a, **kw: (marker, config, ("b" * 64, "123456789012345")))

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
        marker_sha256="b" * 64,
        pg_system_identifier="123456789012345",
        db_hostaddr="127.0.0.1",
    )
    correct = Store(config["database_url"])
    original, identity = authority.checked_readback_binding(correct)
    assert original is correct
    assert identity == {
        "verified": True,
        "installation_id": marker["installation_id"],
        "source_sha": marker["source_sha"],
    }
    # Only the exact pinned loopback alias may be normalized.
    explicit = Store(config["database_url"] + " hostaddr=127.0.0.1")
    assert authority.checked_readback_binding(explicit)[0] is explicit
    foreign = Store(config["database_url"] + " hostaddr=127.0.0.2")
    with pytest.raises(ReleaseSecurityError, match="hostaddr"):
        authority.checked_readback_binding(foreign)
    wrong = Store(config["database_url"].replace("port=55432", "port=55433"))
    with pytest.raises(ReleaseSecurityError, match="owner-bound"):
        authority.checked_readback_binding(wrong)
    with pytest.raises(ReleaseSecurityError, match="Canonical storage"):
        authority.checked_readback_binding(object())


def _offline_authority(tmp_path):
    marker, config = _fixture(tmp_path)
    from src.api.release_service import service_sid

    return auth.ReleaseTransportAuthority(
        installation_id=marker["installation_id"],
        source_sha=marker["source_sha"],
        root=marker["root"],
        dsn=config["database_url"],
        db_identity=("127.0.0.1", 55432, "tester", "tester_app"),
        service_identity=(
            marker["services"]["app"], marker["services"]["postgres"],
            service_sid(marker["services"]["app"]),
            service_sid(marker["services"]["postgres"]),
        ),
        policy_state_at_check="CLEAR",
        marker_sha256="b" * 64,
        pg_system_identifier="123456789012345",
        db_hostaddr="127.0.0.1",
    )



def _signed_offline_pin(scope):
    """Disposable in-memory Ed25519 key; never release or authorize network."""
    import hashlib
    import json
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    payload = auth._offline_pin_payload(scope)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return (
        {
            "payload": payload,
            "public_key_hex": pub.hex(),
            "signature_hex": key.sign(auth._OFFLINE_PIN_CONTEXT + canonical).hex(),
        },
        hashlib.sha256(pub).hexdigest(),
    )


def test_round7_offline_identity_never_authorizes_network(tmp_path):
    scope = _offline_authority(tmp_path)
    pin, fingerprint = _signed_offline_pin(scope)
    calls = []
    def token_checker(sid):
        calls.append("token")
        return sid
    def pg_checker(authority):
        calls.append("pg")
        return authority.installation_id == scope.installation_id
    result = auth.verify_offline_service_identity(
        scope, pin=pin, pinned_public_key_sha256=fingerprint,
        token_checker=token_checker, pg_checker=pg_checker,
    )
    assert calls == ["token", "pg"]
    assert result["network_eligible"] is False
    assert result["analytics_route_eligible"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_sha", "X" * 40),
        ("marker_sha256", "X" * 64),
        ("pg_system_identifier", "wrong"),
        ("db_hostaddr", "127.0.0.2"),
        ("installation_id", str(uuid.UUID(int=3))),
        ("dsn", "host=127.0.0.1 port=55433 dbname=tester user=tester_app require_auth=scram-sha-256"),
        ("dsn", "host=127.0.0.1 hostaddr=127.0.0.2 port=55432 dbname=tester user=tester_app require_auth=scram-sha-256"),
        ("db_identity", ("127.0.0.1", 5432, "tester", "tester_app")),
        ("db_identity", ("127.0.0.1", 55432, "postgres", "tester_app")),
        ("db_identity", ("127.0.0.1", 55432, "tester", "postgres")),
    ],
)
def test_round7_offline_scope_forgery_rejected_before_pg(tmp_path, field, value):
    from dataclasses import replace

    base = _offline_authority(tmp_path)
    pin, fingerprint = _signed_offline_pin(base)
    forged = replace(base, **{field: value})
    calls = []
    with pytest.raises(ReleaseSecurityError):
        auth.verify_offline_service_identity(
            forged, pin=pin, pinned_public_key_sha256=fingerprint,
            token_checker=lambda sid: calls.append("token") or sid,
            pg_checker=lambda scope: calls.append("pg") or True,
        )
    assert calls == []


def test_round7_cross_install_sid_denied_without_pg(tmp_path):
    from dataclasses import replace
    original = _offline_authority(tmp_path)
    app, pg, app_sid, pg_sid = original.service_identity
    other = replace(original, service_identity=(app, pg, "S-1-5-80-0", pg_sid))
    pin, fingerprint = _signed_offline_pin(other)
    with pytest.raises(ReleaseSecurityError, match="SCM"):
        auth.verify_offline_service_identity(
            other, pin=pin, pinned_public_key_sha256=fingerprint,
            token_checker=lambda sid: sid, pg_checker=lambda _: True,
        )


@pytest.mark.parametrize("fault", ["missing_pg", "revoked", "stolen_token", "restart"])
def test_round7_offline_revocation_restart_and_db_unavailable_denied(tmp_path, fault):
    scope = _offline_authority(tmp_path)
    pin, fingerprint = _signed_offline_pin(scope)
    def token_checker(sid):
        return "S-1-5-80-0" if fault == "stolen_token" else sid
    def pg_checker(authority):
        if fault == "missing_pg":
            raise ConnectionError("disposable database offline")
        return fault != "revoked"
    with pytest.raises((ReleaseSecurityError, ConnectionError)):
        auth.verify_offline_service_identity(
            scope, pin=pin, pinned_public_key_sha256=fingerprint,
            token_checker=token_checker,
            pg_checker=None if fault == "restart" else pg_checker,
        )


def test_round7_reject_self_signed_verified_dict_and_non_windows(tmp_path):
    scope = _offline_authority(tmp_path)
    with pytest.raises(ReleaseSecurityError, match="Owner-authenticated"):
        auth.verify_offline_service_identity(
            {"verified": True, "installation_id": scope.installation_id},
            token_checker=lambda sid: sid, pg_checker=lambda obj: True,
        )
    if auth.os.name != "nt":
        with pytest.raises(ReleaseSecurityError, match="Windows SCM token"):
            auth._windows_current_service_sid(scope.service_identity[2])

def test_round7_offline_clone_of_signed_identity_cannot_use_other_service_name(tmp_path):
    from dataclasses import replace

    original = _offline_authority(tmp_path)
    app, pg, app_sid, pg_sid = original.service_identity
    foreign_id = str(uuid.UUID(int=4))
    # Merely copying the signed marker/DSN but changing the UUID must never
    # borrow the old Windows service SID or any other installation scope.
    cloned = replace(original, installation_id=foreign_id)
    pin, fingerprint = _signed_offline_pin(cloned)
    with pytest.raises(ReleaseSecurityError, match="SCM"):
        auth.verify_offline_service_identity(
            cloned, pin=pin, pinned_public_key_sha256=fingerprint,
            token_checker=lambda sid: sid, pg_checker=lambda _: True,
        )
    assert app_sid != pg_sid


def test_round7_offline_signed_pin_rejects_wrong_key_and_forged_signature(tmp_path):
    scope = _offline_authority(tmp_path)
    pin, fingerprint = _signed_offline_pin(scope)
    assert auth.verify_offline_signed_pin(
        scope, pin, pinned_public_key_sha256=fingerprint,
    ) is True
    with pytest.raises(ReleaseSecurityError, match="fingerprint"):
        auth.verify_offline_signed_pin(
            scope, pin, pinned_public_key_sha256="0" * 64,
        )
    forged = dict(pin, signature_hex="0" * 128)
    with pytest.raises(ReleaseSecurityError, match="signature invalid"):
        auth.verify_offline_signed_pin(
            scope, forged, pinned_public_key_sha256=fingerprint,
        )
    with pytest.raises(ReleaseSecurityError, match="envelope"):
        auth.verify_offline_signed_pin(
            scope, {"verified": True}, pinned_public_key_sha256=fingerprint,
        )


def test_round7_offline_signed_pin_alone_is_not_a_network_permission(tmp_path):
    scope = _offline_authority(tmp_path)
    pin, fingerprint = _signed_offline_pin(scope)
    with pytest.raises(ReleaseSecurityError, match="Fresh owner-bound"):
        auth.verify_offline_service_identity(
            scope, pin=pin, pinned_public_key_sha256=fingerprint,
            token_checker=lambda sid: sid,
        )


@pytest.mark.parametrize("state,eligible", [
    ("CLEAR", True), ("PENDING", False), ("HALTED", False), ("CAPACITY", False),
])
def test_round7_offline_pg_authorization_uses_existing_policy_and_denies_revocation(
    tmp_path, monkeypatch, state, eligible
):
    scope = _offline_authority(tmp_path)
    calls = []
    class Policy:
        def __init__(self, dsn, installation_id):
            calls.append(("construct", dsn, str(installation_id)))
        def read(self, endpoint):
            calls.append(("read", endpoint))
            return state
    monkeypatch.setattr("src.api.transport_policy.PostgresTransportPolicy", Policy)
    assert auth._offline_pg_policy_read(scope) is eligible
    assert calls == [
        ("construct", scope.dsn, scope.installation_id),
        ("read", "api.bybit.com/v5/market/kline"),
    ]


def test_round7_offline_pg_unreachable_denies_before_network(tmp_path, monkeypatch):
    scope = _offline_authority(tmp_path)
    pin, fingerprint = _signed_offline_pin(scope)
    class Unavailable:
        def __init__(self, *a):
            pass
        def read(self, endpoint):
            from src.api.transport_policy import PublicPolicyUnavailable
            raise PublicPolicyUnavailable("disposable PG offline")
    monkeypatch.setattr("src.api.transport_policy.PostgresTransportPolicy", Unavailable)
    from src.api.transport_policy import PublicPolicyUnavailable
    with pytest.raises(PublicPolicyUnavailable, match="disposable PG offline"):
        auth.verify_offline_service_identity(
            scope, pin=pin, pinned_public_key_sha256=fingerprint,
            token_checker=lambda sid: sid,
            pg_checker=auth._offline_pg_policy_read,
        )


def _round8_pg_evidence(scope):
    return {
        "system_identifier": scope.pg_system_identifier,
        "host": scope.db_hostaddr,
        "port": scope.db_identity[1],
        "database": scope.db_identity[2],
        "session_user": scope.db_identity[3],
        "installation_id": scope.installation_id,
        "scope_count": 1,
    }


def test_round8_offline_pg_attestation_denies_when_probe_unavailable(tmp_path):
    scope = _offline_authority(tmp_path)
    with pytest.raises(ReleaseSecurityError, match="attestation unavailable"):
        auth._offline_fresh_pg_identity(scope)
    assert auth._offline_fresh_pg_identity(
        scope, cluster_probe=_round8_pg_evidence,
    ) is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("system_identifier", "999999999999999"),
        ("host", "127.0.0.2"),
        ("port", 5432),
        ("port", 55433),
        ("database", "postgres"),
        ("session_user", "postgres"),
        ("installation_id", str(uuid.UUID(int=42))),
        ("scope_count", 0),
        ("scope_count", 2),
        ("scope_count", True),
    ],
)
def test_round8_offline_pg_rejects_foreign_cluster_role_and_ambiguous_scope(
    tmp_path, field, value,
):
    scope = _offline_authority(tmp_path)
    evidence = _round8_pg_evidence(scope)
    evidence[field] = value
    with pytest.raises(ReleaseSecurityError, match="physical or principal"):
        auth._offline_fresh_pg_identity(scope, cluster_probe=lambda _: evidence)


def test_round8_offline_pg_rejects_forged_or_incomplete_evidence(tmp_path):
    scope = _offline_authority(tmp_path)
    with pytest.raises(ReleaseSecurityError, match="Unexpected"):
        auth._offline_fresh_pg_identity(
            scope, cluster_probe=lambda _: {"verified": True},
        )
    evidence = _round8_pg_evidence(scope)
    del evidence["scope_count"]
    with pytest.raises(ReleaseSecurityError, match="Unexpected"):
        auth._offline_fresh_pg_identity(scope, cluster_probe=lambda _: evidence)


def test_round8_owner_static_pin_remains_network_disabled_even_with_pg_attestation(
    tmp_path,
):
    scope = _offline_authority(tmp_path)
    pin, fingerprint = _signed_offline_pin(scope)
    assert auth._offline_fresh_pg_identity(
        scope, cluster_probe=_round8_pg_evidence,
    )
    output = auth.verify_offline_service_identity(
        scope, pin=pin, pinned_public_key_sha256=fingerprint,
        token_checker=lambda sid: sid, pg_checker=lambda _: True,
    )
    assert output["network_eligible"] is False
    assert output["analytics_route_eligible"] is False


def test_round8_signed_static_pin_cannot_be_replayed_to_another_root(tmp_path):
    from dataclasses import replace

    original = _offline_authority(tmp_path)
    pin, fingerprint = _signed_offline_pin(original)
    moved = replace(original, root=str(tmp_path / "cloned-installation"))
    calls = []
    with pytest.raises(ReleaseSecurityError, match="installation mismatch"):
        auth.verify_offline_service_identity(
            moved, pin=pin, pinned_public_key_sha256=fingerprint,
            token_checker=lambda sid: calls.append("token") or sid,
            pg_checker=lambda scope: calls.append("pg") or True,
        )
    assert calls == []  # No token or PostgreSQL access before signature failure.
