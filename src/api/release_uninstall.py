"""Ownership-ledger-driven deletion; native handles pin every Windows object.

Unknown entries are never adopted. The ledger digest and release key fingerprint
must be independently trusted. Runtime creators must register owned identities;
an unregistered file makes removal fail closed, even under an owned directory.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

from src.api.guardians.windows_release_signature import verify_signed_manifest
from src.api.release_security import (
    HEX64,
    REPOSITORY,
    ReleaseSecurityError,
    VerifiedRelease,
    _unique_object,
    canonical_json,
    plain,
    read_json,
    relative_name,
    sha256,
)

MARKER = ".tester-release.json"


class _WindowsObject:
    """No-share-write/delete handles prevent directory/file swaps through deletion."""

    def __init__(self, path, *, delete=False, lock=True, write=False):
        from ctypes import wintypes

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.api.CreateFileW.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        self.api.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self.api.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        self.api.SetFilePointerEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_longlong,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        self.path = Path(path)
        is_dir = stat.S_ISDIR(self.path.lstat().st_mode)
        # Attribute-only directory opens do not enforce share-delete exclusion.
        # FILE_LIST_DIRECTORY (0x1) makes the directory lease participate in sharing.
        access = (
            0x20000
            | 0x80
            | (0x10000 if delete else 0)
            | (0x80000000 if not is_dir else 0x1)
            | (0x40000000 if write else 0)
        )
        self.handle = self.api.CreateFileW(
            str(self.path), access, 1 if lock else 7, None, 3, 0x02000000 | 0x00200000, None
        )
        if self.handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())

        class Info(ctypes.Structure):
            _fields_ = [
                ("attributes", wintypes.DWORD),
                ("creation", wintypes.FILETIME),
                ("access", wintypes.FILETIME),
                ("write", wintypes.FILETIME),
                ("volume", wintypes.DWORD),
                ("sizehigh", wintypes.DWORD),
                ("sizelow", wintypes.DWORD),
                ("links", wintypes.DWORD),
                ("indexhigh", wintypes.DWORD),
                ("indexlow", wintypes.DWORD),
            ]

        info = Info()
        if not self.api.GetFileInformationByHandle(self.handle, ctypes.byref(info)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())
        self.identity = [info.volume, (info.indexhigh << 32) | info.indexlow]
        self.is_dir = bool(info.attributes & 0x10)
        if info.attributes & 0x400 or (not self.is_dir and info.links != 1):
            self.close()
            raise ReleaseSecurityError("Reparse point or hardlink refused")

    def read_bytes(self, *, max_bytes=16 * 1024 * 1024):
        from ctypes import wintypes

        if not self.api.SetFilePointerEx(self.handle, 0, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        result = bytearray()
        buf = ctypes.create_string_buffer(65536)
        count = wintypes.DWORD()
        while True:
            if not self.api.ReadFile(self.handle, buf, len(buf), ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not count.value:
                return bytes(result)
            result.extend(buf.raw[: count.value])
            if len(result) > max_bytes:
                raise ReleaseSecurityError("Maintenance config too large")

    def digest(self):
        from ctypes import wintypes

        if not self.api.SetFilePointerEx(self.handle, 0, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        result = hashlib.sha256()
        buf = ctypes.create_string_buffer(1024 * 1024)
        count = wintypes.DWORD()
        while True:
            if not self.api.ReadFile(self.handle, buf, len(buf), ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not count.value:
                return result.hexdigest()
            result.update(buf.raw[: count.value])

    def delete(self):
        disposition = ctypes.c_ubyte(1)
        if not self.api.SetFileInformationByHandle(self.handle, 4, ctypes.byref(disposition), 1):
            raise ctypes.WinError(ctypes.get_last_error())
        self.close()

    def close(self):
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _identity(path):
    path = plain(path)
    if os.name == "nt":
        obj = _WindowsObject(path, lock=False)
        try:
            return obj.identity
        finally:
            obj.close()
    info = path.lstat()
    return [info.st_dev, info.st_ino]


def _tree(root):
    found = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            path = plain(Path(directory) / name)
            found[path.relative_to(root).as_posix()] = path
    return found


def _security_record(obj):
    """Query the security descriptor of the pinned native object, never a pathname."""
    import win32security

    descriptor = win32security.GetSecurityInfo(
        obj.handle,
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None:
        raise ReleaseSecurityError("Null DACL cannot establish creator provenance")
    aces = []
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        if len(ace) != 3:
            raise ReleaseSecurityError("Unsupported object-specific ACE")
        (kind, flags), mask, sid = ace
        aces.append((kind, flags, mask, win32security.ConvertSidToStringSid(sid)))
    return dict(
        owner=win32security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner()),
        protected=bool(descriptor.GetSecurityDescriptorControl()[0] & 0x1000),
        aces=aces,
    )


def _validate_writer_security(record, service_sid, *, boundary=False, known=False):
    """Unique virtual account owner + exact inherited ACL is kernel origin proof.

    Administrator/SYSTEM tampering is outside this trust model, like tampering with
    the external administrator-protected trust pin itself. Shared LocalService
    ownership is never accepted as dynamic creator provenance.
    """
    if not re.fullmatch(r"S-1-5-80-(?:[0-9]+-){4}[0-9]+", service_sid):
        raise ReleaseSecurityError("A unique virtual service-account SID is required")
    if not known and record["owner"] != service_sid:
        raise ReleaseSecurityError("Foreign creator SID in mutable writer boundary")
    if boundary and not record["protected"]:
        raise ReleaseSecurityError("Writer boundary must disable inherited access")
    expected = {"S-1-5-18": 0x1F01FF, "S-1-5-32-544": 0x1F01FF, service_sid: 0x1301BF}
    actual = {}
    for kind, flags, mask, sid in record["aces"]:
        if (
            kind != 0
            or sid in actual
            or flags & ~0x13
            or (boundary and flags != 3)
            or (not boundary and not flags & 0x10)
        ):
            raise ReleaseSecurityError("Unsupported, explicit or broadened writer ACL")
        actual[sid] = mask
    if actual != expected:
        raise ReleaseSecurityError("Writer ACL permits unknown identity or rights")


def _boundaries(ledger):
    result = ledger.get("writer_boundaries", [])
    if not isinstance(result, list):
        raise ReleaseSecurityError("Invalid writer boundaries")
    paths = set()
    for item in result:
        name = relative_name(item["path"])
        if name in paths or any(
            name.startswith(p + "/") or p.startswith(name + "/") for p in paths
        ):
            raise ReleaseSecurityError("Overlapping writer boundaries")
        paths.add(name)
        if ledger["entries"].get(name, {}).get("kind") != "dir":
            raise ReleaseSecurityError("Writer boundary is not an installer-created directory")
        if item["service_name"] not in ledger["marker"].get("services", {}).values():
            raise ReleaseSecurityError("Writer account is not an installed UUID-owned service")
        ident = uuid.UUID(ledger["marker"]["installation_id"]).hex
        expected = {"app": "TesterReleaseApp_" + ident, "postgres": "TesterReleasePG_" + ident}
        if ledger["marker"]["services"] != expected or not re.fullmatch(
            r"S-1-5-80-(?:[0-9]+-){4}[0-9]+", item["service_sid"]
        ):
            raise ReleaseSecurityError(
                "Boundary service identity is not bound to the installation UUID"
            )
    return result


def _enroll_boundary_accounts(boundaries):
    """Resolve accounts once during trusted installation, before sealing receipts.

    Removal reuses this externally pinned OS enrollment evidence even after SCM
    deregistration; every current descendant owner/DACL is still queried live.
    """
    if not boundaries:
        return
    import win32security

    for item in boundaries:
        sid, _, _ = win32security.LookupAccountName(None, "NT SERVICE\\" + item["service_name"])
        if win32security.ConvertSidToStringSid(sid) != item["service_sid"]:
            raise ReleaseSecurityError(
                "Writer SID does not match native service-account enrollment"
            )


def _boundary_for(name, boundaries):
    return next(
        (b for b in boundaries if name == b["path"] or name.startswith(b["path"] + "/")), None
    )


def _writer_proof(obj, name, boundary, *, known):
    _validate_writer_security(
        _security_record(obj),
        boundary["service_sid"],
        boundary=name == boundary["path"],
        known=known,
    )


def _validate_installed_mapping(mapping, signed_files):
    if not isinstance(mapping, dict) or not mapping or len(set(mapping.values())) != len(mapping):
        raise ReleaseSecurityError("Invalid installed signed-file mapping")
    required = {
        n
        for n in signed_files
        if n.startswith(("payload/", "runtimes/python/", "runtimes/postgres/"))
    }
    if not required.issubset(mapping):
        raise ReleaseSecurityError("All signed application and runtime files must be mapped")
    for source, installed in mapping.items():
        relative_name(source)
        relative_name(installed)
        if source not in signed_files:
            raise ReleaseSecurityError("Installed bytes lack signed provenance")


def _external_receipts(root, paths):
    paths = [plain(path) for path in paths]
    expected = {
        "TESTER \u2014 \u0417\u0430\u043f\u0443\u0441\u043a.lnk",
        "TESTER \u2014 \u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435.lnk",
    }
    if paths and (
        len(paths) != 2 or {p.name for p in paths} != expected or paths[0].parent != paths[1].parent
    ):
        raise ReleaseSecurityError(
            "Exactly the two finalized TESTER shortcuts may be externally enrolled"
        )
    result = {}
    for path in paths:
        if root == path or root in path.parents or not path.is_file():
            raise ReleaseSecurityError("Invalid external shortcut path")
        result[str(path)] = dict(identity=_identity(path), sha256=sha256(path))
    return result


def _trust_config_plan(root, ledger_path, config_path):
    if config_path is None:
        return None
    path = plain(config_path)
    if (
        path.parent != ledger_path.parent
        or path == ledger_path
        or path.exists()
        or root in path.parents
    ):
        raise ReleaseSecurityError(
            "Trust config requires a new external sibling of the ownership ledger"
        )
    return dict(path=str(path), parent_identity=_identity(path.parent))


def _validate_admin_security(record):
    if record["owner"] not in ("S-1-5-18", "S-1-5-32-544") or not record["protected"]:
        raise ReleaseSecurityError("Maintenance trust requires protected SYSTEM/Admin ownership")
    actual = {}
    for kind, flags, mask, sid in record["aces"]:
        if kind != 0 or sid in actual or flags & ~3:
            raise ReleaseSecurityError("Unsupported maintenance ACL")
        actual[sid] = mask
    if actual != {"S-1-5-18": 0x1F01FF, "S-1-5-32-544": 0x1F01FF}:
        raise ReleaseSecurityError("Maintenance trust permits foreign access")


def write_ownership(
    root,
    marker,
    *,
    release,
    ledger_path,
    owned_paths,
    release_prefix="",
    writer_boundaries=(),
    installed_files=None,
    external_paths=(),
    trust_config_path=None,
    expected_identities=None,
    expected_root_identity=None,
):
    """Bind final enrollment to sealed creation IDs, including across owner changes.

    The canonical provisioning caller passes the mapping returned by
    seal_provisioning_outputs plus its original root ID. Older explicit fixture
    receipts can still use the creation-path contract without this transition.
    """
    root = plain(root)
    arguments = dict(
        release=release,
        ledger_path=ledger_path,
        owned_paths=owned_paths,
        release_prefix=release_prefix,
        writer_boundaries=writer_boundaries,
        installed_files=installed_files,
        external_paths=external_paths,
        trust_config_path=trust_config_path,
    )
    if expected_identities is None:
        if expected_root_identity is not None:
            raise ReleaseSecurityError(
                "Creation ID mapping required with the original root identity"
            )
        return _write_ownership_receipts(root, marker, **arguments)
    actual = _tree(root)
    if set(expected_identities) != set(actual):
        raise ReleaseSecurityError(
            "Final enrollment contains objects absent from sealed creation receipts"
        )
    entries = {
        name: dict(
            identity=_valid_identity(identity), kind="dir" if actual[name].is_dir() else "file"
        )
        for name, identity in expected_identities.items()
    }
    ledger = dict(
        root_identity=_valid_identity(expected_root_identity), marker=marker, writer_boundaries=[]
    )
    with _locked_tree(root, ledger, entries, delete=False):
        return _write_ownership_receipts(root, marker, **arguments)


def _write_ownership_receipts(
    root,
    marker,
    *,
    release,
    ledger_path,
    owned_paths,
    release_prefix="",
    writer_boundaries=(),
    installed_files=None,
    external_paths=(),
    trust_config_path=None,
):
    """Seal explicit installation creation receipts, without adopting extra entries.

    Store the returned SHA256 outside the installation under administrator control.
    Calling this on arbitrary pre-existing paths does NOT establish their ownership.
    """
    if not isinstance(release, VerifiedRelease):
        raise ReleaseSecurityError("Verified release receipt required")
    root, ledger_path = plain(root), plain(ledger_path)
    if root == ledger_path or root in ledger_path.parents:
        raise ReleaseSecurityError("Ownership ledger must be outside deleted root")
    ident = str(uuid.UUID(marker["installation_id"]))
    if (
        marker.get("root") != str(root)
        or marker.get("schema") != "tester-release-install/v1"
        or marker.get("source_sha") != release.source_sha
        or marker.get("release_manifest_sha256") != release.manifest_sha256
    ):
        raise ReleaseSecurityError("Installation marker does not bind verified release")
    declared = {relative_name(name) for name in owned_paths}
    actual = _tree(root)
    if declared != set(actual) or MARKER not in declared:
        raise ReleaseSecurityError("Unregistered files or directories; never adopt foreign entries")
    if read_json(root / MARKER) != marker:
        raise ReleaseSecurityError("Marker mismatch")
    if release_prefix:
        relative_name(release_prefix)
    signed_files = dict(release.files)
    if installed_files is None:
        installed_files = {
            name: str(Path(release_prefix, name)).replace("\\", "/") for name in signed_files
        }
    _validate_installed_mapping(installed_files, signed_files)
    immutable = {installed: signed_files[source] for source, installed in installed_files.items()}
    for name, digest in immutable.items():
        if name not in actual or sha256(actual[name]) != digest:
            raise ReleaseSecurityError("Installed immutable release bytes mismatch")
    entries = {}
    for name, path in actual.items():
        entry = dict(identity=_identity(path), kind="dir" if path.is_dir() else "file")
        if path.is_file() and (
            name in immutable or name == MARKER or name.startswith(("releases/", "dependencies/"))
        ):
            entry["sha256"] = sha256(path)
        entries[name] = entry
    ledger = dict(
        schema="tester-ownership/v1",
        installation_id=ident,
        root=str(root),
        root_identity=_identity(root),
        marker=marker,
        entries=entries,
        release_envelope=json.loads(release.manifest),
        release_prefix=release_prefix,
        release_manifest_sha256=release.manifest_sha256,
        writer_boundaries=list(writer_boundaries),
        installed_files=installed_files,
        external_entries=_external_receipts(root, external_paths),
        trust_config=_trust_config_plan(root, ledger_path, trust_config_path),
        ledger_path=str(ledger_path),
        maintenance_files={
            name: entry["sha256"]
            for name, entry in entries.items()
            if entry["kind"] == "file"
            and "sha256" in entry
            and (
                name.startswith("releases/" + release.source_sha + "/")
                or name.startswith("dependencies/python/")
                or name.startswith("dependencies/venv/Lib/site-packages/")
            )
        },
    )
    boundaries = _boundaries(ledger)
    _enroll_boundary_accounts(boundaries)
    for name, path in actual.items():
        boundary = _boundary_for(name, boundaries)
        if boundary is not None:
            obj = _WindowsObject(path)
            try:
                _writer_proof(obj, name, boundary, known=True)
            finally:
                obj.close()
    encoded = canonical_json(ledger)
    with ledger_path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(encoded).hexdigest()


def _load_ledger(root, ledger_path, pin, key_pin, source_sha):
    encoded = plain(ledger_path).read_bytes()
    if (
        type(pin) is not str
        or not HEX64.fullmatch(pin)
        or hashlib.sha256(encoded).hexdigest() != pin
    ):
        raise ReleaseSecurityError("Ownership ledger external SHA256 pin mismatch")
    ledger = json.loads(encoded, object_pairs_hook=_unique_object)
    if (
        ledger.get("schema") != "tester-ownership/v1"
        or ledger.get("root") != str(root)
        or ledger.get("installation_id") != str(uuid.UUID(ledger["marker"]["installation_id"]))
    ):
        raise ReleaseSecurityError("Foreign ownership ledger")
    envelope = ledger["release_envelope"]
    manifest = envelope["manifest"]
    if (
        hashlib.sha256(canonical_json(envelope)).hexdigest() != ledger["release_manifest_sha256"]
        or not verify_signed_manifest(
            manifest,
            signature_hex=envelope["signature_hex"],
            public_key_hex=envelope["public_key_hex"],
            trusted_public_key_sha256=key_pin,
        )
        or manifest.get("schema") != "tester-release/v1"
        or manifest.get("release_version") != "1"
        or manifest.get("repository") != REPOSITORY
        or manifest.get("source_sha") != source_sha
        or ledger["marker"].get("source_sha") != source_sha
        or ledger["marker"].get("release_manifest_sha256") != ledger["release_manifest_sha256"]
    ):
        raise ReleaseSecurityError("Signed immutable release proof rejected")
    entries = ledger["entries"]
    if not isinstance(entries, dict) or not entries or MARKER not in entries:
        raise ReleaseSecurityError("Missing ownership entries")
    prefix = ledger["release_prefix"]
    if prefix:
        relative_name(prefix)
    mapping = ledger["installed_files"]
    _validate_installed_mapping(mapping, manifest["files"])
    for source, installed in mapping.items():
        if entries.get(installed, {}).get("sha256") != manifest["files"][source]:
            raise ReleaseSecurityError("Incomplete immutable release ownership")
    for name, entry in entries.items():
        relative_name(name)
        if entry.get("kind") not in ("file", "dir") or not isinstance(entry.get("identity"), list):
            raise ReleaseSecurityError("Invalid ownership identity")
    return ledger


def _preflight(root, ledger):
    if _identity(root) != ledger["root_identity"]:
        raise ReleaseSecurityError("Installation directory identity changed")
    actual = _tree(root)
    boundaries = _boundaries(ledger)
    entries = dict(ledger["entries"])
    for name, path in actual.items():
        known = name in entries
        boundary = _boundary_for(name, boundaries)
        if not known and boundary is None:
            raise ReleaseSecurityError("Unknown entries; no deletion authorized")
        if boundary is not None:
            obj = _WindowsObject(path, lock=False)
            try:
                same_identity = known and obj.identity == entries[name]["identity"]
                replaceable = not known or (
                    "sha256" not in entries[name] and name != boundary["path"]
                )
                authenticated_creation = replaceable and not same_identity
                _writer_proof(obj, name, boundary, known=not authenticated_creation)
                if authenticated_creation:
                    entries[name] = dict(
                        identity=obj.identity,
                        kind="dir" if obj.is_dir else "file",
                        writer_boundary=boundary["path"],
                    )
            finally:
                obj.close()
        entry = entries[name]
        if (
            _identity(path) != entry["identity"]
            or path.is_dir() != (entry["kind"] == "dir")
            or ("sha256" in entry and sha256(path) != entry["sha256"])
        ):
            raise ReleaseSecurityError("Owned object identity or immutable bytes changed")
    if (root / MARKER).exists() and read_json(root / MARKER) != ledger["marker"]:
        raise ReleaseSecurityError("Installation marker altered")
    return entries


def _write_evidence(stream, value):
    stream.write(canonical_json(value) + b"\n")
    stream.flush()
    os.fsync(stream.fileno())


@contextlib.contextmanager
def _locked_tree(root, ledger, entries, *, defer_completion=False, delete=True):
    if os.name != "nt":
        raise ReleaseSecurityError("Native handle-safe deletion currently supports Windows only")
    held, ancestors = {}, []
    try:
        for path in reversed(root.parents):
            ancestors.append(_WindowsObject(path))
        held[""] = _WindowsObject(root, delete=delete)
        if held[""].identity != ledger["root_identity"]:
            raise ReleaseSecurityError("Root swapped before lock")
        for name, entry in sorted(entries.items(), key=lambda pair: len(Path(pair[0]).parts)):
            if not (root / name).exists():
                continue  # A previous partial attempt may already have removed this identity.
            handle = _WindowsObject(root / name, delete=delete)
            held[name] = handle
            if handle.identity != entry["identity"] or handle.is_dir != (entry["kind"] == "dir"):
                raise ReleaseSecurityError("Object swapped before lock")
            if "sha256" in entry and handle.digest() != entry["sha256"]:
                raise ReleaseSecurityError("Immutable content changed before lock")
            boundary = _boundary_for(name, _boundaries(ledger))
            if boundary is not None:
                _writer_proof(handle, name, boundary, known="writer_boundary" not in entries[name])
        for path, entry in ledger.get("external_entries", {}).items():
            path = plain(path)
            if not path.exists():
                continue
            for parent in reversed(path.parents):
                ancestors.append(_WindowsObject(parent))
            handle = _WindowsObject(path, delete=delete)
            held["@external:" + str(path)] = handle
            if (
                handle.identity != entry["identity"]
                or handle.is_dir
                or handle.digest() != entry["sha256"]
            ):
                raise ReleaseSecurityError("External shortcut swapped before locking")
        config_plan = ledger.get("trust_config")
        if config_plan is not None:
            path = plain(config_plan["path"])
            if _identity(path.parent) != config_plan["parent_identity"]:
                raise ReleaseSecurityError("Maintenance trust directory swapped")
            for parent in reversed(path.parents):
                ancestors.append(_WindowsObject(parent))
            parent_handle = _WindowsObject(path.parent)
            ancestors.append(parent_handle)
            _validate_admin_security(_security_record(parent_handle))
            if path.exists():
                handle = _WindowsObject(path, delete=delete)
                held[("@config:" if defer_completion else "@external:") + str(path)] = handle
                _validate_admin_security(_security_record(handle))
                config = json.loads(handle.read_bytes(), object_pairs_hook=_unique_object)
                expected = dict(
                    schema="tester-maintenance-trust/v1",
                    root=str(root),
                    installation_id=ledger["installation_id"],
                    ledger=ledger["ledger_path"],
                    trusted_ledger_sha256=hashlib.sha256(canonical_json(ledger)).hexdigest(),
                    source_sha=ledger["marker"]["source_sha"],
                    manifest_sha256=ledger["release_manifest_sha256"],
                    trusted_public_key_sha256=hashlib.sha256(
                        bytes.fromhex(ledger["release_envelope"]["public_key_hex"])
                    ).hexdigest(),
                )
                if any(config.get(k) != v for k, v in expected.items()):
                    raise ReleaseSecurityError("Foreign maintenance trust config")
        if set(_tree(root)) != {n for n in held if n and not n.startswith("@")}:
            raise ReleaseSecurityError("Unknown object appeared while acquiring locks")
        yield held
    finally:
        for handle in reversed(list(held.values())):
            handle.close()
        for handle in reversed(ancestors):
            handle.close()


def uninstall(
    root,
    *,
    ledger_path,
    trusted_ledger_sha256,
    trusted_public_key_sha256,
    expected_source_sha,
    confirmations,
    evidence_path,
    resources=None,
    lifecycle_lock=None,
    defer_completion=False,
):
    """Delete only pinned known objects after explicit double confirmation.

    A supplies the single native SCM adapter and shared lifecycle lock. The caller
    must show disclose_scope() before collecting confirmations. No shell deletion,
    recursion, PostgreSQL client, alternate SCM manager or process killer is used.
    """
    if confirmations != ("DELETE TESTER", "YES"):
        raise ReleaseSecurityError("Both exact irreversible deletion confirmations required")
    root, ledger_path, evidence_path = plain(root), plain(ledger_path), plain(evidence_path)
    if root in evidence_path.parents or evidence_path == root or root in ledger_path.parents:
        raise ReleaseSecurityError("Ledger/evidence must be outside deleted installation")
    if resources is None or lifecycle_lock is None:
        from src.api import release_install, release_service

        resources = release_service
        lifecycle_lock = release_install.lifecycle_lock
    ledger = None
    evidence = dict(
        schema="tester-removal/v1",
        root=str(root),
        ledger_sha256=trusted_ledger_sha256,
        status="refused_or_partial",
        deleted_count=0,
    )
    if evidence_path.exists():
        previous = read_evidence(evidence_path)
        if (
            not root.exists()
            and previous.get("status") == "removed"
            and previous.get("ledger_sha256") == trusted_ledger_sha256
            and previous.get("root") == str(root)
        ):
            _load_ledger(
                root,
                ledger_path,
                trusted_ledger_sha256,
                trusted_public_key_sha256,
                expected_source_sha,
            )
            return {**previous, "status": "already_removed"}
        raise ReleaseSecurityError("Evidence path exists; use a new unique receipt for recovery")
    with evidence_path.open("xb") as stream:
        _write_evidence(stream, evidence)
        try:
            with lifecycle_lock(root):
                ledger = _load_ledger(
                    root,
                    ledger_path,
                    trusted_ledger_sha256,
                    trusted_public_key_sha256,
                    expected_source_sha,
                )
                _preflight(root, ledger)
                for path, entry in ledger.get("external_entries", {}).items():
                    path = plain(path)
                    if path.exists() and (
                        _identity(path) != entry["identity"] or sha256(path) != entry["sha256"]
                    ):
                        raise ReleaseSecurityError("External shortcut identity changed")
                marker = ledger["marker"]
                resources.validate_resources(marker)
                resources.stop_and_prove_exit(marker)
                entries = _preflight(root, ledger)
                with _locked_tree(root, ledger, entries, defer_completion=defer_completion) as held:
                    resources.validate_resources(marker)
                    resources.remove_resources(marker)
                    for name in (n for n in held if n.startswith("@external:")):
                        held[name].delete()
                        evidence["deleted_count"] += 1
                        _write_evidence(stream, evidence)
                    for name in sorted(
                        (n for n in held if n and not n.startswith("@")),
                        key=lambda p: (p != MARKER, len(Path(p).parts), p),
                        reverse=True,
                    ):
                        held[name].delete()
                        evidence["deleted_count"] += 1
                        _write_evidence(stream, evidence)
                    held[""].delete()
                if root.exists():
                    raise ReleaseSecurityError("Root removal not proven")
                evidence["status"] = (
                    "root_removed_pending_maintenance_cleanup" if defer_completion else "removed"
                )
                _write_evidence(stream, evidence)
                return evidence
        except Exception as exc:
            evidence["error_type"] = type(exc).__name__
            _write_evidence(stream, evidence)
            raise


def disclose_scope(ledger_path, trusted_ledger_sha256):
    encoded = plain(ledger_path).read_bytes()
    if hashlib.sha256(encoded).hexdigest() != trusted_ledger_sha256:
        raise ReleaseSecurityError("Untrusted deletion scope")
    ledger = json.loads(encoded, object_pairs_hook=_unique_object)
    return dict(
        root=ledger["root"],
        installation_id=ledger["installation_id"],
        services=ledger["marker"].get("services", {}),
        paths=sorted(ledger["entries"]),
        warning="IRREVERSIBLE: all registered TESTER data will be permanently deleted",
    )


def read_evidence(path):
    """Return the last complete fsynced JSONL record; tolerate a torn final write."""
    result = None
    for line in plain(path).read_bytes().splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        try:
            value = json.loads(line, object_pairs_hook=_unique_object)
        except (ValueError, TypeError) as exc:
            raise ReleaseSecurityError("Corrupt evidence record") from exc
        if value.get("schema") != "tester-removal/v1":
            raise ReleaseSecurityError("Foreign evidence record")
        result = value
    if result is None:
        raise ReleaseSecurityError("No complete durable evidence record")
    return result


# Provisioning journals are created before any root resource. Unlike the final
# ledger, they permit a signed payload to be only partly copied.
_PROVISION_PREFIXES = {
    "runtime",
    "state",
    "private",
    "backups",
    "postgres-data",
    "dependencies/venv",
}
_PROVISION_FILES = {"config.json", "config.pending"}


def _valid_identity(value):
    if not (
        isinstance(value, list)
        and len(value) == 2
        and all(type(n) is int and n >= 0 for n in value)
        and value[1] > 0
    ):
        raise ReleaseSecurityError("Invalid native provisioning identity")
    return value


def _actor_path(name, header):
    return name in header["allowed_actor_files"] or any(
        name == p or name.startswith(p + "/") for p in header["allowed_actor_prefixes"]
    )


def _parse_provisioning_journal(encoded, *, root, trusted_public_key_sha256, expected_source_sha):
    """Authenticate a complete fsynced prefix; never infer creation from a phase."""
    try:
        lines = encoded.splitlines(keepends=True)
        rows = [
            json.loads(line, object_pairs_hook=_unique_object)
            for line in lines
            if line.endswith(b"\n")
        ]
        if not rows or not lines[0].endswith(b"\n"):
            raise ReleaseSecurityError("Provisioning header was not durably completed")
        header = rows[0]
        marker = header["marker"]
        ident = str(uuid.UUID(header["installation_id"]))
        expected_services = {
            "app": "TesterReleaseApp_" + uuid.UUID(ident).hex,
            "postgres": "TesterReleasePG_" + uuid.UUID(ident).hex,
        }
        if (
            header["schema"] != "tester-provisioning/v1"
            or header["root"] != str(root)
            or marker["root"] != str(root)
            or marker["installation_id"] != ident
            or marker["schema"] != "tester-release-install/v1"
            or marker["services"] != expected_services
            or set(header["allowed_actor_prefixes"]) != _PROVISION_PREFIXES
            or set(header["allowed_actor_files"]) != _PROVISION_FILES
            or not re.fullmatch(r"S-1-5-80-(?:[0-9]+-){4}[0-9]+", header["actor_sid"])
        ):
            raise ReleaseSecurityError("Foreign provisioning authority or actor scope")
        _valid_identity(header["root_parent_identity"])
        envelope = header["release_envelope"]
        manifest = envelope["manifest"]
        if (
            set(envelope) != {"manifest", "signature_hex", "public_key_hex"}
            or not verify_signed_manifest(
                manifest,
                signature_hex=envelope["signature_hex"],
                public_key_hex=envelope["public_key_hex"],
                trusted_public_key_sha256=trusted_public_key_sha256,
            )
            or set(manifest) != {"schema", "release_version", "source_sha", "repository", "files"}
            or manifest["schema"] != "tester-release/v1"
            or manifest["release_version"] != "1"
            or manifest["repository"] != REPOSITORY
            or manifest["source_sha"] != expected_source_sha
            or not re.fullmatch("[0-9a-f]{40}", expected_source_sha)
            or marker["source_sha"] != expected_source_sha
            or hashlib.sha256(canonical_json(envelope)).hexdigest()
            != header["release_manifest_sha256"]
            or marker["release_manifest_sha256"] != header["release_manifest_sha256"]
        ):
            raise ReleaseSecurityError("Provisioning release signature/provenance rejected")
        files = manifest["files"]
        if not isinstance(files, dict) or not files:
            raise ReleaseSecurityError("Missing signed provisioning payload inventory")
        folded = set()
        for name, digest in files.items():
            relative_name(name)
            if name.casefold() in folded or type(digest) is not str or not HEX64.fullmatch(digest):
                raise ReleaseSecurityError("Invalid signed provisioning inventory")
            folded.add(name.casefold())
        external_paths = header.get("external_paths", [])
        expected_names = {
            "TESTER \u2014 \u0417\u0430\u043f\u0443\u0441\u043a.lnk",
            "TESTER \u2014 \u0423\u0434\u0430\u043b\u0435\u043d\u0438\u0435.lnk",
        }
        if external_paths and (
            len(external_paths) != 2
            or {Path(p).name for p in external_paths} != expected_names
            or any(
                not Path(p).is_absolute() or Path(p).parent != Path(marker["desktop"])
                for p in external_paths
            )
        ):
            raise ReleaseSecurityError("Foreign provisioning external shortcut scope")
        state = dict(
            header=header,
            root_identity=None,
            entries={},
            directories=set(),
            plans={},
            external_plans=set(),
            external_entries={},
            final_ledger=None,
        )
        for row in rows[1:]:
            event = row.get("event")
            if event == "root_created":
                identity = _valid_identity(row["identity"])
                if state["root_identity"] not in (None, identity):
                    raise ReleaseSecurityError("Conflicting provisioning root identities")
                state["root_identity"] = identity
            elif event == "planned_dir":
                state["directories"].add(relative_name(row["path"]))
            elif event == "planned_file":
                name, temp = relative_name(row["path"]), relative_name(row["temp"])
                if (
                    Path(name).parent != Path(temp).parent
                    or not re.fullmatch(r"\.tester-copy_[0-9a-f]{32}\.pending", Path(temp).name)
                    or type(row["sha256"]) is not str
                    or not HEX64.fullmatch(row["sha256"])
                ):
                    raise ReleaseSecurityError("Unsafe provisioning temporary-file plan")
                source = row["signed_source"]
                if source is not None and files.get(relative_name(source)) != row["sha256"]:
                    raise ReleaseSecurityError(
                        "Provisioning copy is not the signed source artifact"
                    )
                if name in state["plans"] and state["plans"][name] != row:
                    raise ReleaseSecurityError("Conflicting provisioning file plans")
                state["plans"][name] = row
            elif event == "created":
                name = relative_name(row["path"])
                kind = row["kind"]
                planned = (
                    name in state["directories"]
                    if kind == "dir"
                    else any(name in (p["path"], p["temp"]) for p in state["plans"].values())
                )
                if kind not in ("file", "dir") or not (planned or _actor_path(name, header)):
                    raise ReleaseSecurityError(
                        "Creation receipt has no authorized plan or actor scope"
                    )
                state["entries"][name] = dict(identity=_valid_identity(row["identity"]), kind=kind)
            elif event == "renamed":
                name, temp = relative_name(row["path"]), relative_name(row["temp"])
                plan = state["plans"].get(name)
                old = state["entries"].get(temp)
                identity = _valid_identity(row["identity"])
                if (
                    not plan
                    or plan["temp"] != temp
                    or not old
                    or old["kind"] != "file"
                    or old["identity"] != identity
                ):
                    raise ReleaseSecurityError(
                        "Rename is not bound to a recorded creation identity"
                    )
                state["entries"].pop(temp)
                state["entries"][name] = dict(identity=identity, kind="file", sha256=plan["sha256"])
            elif event == "finalized":
                name = relative_name(row["path"])
                old = state["entries"].get(name)
                if (
                    not old
                    or old["kind"] != "file"
                    or old["identity"] != _valid_identity(row["identity"])
                    or type(row["sha256"]) is not str
                    or not HEX64.fullmatch(row["sha256"])
                    or (name in state["plans"] and state["plans"][name]["sha256"] != row["sha256"])
                ):
                    raise ReleaseSecurityError("Finalized bytes lack matching creation provenance")
                old["sha256"] = row["sha256"]
            elif event == "planned_external":
                name = row["path"]
                if (
                    name not in external_paths
                    or row["kind"] != "file"
                    or row["purpose"] != "desktop-shortcut"
                ):
                    raise ReleaseSecurityError("External resource has no declared desktop scope")
                state["external_plans"].add(name)
            elif event == "external_created":
                name = row["path"]
                if name not in state["external_plans"]:
                    raise ReleaseSecurityError("External creation lacks a prior durable plan")
                state["external_entries"][name] = dict(identity=_valid_identity(row["identity"]))
            elif event == "external_finalized":
                name = row["path"]
                old = state["external_entries"].get(name)
                if (
                    not old
                    or old["identity"] != _valid_identity(row["identity"])
                    or type(row["sha256"]) is not str
                    or not HEX64.fullmatch(row["sha256"])
                ):
                    raise ReleaseSecurityError("External finalized bytes lack a creation identity")
                old["sha256"] = row["sha256"]
            elif event == "final_ledger_sealed":
                ledger_path, config_path = Path(row["ledger_path"]), Path(row["trust_config_path"])
                if (
                    not ledger_path.is_absolute()
                    or not config_path.is_absolute()
                    or ledger_path == root
                    or root in ledger_path.parents
                    or root in config_path.parents
                    or type(row["ledger_sha256"]) is not str
                    or not HEX64.fullmatch(row["ledger_sha256"])
                    or state["final_ledger"] not in (None, row)
                ):
                    raise ReleaseSecurityError("Invalid final provisioning ownership commitment")
                state["final_ledger"] = row
            elif event == "phase":
                if type(row["phase"]) is not str or row["phase"] in (
                    "ready",
                    "committed",
                    "completed",
                ):
                    raise ReleaseSecurityError(
                        "Committed installation requires confirmed full uninstall"
                    )
            else:
                raise ReleaseSecurityError("Unknown provisioning journal event")
        return state
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        if isinstance(exc, ReleaseSecurityError):
            raise
        raise ReleaseSecurityError("Malformed provisioning authority or journal") from exc


def _validate_provisioning_actor_security(record, actor_sid, *, initial=False):
    """Unknown output requires a unique kernel creator; Admin ownership is not enough."""
    if record["owner"] != actor_sid:
        raise ReleaseSecurityError("Unknown provisioning object has a foreign creator SID")
    allowed = {"S-1-5-18", "S-1-5-32-544", actor_sid}
    if not record["aces"]:
        raise ReleaseSecurityError("Empty provisioning ACL cannot authorize deletion")
    if initial:
        copy = {**record, "owner": "S-1-5-32-544"}
        _validate_admin_security(copy)
        return
    for kind, flags, mask, sid in record["aces"]:
        if kind != 0 or flags & ~0x1F or sid not in allowed or mask & ~0x1F01FF:
            raise ReleaseSecurityError("Provisioning actor ACL contains foreign access")


def _provisioning_entries(root, state):
    header = state["header"]
    if (root / MARKER).exists() and read_json(root / MARKER) != header["marker"]:
        raise ReleaseSecurityError("Provisioning marker belongs to a different installation")
    if _identity(root.parent) != header["root_parent_identity"]:
        raise ReleaseSecurityError("Provisioning root parent identity changed")
    root_handle = _WindowsObject(root, lock=False)
    try:
        if state["root_identity"] is None:
            _validate_provisioning_actor_security(
                _security_record(root_handle), header["actor_sid"]
            )
            root_identity = root_handle.identity
        else:
            root_identity = state["root_identity"]
            if root_handle.identity != root_identity:
                raise ReleaseSecurityError("Provisioning root was replaced")
    finally:
        root_handle.close()
    entries = {}
    for name, path in _tree(root).items():
        obj = _WindowsObject(path, lock=False)
        try:
            known = state["entries"].get(name)
            if (
                known
                and known["identity"] == obj.identity
                and (known["kind"] == "dir") == obj.is_dir
            ):
                if "sha256" not in known or obj.digest() == known["sha256"]:
                    entries[name] = dict(known)
                    continue
                if not _actor_path(name, header):
                    raise ReleaseSecurityError("Finalized provisioning content changed")
            plan = state["plans"].get(name)
            moved = state["entries"].get(plan["temp"]) if plan else None
            if (
                plan
                and moved
                and moved["kind"] == "file"
                and moved["identity"] == obj.identity
                and not obj.is_dir
                and obj.digest() == plan["sha256"]
            ):
                entries[name] = dict(identity=obj.identity, kind="file", sha256=plan["sha256"])
                continue
            initial = name in state["directories"] or any(
                name == p["temp"] for p in state["plans"].values()
            )
            if not _actor_path(name, header) and not initial:
                raise ReleaseSecurityError("Unknown provisioning object has no creator scope")
            _validate_provisioning_actor_security(
                _security_record(obj), header["actor_sid"], initial=not _actor_path(name, header)
            )
            entries[name] = dict(
                identity=obj.identity, kind="dir" if obj.is_dir else "file", provisioning_actor=True
            )
        finally:
            obj.close()
    return root_identity, entries


def _provisioning_external_entries(state):
    result = {}
    for name in state["external_plans"]:
        path = plain(name)
        if not path.exists():
            continue
        obj = _WindowsObject(path, lock=False)
        try:
            known = state["external_entries"].get(name)
            if obj.is_dir:
                raise ReleaseSecurityError("External provisioning shortcut became a directory")
            if known:
                if obj.identity != known["identity"] or (
                    "sha256" in known and obj.digest() != known["sha256"]
                ):
                    raise ReleaseSecurityError(
                        "External provisioning shortcut identity/content changed"
                    )
            else:
                _validate_provisioning_actor_security(
                    _security_record(obj), state["header"]["actor_sid"], initial=True
                )
            result[name] = dict(
                identity=obj.identity, sha256=obj.digest(), provisioning_actor=not bool(known)
            )
        finally:
            obj.close()
    return result


def _append_native_journal(obj, row):
    from ctypes import wintypes

    encoded = canonical_json(row) + b"\n"
    obj.api.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    obj.api.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    if not obj.api.SetFilePointerEx(obj.handle, 0, None, 2):
        raise ctypes.WinError(ctypes.get_last_error())
    count = wintypes.DWORD()
    if not obj.api.WriteFile(
        obj.handle, encoded, len(encoded), ctypes.byref(count), None
    ) or count.value != len(encoded):
        raise ReleaseSecurityError("Incomplete provisioning journal append")
    if not obj.api.FlushFileBuffers(obj.handle):
        raise ctypes.WinError(ctypes.get_last_error())


@contextlib.contextmanager
def _protected_provisioning_journal(path, *, write=False):
    path = plain(path)
    if os.name != "nt":
        raise ReleaseSecurityError("Provisioning rollback requires native Windows handles")
    ancestors = []
    obj = None
    try:
        for parent in reversed(path.parents):
            ancestors.append(_WindowsObject(parent))
        parent = ancestors[-1]
        _validate_admin_security(_security_record(parent))
        obj = _WindowsObject(path, write=write)
        _validate_admin_security(_security_record(obj))
        yield obj
    finally:
        if obj is not None:
            obj.close()
        for handle in reversed(ancestors):
            handle.close()


def _provisioning_resources(resources, lifecycle_lock):
    if resources is None:
        from src.api import release_provision

        resources = release_provision
    if lifecycle_lock is None:
        from src.api.release_install import lifecycle_lock
    return resources, lifecycle_lock


def _locked_provisioning_proof(root, state, root_identity, entries, held, external_entries=None):
    if _identity(root.parent) != state["header"]["root_parent_identity"]:
        raise ReleaseSecurityError("Provisioning parent swapped before native lock")
    if (
        MARKER in held
        and json.loads(held[MARKER].read_bytes(), object_pairs_hook=_unique_object)
        != state["header"]["marker"]
    ):
        raise ReleaseSecurityError("Provisioning marker changed before native lock")
    if state["root_identity"] is None:
        _validate_provisioning_actor_security(
            _security_record(held[""]), state["header"]["actor_sid"]
        )
    for path, entry in (external_entries or {}).items():
        if entry.get("provisioning_actor"):
            _validate_provisioning_actor_security(
                _security_record(held["@external:" + path]),
                state["header"]["actor_sid"],
                initial=True,
            )
    for name, entry in entries.items():
        if entry.get("provisioning_actor") and name in held:
            initial = not _actor_path(name, state["header"])
            _validate_provisioning_actor_security(
                _security_record(held[name]), state["header"]["actor_sid"], initial=initial
            )


def seal_provisioning_outputs(
    root,
    *,
    journal_path,
    trusted_public_key_sha256,
    expected_source_sha,
    resources=None,
    lifecycle_lock=None,
):
    """Seal only authenticated stopped-actor outputs before owner/ACL transitions."""
    root, journal_path = plain(root), plain(journal_path)
    if root == journal_path or root in journal_path.parents:
        raise ReleaseSecurityError("Provisioning journal must be external")
    resources, lifecycle_lock = _provisioning_resources(resources, lifecycle_lock)
    with lifecycle_lock(root), _protected_provisioning_journal(journal_path, write=True) as journal:
        encoded = journal.read_bytes(max_bytes=128 * 1024 * 1024)
        if not encoded.endswith(b"\n"):
            raise ReleaseSecurityError(
                "Incomplete provisioning append requires rollback before resealing"
            )
        state = _parse_provisioning_journal(
            encoded,
            root=root,
            trusted_public_key_sha256=trusted_public_key_sha256,
            expected_source_sha=expected_source_sha,
        )
        resources.validate_resources(state["header"]["marker"])
        resources.stop_and_prove_exit(state["header"]["marker"])
        root_identity, entries = _provisioning_entries(root, state)
        externals = _provisioning_external_entries(state)
        ledger = dict(
            root_identity=root_identity,
            marker=state["header"]["marker"],
            writer_boundaries=[],
            external_entries=externals,
        )
        with _locked_tree(root, ledger, entries, delete=False) as held:
            _locked_provisioning_proof(root, state, root_identity, entries, held, externals)
            for name, entry in entries.items():
                if entry.get("provisioning_actor"):
                    _append_native_journal(
                        journal,
                        dict(
                            event="created",
                            path=name,
                            identity=entry["identity"],
                            kind=entry["kind"],
                        ),
                    )
                if entry["kind"] == "file" and name.startswith("dependencies/venv/"):
                    _append_native_journal(
                        journal,
                        dict(
                            event="finalized",
                            path=name,
                            identity=entry["identity"],
                            sha256=held[name].digest(),
                        ),
                    )
        return {name: list(entries[name]["identity"]) for name in sorted(entries)}


def _final_provisioning_rollback(
    root, state, *, trusted_public_key_sha256, expected_source_sha, evidence_path, final_resources
):
    commitment = state["final_ledger"]
    ledger_path = plain(commitment["ledger_path"])
    ledger = _load_ledger(
        root,
        ledger_path,
        commitment["ledger_sha256"],
        trusted_public_key_sha256,
        expected_source_sha,
    )
    config = ledger.get("trust_config")
    if (
        ledger["marker"] != state["header"]["marker"]
        or ledger["installation_id"] != state["header"]["installation_id"]
        or ledger["release_manifest_sha256"] != state["header"]["release_manifest_sha256"]
        or (
            state["root_identity"] is not None and ledger["root_identity"] != state["root_identity"]
        )
        or not config
        or config["path"] != commitment["trust_config_path"]
    ):
        raise ReleaseSecurityError(
            "Final ownership ledger is not the fresh provisioning transaction"
        )
    if final_resources is None:
        from src.api import release_service

        final_resources = release_service
    if evidence_path.exists():
        previous = read_evidence(evidence_path)
        if previous.get("status") != "removed":
            evidence_path = evidence_path.with_name(
                evidence_path.stem + "-retry-" + uuid.uuid4().hex + ".jsonl"
            )
    # This is rollback of an authenticated, never-committed new installation, not
    # a user uninstall. The parser rejects ready/completed/committed transactions.
    result = uninstall(
        root,
        ledger_path=ledger_path,
        trusted_ledger_sha256=commitment["ledger_sha256"],
        trusted_public_key_sha256=trusted_public_key_sha256,
        expected_source_sha=expected_source_sha,
        confirmations=("DELETE TESTER", "YES"),
        evidence_path=evidence_path,
        resources=final_resources,
        lifecycle_lock=lambda unused: contextlib.nullcontext(),
    )
    if result["status"] not in ("removed", "already_removed"):
        raise ReleaseSecurityError("Full ownership rollback did not complete")
    return {
        **result,
        "status": "already_rolled_back" if result["status"] == "already_removed" else "rolled_back",
        "provisioning_header_sha256": hashlib.sha256(canonical_json(state["header"])).hexdigest(),
    }


def rollback_provisioning(
    root,
    *,
    journal_path,
    trusted_public_key_sha256,
    expected_source_sha,
    resources=None,
    lifecycle_lock=None,
    evidence_path=None,
    final_resources=None,
):
    """Roll back a never-committed fresh install; never adopt arbitrary Admin files."""
    root, journal_path = plain(root), plain(journal_path)
    if root == journal_path or root in journal_path.parents:
        raise ReleaseSecurityError("Provisioning journal must be external")
    evidence_path = plain(evidence_path or journal_path.with_suffix(".rollback.jsonl"))
    if root == evidence_path or root in evidence_path.parents or evidence_path == journal_path:
        raise ReleaseSecurityError("Provisioning evidence must be a distinct external file")
    resources, lifecycle_lock = _provisioning_resources(resources, lifecycle_lock)
    with lifecycle_lock(root), _protected_provisioning_journal(journal_path) as journal:
        state = _parse_provisioning_journal(
            journal.read_bytes(max_bytes=128 * 1024 * 1024),
            root=root,
            trusted_public_key_sha256=trusted_public_key_sha256,
            expected_source_sha=expected_source_sha,
        )
        if state["final_ledger"] is not None:
            return _final_provisioning_rollback(
                root,
                state,
                trusted_public_key_sha256=trusted_public_key_sha256,
                expected_source_sha=expected_source_sha,
                evidence_path=evidence_path,
                final_resources=final_resources,
            )
        pin = hashlib.sha256(canonical_json(state["header"])).hexdigest()
        if evidence_path.exists():
            previous = read_evidence(evidence_path)
            if (
                previous.get("root") != str(root)
                or previous.get("provisioning_header_sha256") != pin
            ):
                raise ReleaseSecurityError("Foreign provisioning evidence path")
            if not root.exists() and previous.get("status") == "rolled_back":
                return {**previous, "status": "already_rolled_back"}
        evidence = dict(
            schema="tester-removal/v1",
            root=str(root),
            provisioning_header_sha256=pin,
            status="refused_or_partial",
            deleted_count=0,
            attempt_id=uuid.uuid4().hex,
        )
        with evidence_path.open("ab" if evidence_path.exists() else "xb") as stream:
            _write_evidence(stream, evidence)
            try:
                marker = state["header"]["marker"]
                resources.validate_resources(marker)
                resources.stop_and_prove_exit(marker)
                if not root.exists():
                    resources.validate_resources(marker)
                    resources.remove_resources(marker)
                else:
                    root_identity, entries = _provisioning_entries(root, state)
                    externals = _provisioning_external_entries(state)
                    ledger = dict(
                        root_identity=root_identity,
                        marker=marker,
                        writer_boundaries=[],
                        external_entries=externals,
                    )
                    with _locked_tree(root, ledger, entries) as held:
                        _locked_provisioning_proof(
                            root, state, root_identity, entries, held, externals
                        )
                        resources.validate_resources(marker)
                        resources.remove_resources(marker)
                        for path in externals:
                            held["@external:" + path].delete()
                            evidence["deleted_count"] += 1
                            _write_evidence(stream, evidence)
                        for name in sorted(
                            entries, key=lambda n: (len(Path(n).parts), n), reverse=True
                        ):
                            held[name].delete()
                            evidence["deleted_count"] += 1
                            _write_evidence(stream, evidence)
                        held[""].delete()
                if root.exists():
                    raise ReleaseSecurityError("Provisioning root absence not proven")
                evidence["status"] = "rolled_back"
                _write_evidence(stream, evidence)
                return evidence
            except Exception as exc:
                evidence["error_type"] = type(exc).__name__
                _write_evidence(stream, evidence)
                raise


def main(argv=None):
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Permanently delete only cryptographically and physically owned TESTER resources"
    )
    for name in (
        "root",
        "ledger",
        "trusted-ledger-sha256",
        "trusted-public-key-sha256",
        "source-sha",
        "evidence-path",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--scope-only", action="store_true")
    parser.add_argument("--defer-completion", action="store_true")
    args = parser.parse_args(argv)
    root = plain(args.root)
    if (
        root == Path(sys.executable)
        or root in Path(sys.executable).parents
        or root == Path.cwd()
        or root in Path.cwd().parents
    ):
        raise ReleaseSecurityError(
            "Run maintenance from an external Python runtime and working directory"
        )
    _load_ledger(
        root,
        plain(args.ledger),
        args.trusted_ledger_sha256,
        args.trusted_public_key_sha256,
        args.source_sha,
    )
    scope = disclose_scope(args.ledger, args.trusted_ledger_sha256)
    print(json.dumps(scope, ensure_ascii=False, indent=2))
    print(
        "All registered application, PostgreSQL cluster, history, results, logs, cache, checkpoints, queues and backups will be irreversibly deleted."
    )
    if args.scope_only:
        return 0
    first = input("Type DELETE TESTER: ")
    second = input("Type YES: ")
    result = uninstall(
        root,
        ledger_path=args.ledger,
        trusted_ledger_sha256=args.trusted_ledger_sha256,
        trusted_public_key_sha256=args.trusted_public_key_sha256,
        expected_source_sha=args.source_sha,
        confirmations=(first, second),
        evidence_path=args.evidence_path,
        defer_completion=args.defer_completion,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
