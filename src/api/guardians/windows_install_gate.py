"""Windows TESTER installer ownership gate; no service or file mutations.

This module is deliberately pure. A future Windows adapter must independently
attest service identities and enforce this plan before touching a live host.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import re
from collections.abc import Mapping
from dataclasses import dataclass

SCHEMA = "tester-install/v1"
FORBIDDEN = ("c:\\program files", "c:\\windows", "c:\\users", "c:\\programdata")
SYSTEM_SERVICE = "postgresql-x64-18"
SAFE_NAME = re.compile(r"^Tester[A-Za-z0-9_-]{1,80}$")
_RESERVED = frozenset({"con", "prn", "aux", "nul"}) | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}
_WIN_INVALID = re.compile(r'[<>:"|?*\\x00-\\x1f]')


@dataclass(frozen=True)
class Gate:
    allowed: bool
    reason: str
    targets: tuple[str, ...] = ()


def canonical(path: object) -> str | None:
    if not isinstance(path, str) or not path or "/" in path or "\\?\\" in path:
        return None
    value = ntpath.normcase(ntpath.normpath(path))
    drive, tail = ntpath.splitdrive(value)
    if drive != "c:" or not tail.startswith("\\") or value in ("c:\\", "c:"):
        return None
    if value.startswith("\\\\"):
        return None
    raw_drive, raw_tail = ntpath.splitdrive(path)
    if raw_drive.casefold() != "c:" or not raw_tail.startswith("\\"):
        return None
    parts = raw_tail.split("\\")[1:]
    if not parts or any(
        part in ("", ".", "..")
        or part.endswith((".", " "))
        or _WIN_INVALID.search(part)
        or part.split(".")[0].casefold() in _RESERVED
        for part in parts
    ):
        return None
    return value


def _child(path: str, root: str) -> bool:
    return path.startswith(root + "\\")


def validate_manifest(manifest: Mapping, *, attested: Mapping | None = None) -> Gate:
    """Fail closed; caller supplies independent Windows observations.

    attested requires canonical paths, service image paths, cluster port and
    a list of every reparse point observed along each target ancestry.
    """
    if not isinstance(manifest, Mapping) or not isinstance(attested, Mapping):
        return Gate(False, "independent_attestation_missing")
    if manifest.get("schema") != SCHEMA or manifest.get("guardians_enabled") is not False:
        return Gate(False, "schema_or_guardian_gate")
    root = canonical(manifest.get("root"))
    cluster = canonical(manifest.get("pg_data"))
    if (
        root is None
        or cluster is None
        or any(root == p or root.startswith(p + "\\") for p in FORBIDDEN)
    ):
        return Gate(False, "unsafe_root")
    if not _child(cluster, root):
        return Gate(False, "foreign_cluster")
    port = manifest.get("pg_port")
    if type(port) is not int or not 1024 <= port <= 65535 or port == 5432:
        return Gate(False, "system_postgres_port")
    source_sha = manifest.get("source_sha")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        return Gate(False, "unpinned_commit")
    if attested.get("source_sha") != source_sha:
        return Gate(False, "commit_attestation_mismatch")
    digest = manifest.get("package_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return Gate(False, "unpinned_package")
    services = manifest.get("services")
    if not isinstance(services, dict) or not services or len(services) > 4:
        return Gate(False, "service_manifest_invalid")
    for name, image in services.items():
        normalized = canonical(image)
        if (
            not isinstance(name, str)
            or not SAFE_NAME.fullmatch(name)
            or name.casefold() == SYSTEM_SERVICE
            or normalized is None
            or not _child(normalized, root)
        ):
            return Gate(False, "foreign_service")
    targets = manifest.get("delete_targets")
    if not isinstance(targets, list) or not targets or len(targets) > 30:
        return Gate(False, "invalid_targets")
    normalized_targets = [canonical(p) for p in targets]
    if any(p is None or not (p == root or _child(p, root)) for p in normalized_targets):
        return Gate(False, "foreign_delete_target")
    if len(set(normalized_targets)) != len(normalized_targets):
        return Gate(False, "duplicate_targets")
    if attested.get("root") != root or attested.get("pg_data") != cluster:
        return Gate(False, "directory_identity_mismatch")
    if attested.get("pg_port") != port or attested.get("services") != {
        name: canonical(image) for name, image in services.items()
    }:
        return Gate(False, "service_identity_mismatch")
    if attested.get("reparse_points") != []:
        return Gate(False, "reparse_or_unknown")
    if attested.get("system_pg_5432_unchanged") is not True:
        return Gate(False, "system_pg_not_attested")
    return Gate(True, "attested_plan_only", tuple(normalized_targets))


def uninstall_authorized(phrase: object, second_confirmation: object, gate: Gate) -> bool:
    """Only an authorization predicate; NEVER performs deletion."""
    return (
        gate.allowed is True
        and type(phrase) is str
        and phrase == "DELETE TESTER"
        and second_confirmation is True
    )


def manifest_fingerprint(manifest: Mapping) -> str:
    """Deterministic digest, NOT a digital signature."""
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
