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

from psycopg.conninfo import conninfo_to_dict

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

    def public_policy(self):
        """Existing durable policy only; every reservation rechecks its own state."""
        from .transport_policy import PostgresTransportPolicy

        return PostgresTransportPolicy(self.dsn, self.installation_id)


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
        return marker, config


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
    marker, config = _trusted_release_context(target, allow_test=allow_test)
    db_identity, service_identity = _bind_identity(marker, config)
    policy = PostgresTransportPolicy(config["database_url"], marker["installation_id"])
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
        dsn=config["database_url"],
        db_identity=db_identity,
        service_identity=service_identity,
        policy_state_at_check=state,
    )
