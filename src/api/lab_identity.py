"""Read-only installed lab identity, bound to the executing immutable release.

These local metadata facts are host reported. They do not independently certify
Windows SCM state, PostgreSQL identity, source provenance or the entire file tree.
"""

import hashlib
import json
import os
import re
import uuid
from pathlib import Path, PurePosixPath

from psycopg import Error as PsycopgError
from psycopg.conninfo import conninfo_to_dict

from .lab_install import plain


class LabIdentityUnavailable(ValueError):
    def __init__(self):
        super().__init__("Installed lab identity cannot be verified")


def _json(path, limit):
    path = plain(path)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Metadata limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Metadata object required")
    return value, raw


def _sha(value, length=40):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(length) + "}", value)


def _inventory(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("Missing inventory")
    for name, digest in value.items():
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or ":" in name
            or str(path) != name
            or not _sha(digest, 64)
        ):
            raise ValueError("Invalid inventory")
    return value


def read_lab_identity(settings, *, _source_file=None):
    """Return safe identity facts, or a fixed failure. No DB/native/network writes.

    _source_file is a private test seam; the HTTP route must not expose it.
    The running instance is the same environment value reported by /api/health.
    """
    try:
        if (
            settings.provider_factory != "src.integration.lab_market:create_reader"
            or settings.handler_factory != "src.integration.lab_market:create_handler"
            or settings.host != "127.0.0.1"
            or settings.port != 18766
        ):
            raise ValueError("Lab opt-in required")
        pg = conninfo_to_dict(settings.dsn or "")
        if (
            pg.get("host") != "127.0.0.1"
            or pg.get("port") != "55470"
            or pg.get("dbname") != "tester_lab"
            or pg.get("service")
            or pg.get("hostaddr")
        ):
            raise ValueError("Dedicated lab configuration required")
        runtime = plain(settings.data_dir)
        root = plain(runtime.parent)
        if runtime != root / "runtime":
            raise ValueError("Dedicated runtime required")
        marker, _ = _json(root / ".tester-lab.json", 1024 * 1024)
        ident = str(uuid.UUID(marker["installation_id"]))
        if ident != marker["installation_id"]:
            raise ValueError("Noncanonical installation ID")
        suffix = uuid.UUID(ident).hex
        services = dict(app="TesterLabApp_" + suffix, postgres="TesterLabPG_" + suffix)
        sha = marker["active_sha"]
        if (
            marker["schema"] != "tester-lab/v1"
            or marker["root"] != str(root)
            or marker["repository"] != "YehorBabenko88/tester"
            or not _sha(sha)
            or marker["ports"] != dict(api=18766, postgres=55470)
            or marker["services"] != services
        ):
            raise ValueError("Unbound marker")
        source = plain(Path(__file__) if _source_file is None else _source_file)
        if source != root / "releases" / sha / "src/api/lab_identity.py":
            raise ValueError("Executing release differs")
        payload, raw = _json(root / "state/deploy-payload.json", 1024 * 1024)
        files = _inventory(payload["files"])
        if (
            payload["schema_version"] != "tester-lab-payload/v1"
            or payload["source_sha"] != sha
            or not _sha(payload["tree_sha"])
            or marker["releases"][sha] != files
        ):
            raise ValueError("Unbound deployment manifest")
        # Only this executing module is rehashed; the endpoint makes no full-tree
        # integrity claim and never rescans source/dependency directories.
        with source.open("rb") as stream:
            module_bytes = stream.read(256 * 1024 + 1)
        if (
            len(module_bytes) > 256 * 1024
            or hashlib.sha256(module_bytes).hexdigest() != files["src/api/lab_identity.py"]
        ):
            raise ValueError("Identity module differs")
        report, _ = _json(runtime / "service.json", 64 * 1024)
        instance = os.environ.get("TESTER_SERVICE_INSTANCE")
        if (
            not _sha(instance, 32)
            or report["instance_id"] != instance
            or report["source_sha"] != sha
            or report["installation_id"] != ident
            or report["service"] != services["app"]
        ):
            raise ValueError("Runtime identity differs")
        return dict(
            schema_version="1.0",
            data=dict(
                source_sha=sha,
                tree_sha=payload["tree_sha"],
                deploy_manifest_sha256=hashlib.sha256(raw).hexdigest(),
                installation_id=ident,
                services=services,
                instance_id=instance,
                api_endpoint="http://127.0.0.1:18766",
                postgres_port=55470,
                provenance="host_reported",
                scm_state="not_independently_verified",
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, PsycopgError):
        raise LabIdentityUnavailable() from None
