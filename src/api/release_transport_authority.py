"""Owner-authenticated, read-only release Transport authority (E-SCOPE-02).

This verifier has no HTTP client, no policy reset/reserve, no migration and no
new store. Only an owner-capable trusted server process can read the protected
maintenance ledger; the ordinary App service must not be granted access to it.
A caller-supplied DSN, UUID or source SHA is never an authority input.
"""

import json
import os
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from .lab_install import plain
from .release_security import ReleaseSecurityError, _unique_object


@dataclass(frozen=True, slots=True)
class ReleaseTransportAuthority:
    installation_id: str
    source_sha: str
    root: str
    dsn: str = field(repr=False)
    db_identity: tuple[str, int, str, str]
    service_identity: tuple[str, str, str, str]
    policy_state_at_check: str
    marker_sha256: str
    pg_system_identifier: str
    db_hostaddr: str

    def public_policy(self):
        """Existing durable policy only; every reservation rechecks its own state."""
        from .transport_policy import PostgresTransportPolicy

        return PostgresTransportPolicy(self.dsn, self.installation_id)

    def checked_readback_binding(self, store):
        """Explicit B #139 seam: no new Store/DB client or ownerless DSN.

        Call from an owner-verified server-side factory, never a request. The
        existing Store.read_bounded is the canonical PostgreSQL REPEATABLE READ
        READ ONLY snapshot with pre-transfer SQL budget and statement timeout.
        This check is not an unforgeable runtime credential and must NOT make
        an unprivileged service able to mint a verified identity independently.
        """
        from src.storage.store import Store

        if not isinstance(store, Store) or not isinstance(getattr(store, "_dsn", None), str):
            raise ReleaseSecurityError("Canonical storage binding required")
        actual = conninfo_to_dict(store._dsn)
        expected = conninfo_to_dict(self.dsn)
        # Hostaddr may be absent on an otherwise equivalent owner-generated
        # DSN, but it must never point outside the pinned loopback endpoint.
        if actual.get("hostaddr", actual.get("host")) != "127.0.0.1":
            raise ReleaseSecurityError("Readback Store hostaddr outside owned loopback")
        actual["hostaddr"] = "127.0.0.1"
        if actual != expected:
            raise ReleaseSecurityError("Readback Store must use owner-bound PostgreSQL DSN")
        return store, {
            "verified": True,
            "installation_id": self.installation_id,
            "source_sha": self.source_sha,
        }


def _trusted_release_context(root, *, allow_test):
    """Validate OS-owned trust, signed ledger, SCM registration and PG cluster.

    This is intentionally unavailable on non-Windows hosts, with any missing
    trust evidence or without owner access to protected ProgramData. It does
    not elevate the process or grant an App service access to the owner ledger.
    """
    if os.name != "nt":
        raise ReleaseSecurityError("Windows owner-bound release authority required")
    from .release_install import (
        CANONICAL_ROOT, load_marker, maintenance_parent,
    )
    from .release_service import preflight, validate_resources
    from .release_uninstall import (
        _WindowsObject, _load_ledger, _preflight, _security_record,
        _validate_admin_security,
    )

    root = plain(root)
    if not allow_test and root != CANONICAL_ROOT:
        raise ReleaseSecurityError("Canonical owner installation required")
    marker = load_marker(root)
    if marker.get("mode") != ("test" if allow_test else "production"):
        raise ReleaseSecurityError("Release installation mode mismatch")
    trust_path = plain(marker["trust_config"])
    expected = plain(
        maintenance_parent(root, marker["mode"])
        / ("TesterReleaseMaintenance_" + marker["installation_id"].replace("-", ""))
        / "trust.json"
    )
    if trust_path != expected:
        raise ReleaseSecurityError("External maintenance authority path mismatch")
    ledger_path = plain(trust_path.parent / "ownership.json")
    with ExitStack() as stack:
        for path in (trust_path.parent, trust_path, ledger_path):
            handle = _WindowsObject(path)
            stack.callback(handle.close)
            _validate_admin_security(_security_record(handle))
            if path == trust_path:
                trust = json.loads(
                    handle.read_bytes(max_bytes=64 * 1024),
                    object_pairs_hook=_unique_object,
                )
        if (
            trust.get("schema") != "tester-maintenance-trust/v1"
            or trust.get("installation_id") != marker["installation_id"]
            or trust.get("root") != str(root)
            or trust.get("source_sha") != marker["source_sha"]
            or trust.get("manifest_sha256") != marker["release_manifest_sha256"]
            or trust.get("ledger") != str(ledger_path)
        ):
            raise ReleaseSecurityError("Protected release trust scope mismatch")
        ledger = _load_ledger(
            root, ledger_path, trust["trusted_ledger_sha256"],
            trust["trusted_public_key_sha256"], marker["source_sha"],
        )
        if ledger["marker"] != marker:
            raise ReleaseSecurityError("Authenticated release marker mismatch")
        # Existing validation is authoritative; do not duplicate SCM, ACL,
        # signed inventory, physical PG cluster or dynamic writer checks.
        if preflight(root) != marker:
            raise ReleaseSecurityError("Release preflight identity mismatch")
        _preflight(root, ledger, tolerate_churn=True)
        validate_resources(marker)
        config = json.loads(
            (root / "config.json").read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
        from .release_install import MARKER

        marker_digest = ledger["entries"].get(MARKER, {}).get("sha256")
        if not isinstance(marker_digest, str) or len(marker_digest) != 64:
            raise ReleaseSecurityError("Authenticated marker digest unavailable")
        cluster = json.loads(
            (root / "state/postgres-identity.json").read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
        if (
            cluster.get("installation_id") != marker["installation_id"]
            or not isinstance(cluster.get("system_identifier"), str)
            or not cluster["system_identifier"].isdecimal()
        ):
            raise ReleaseSecurityError("Validated PostgreSQL cluster receipt unavailable")
        return marker, config, (marker_digest, cluster["system_identifier"])


def _bind_identity(marker, config):
    """Pure and bounded scope comparison, before any PostgreSQL or HTTP call."""
    from .release_database import database_identity
    from .release_service import service_sid, validate_config

    validate_config(marker, config)
    identity = database_identity(marker)
    if (
        config.get("public_transport_policy_enabled") is not True
        or config.get("public_transport_installation_id")
        != marker["installation_id"]
    ):
        raise ReleaseSecurityError("Transport installation identity mismatch")
    values = conninfo_to_dict(config["database_url"])
    if (
        values.get("host") != identity["host"]
        or values.get("hostaddr", identity["hostaddr"]) != identity["hostaddr"]
        or values.get("port") != str(identity["port"])
        or values.get("dbname") != "tester"
        or values.get("user") != "tester_app"
        or values.get("require_auth") != identity["require_auth"]
    ):
        raise ReleaseSecurityError("Transport PostgreSQL scope mismatch")
    service_names = marker["services"]
    return (
        (identity["host"], identity["port"], "tester", "tester_app"),
        (
            service_names["app"], service_names["postgres"],
            service_sid(service_names["app"]), service_sid(service_names["postgres"]),
        ),
    )


def release_transport_authority(*, root=None, allow_test=False):
    """Resolve from owner-protected state and existing PostgreSQL policy.

    Only the production canonical root is accepted by default. Explicit
    allow_test is for a separately created isolated Windows test installation;
    NEVER accept it from a request/CLI parameter. No caller DSN/UUID override.
    A policy read uses the existing SECURITY DEFINER enrollment/role verifier
    and takes no lease or HTTP action. A later Transport reservation is still
    compulsory; this snapshot does not itself authorize any network request.
    """
    from .release_install import CANONICAL_ROOT
    from .transport_policy import PostgresTransportPolicy

    if root is not None and not allow_test:
        raise ReleaseSecurityError("Caller-supplied installation root refused")
    target = CANONICAL_ROOT if root is None else Path(root)
    marker, config, proof = _trusted_release_context(target, allow_test=allow_test)
    db_identity, service_identity = _bind_identity(marker, config)
    owned_dsn = make_conninfo(config["database_url"], hostaddr="127.0.0.1")
    policy = PostgresTransportPolicy(owned_dsn, marker["installation_id"])
    # This SECURITY DEFINER read checks the enrolled installation and runtime
    # role. It is *not* read-only SQL internally (it takes a scoped row lock);
    # it has no persistent mutation and is never a second network client.
    state = policy.read("api.bybit.com/v5/market/kline")
    if state not in {"CLEAR", "PENDING", "HALTED", "CAPACITY"}:
        raise ReleaseSecurityError("Transport owner policy unavailable")
    return ReleaseTransportAuthority(
        installation_id=marker["installation_id"],
        source_sha=marker["source_sha"],
        root=marker["root"],
        dsn=owned_dsn,
        db_identity=db_identity,
        service_identity=service_identity,
        policy_state_at_check=state,
        marker_sha256=proof[0],
        pg_system_identifier=proof[1],
        db_hostaddr="127.0.0.1",
    )


_OFFLINE_PIN_CONTEXT = b"TESTER-OFFLINE-SCM-IDENTITY-v1\x00"


def _offline_pin_payload(authority):
    app, postgres, app_sid, postgres_sid = authority.service_identity
    return {
        "schema": "tester-offline-scm-pin/v1",
        "installation_id": authority.installation_id,
        "source_sha": authority.source_sha,
        "marker_sha256": authority.marker_sha256,
        "pg_system_identifier": authority.pg_system_identifier,
        "hostaddr": authority.db_hostaddr,
        "port": authority.db_identity[1],
        "dbname": authority.db_identity[2],
        "dbuser": authority.db_identity[3],
        "app_service": app,
        "app_sid": app_sid,
        "postgres_service": postgres,
        "postgres_sid": postgres_sid,
    }


def verify_offline_signed_pin(authority, envelope, *, pinned_public_key_sha256):
    """Verify independently pinned owner key and exact STATIC identity bytes.

    An owner-installed static pin identifies the installation; it cannot
    authorize HTTP, prove fresh boot identity, or override PostgreSQL revocation.
    No signing/private key generation happens in production code.
    """
    import hashlib
    import hmac
    import re

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(authority, ReleaseTransportAuthority):
        raise ReleaseSecurityError("Owner-authenticated installation authority required")
    if not isinstance(envelope, dict) or set(envelope) != {
        "payload", "public_key_hex", "signature_hex",
    }:
        raise ReleaseSecurityError("Offline identity pin envelope invalid")
    if envelope["payload"] != _offline_pin_payload(authority):
        raise ReleaseSecurityError("Offline identity pin installation mismatch")
    public = envelope["public_key_hex"]
    signature = envelope["signature_hex"]
    if not (type(public) is str and re.fullmatch("[0-9a-f]{64}", public)
            and type(signature) is str and re.fullmatch("[0-9a-f]{128}", signature)
            and type(pinned_public_key_sha256) is str
            and re.fullmatch("[0-9a-f]{64}", pinned_public_key_sha256)):
        raise ReleaseSecurityError("Offline owner signature malformed")
    raw_key = bytes.fromhex(public)
    if not hmac.compare_digest(hashlib.sha256(raw_key).hexdigest(), pinned_public_key_sha256):
        raise ReleaseSecurityError("Offline owner key fingerprint mismatch")
    canonical = json.dumps(
        envelope["payload"], sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    try:
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            bytes.fromhex(signature), _OFFLINE_PIN_CONTEXT + canonical
        )
    except (InvalidSignature, ValueError, TypeError):
        raise ReleaseSecurityError("Offline owner signature invalid") from None
    return True


def _windows_current_service_sid(expected):
    """Read actual process TokenGroups; a computed name is never proof."""
    if os.name != "nt":
        raise ReleaseSecurityError("Windows SCM token required")
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        groups = win32security.GetTokenInformation(token, win32security.TokenGroups)
        actual = {
            win32security.ConvertSidToStringSid(sid)
            for sid, attributes in groups
            if attributes & win32con.SE_GROUP_ENABLED
        }
    finally:
        token.Close()
    if expected not in actual:
        raise ReleaseSecurityError("Current process has no expected SCM service SID")
    return expected



def _offline_pg_policy_read(authority):
    """Consult existing PostgreSQL SECURITY DEFINER role/policy: NO HTTP.

    A policy READ is not a network reservation, revocable handoff or physical
    database attestation. Only CLEAR is accepted; unknown/paused/revoked
    states and database failures deny the offline positive identity sample.
    """
    from .transport_policy import PostgresTransportPolicy

    policy = PostgresTransportPolicy(authority.dsn, authority.installation_id)
    return policy.read("api.bybit.com/v5/market/kline") == "CLEAR"



def _offline_fresh_pg_identity(authority, *, cluster_probe=None):
    """Offline/disposable physical cluster + SQL principal check (NO HTTP).

    Probe must supply verified physical system identifier and SQL session
    identity on the same dedicated endpoint. A policy read or caller UUID
    is not a proof. No default probe: without owner attestation fail closed.
    """
    if not isinstance(authority, ReleaseTransportAuthority):
        raise ReleaseSecurityError("Owner-authenticated installation authority required")
    if cluster_probe is None:
        raise ReleaseSecurityError("Fresh PostgreSQL physical/principal attestation unavailable")
    evidence = cluster_probe(authority)
    if not isinstance(evidence, dict) or set(evidence) != {
        "system_identifier", "host", "port", "database", "session_user",
        "installation_id", "scope_count",
    }:
        raise ReleaseSecurityError("Unexpected PostgreSQL identity evidence")
    if (evidence["system_identifier"] != authority.pg_system_identifier
            or evidence["host"] != authority.db_hostaddr
            or type(evidence["port"]) is not int
            or evidence["port"] != authority.db_identity[1]
            or evidence["database"] != "tester"
            or evidence["session_user"] != "tester_app"
            or evidence["installation_id"] != authority.installation_id
            or type(evidence["scope_count"]) is not int
            or evidence["scope_count"] != 1):
        raise ReleaseSecurityError("PostgreSQL physical or principal isolation mismatch")
    return True


def verify_offline_service_identity(authority, *, pin=None, pinned_public_key_sha256=None,
                                    token_checker=None, pg_checker=None):
    """Round7 OFFLINE/DISPOSABLE verification; never grants runtime HTTP.

    Caller supplies owner-verified authority, never caller UUID/DSN or an
    arbitrary verified:true dictionary. A static pin alone does not authorize
    network operations across reboot or revocation. Checkers are test seams:
    never source them from HTTP, environment variables or untrusted CLI input.
    """
    from uuid import UUID

    if not isinstance(authority, ReleaseTransportAuthority):
        raise ReleaseSecurityError("Owner-authenticated installation authority required")
    if type(authority.installation_id) is not str or type(authority.source_sha) is not str:
        raise ReleaseSecurityError("Malformed owner installation identity")
    verify_offline_signed_pin(
        authority, pin, pinned_public_key_sha256=pinned_public_key_sha256,
    )
    try:
        if str(UUID(authority.installation_id)) != authority.installation_id:
            raise ValueError
    except (ValueError, AttributeError):
        raise ReleaseSecurityError("Noncanonical owner installation identity") from None
    if len(authority.source_sha) != 40 or any(c not in "0123456789abcdef" for c in authority.source_sha):
        raise ReleaseSecurityError("Invalid owner release source digest")
    if len(authority.marker_sha256) != 64 or any(c not in "0123456789abcdef" for c in authority.marker_sha256):
        raise ReleaseSecurityError("Invalid signed installation marker digest")
    if type(authority.pg_system_identifier) is not str or not authority.pg_system_identifier.isdecimal():
        raise ReleaseSecurityError("Invalid owner PostgreSQL system identifier")
    if authority.db_hostaddr != "127.0.0.1":
        raise ReleaseSecurityError("Owner database hostaddr is not loopback")
    if authority.db_identity[0] != authority.db_hostaddr or authority.db_identity[2:] != ("tester", "tester_app"):
        raise ReleaseSecurityError("Owner database role or name mismatch")
    if type(authority.db_identity[1]) is not int or not 1024 <= authority.db_identity[1] <= 65535 or authority.db_identity[1] == 5432:
        raise ReleaseSecurityError("Owner database port is forbidden")
    dsn_values = conninfo_to_dict(authority.dsn)
    if (dsn_values.get("host") != "127.0.0.1"
            or dsn_values.get("hostaddr", "127.0.0.1") != "127.0.0.1"
            or dsn_values.get("port") != str(authority.db_identity[1])
            or dsn_values.get("dbname") != "tester"
            or dsn_values.get("user") != "tester_app"
            or dsn_values.get("require_auth") != "scram-sha-256"
            or set(dsn_values) - {
                "host", "hostaddr", "port", "dbname", "user", "password",
                "sslmode", "sslrootcert", "connect_timeout", "require_auth",
            }):
        raise ReleaseSecurityError("Owner database DSN does not match signed scope")
    from .release_service import service_sid

    app, postgres, app_sid, postgres_sid = authority.service_identity
    suffix = UUID(authority.installation_id).hex
    if (app_sid != service_sid(app) or postgres_sid != service_sid(postgres)
            or app != "TesterReleaseApp_" + suffix
            or postgres != "TesterReleasePG_" + suffix):
        raise ReleaseSecurityError("Owner SCM service identity mismatch")
    if token_checker is None:
        token_checker = _windows_current_service_sid
    if token_checker(app_sid) != app_sid:
        raise ReleaseSecurityError("Current SCM service token mismatch")
    if pg_checker is None:
        raise ReleaseSecurityError("Fresh owner-bound PostgreSQL identity probe required")
    if pg_checker(authority) is not True:
        raise ReleaseSecurityError("Owner PostgreSQL identity or revocation check denied")
    return {
        "installation_id": authority.installation_id,
        "source_sha": authority.source_sha,
        "marker_sha256": authority.marker_sha256,
        "pg_system_identifier": authority.pg_system_identifier,
        "service_sid_verified": True,
        "database_scope_verified": True,
        "network_eligible": False,
        "analytics_route_eligible": False,
        "reason": "offline_identity_only_no_revocable_handoff",
    }
