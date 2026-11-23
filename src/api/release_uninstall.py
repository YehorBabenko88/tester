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

    def __init__(self, path, *, delete=False, lock=True):
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
        access = 0x80 | (0x10000 if delete else 0) | (0x80000000 if not is_dir else 0)
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


def write_ownership(root, marker, *, release, ledger_path, owned_paths, release_prefix=""):
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
    immutable = {
        str(Path(release_prefix, name)).replace("\\", "/"): digest for name, digest in release.files
    }
    for name, digest in immutable.items():
        if name not in actual or sha256(actual[name]) != digest:
            raise ReleaseSecurityError("Installed immutable release bytes mismatch")
    entries = {}
    for name, path in actual.items():
        entry = dict(identity=_identity(path), kind="dir" if path.is_dir() else "file")
        if name in immutable or name == MARKER:
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
    )
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
    for name, digest in manifest["files"].items():
        installed = str(Path(prefix, relative_name(name))).replace("\\", "/")
        if entries.get(installed, {}).get("sha256") != digest:
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
    if not set(actual).issubset(ledger["entries"]):
        raise ReleaseSecurityError("Unknown entries; no deletion authorized")
    for name, path in actual.items():
        entry = ledger["entries"][name]
        if (
            _identity(path) != entry["identity"]
            or path.is_dir() != (entry["kind"] == "dir")
            or ("sha256" in entry and sha256(path) != entry["sha256"])
        ):
            raise ReleaseSecurityError("Owned object identity or immutable bytes changed")
    if (root / MARKER).exists() and read_json(root / MARKER) != ledger["marker"]:
        raise ReleaseSecurityError("Installation marker altered")


def _write_evidence(stream, value):
    stream.write(canonical_json(value) + b"\n")
    stream.flush()
    os.fsync(stream.fileno())


@contextlib.contextmanager
def _locked_tree(root, ledger):
    if os.name != "nt":
        raise ReleaseSecurityError("Native handle-safe deletion currently supports Windows only")
    held, ancestors = {}, []
    try:
        for path in reversed(root.parents):
            ancestors.append(_WindowsObject(path))
        held[""] = _WindowsObject(root, delete=True)
        if held[""].identity != ledger["root_identity"]:
            raise ReleaseSecurityError("Root swapped before lock")
        for name, entry in sorted(
            ledger["entries"].items(), key=lambda pair: len(Path(pair[0]).parts)
        ):
            if not (root / name).exists():
                continue  # A previous partial attempt may already have removed this identity.
            handle = _WindowsObject(root / name, delete=True)
            held[name] = handle
            if handle.identity != entry["identity"] or handle.is_dir != (entry["kind"] == "dir"):
                raise ReleaseSecurityError("Object swapped before lock")
            if "sha256" in entry and handle.digest() != entry["sha256"]:
                raise ReleaseSecurityError("Immutable content changed before lock")
        if not set(_tree(root)).issubset(ledger["entries"]):
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
                marker = ledger["marker"]
                resources.validate_resources(marker)
                resources.stop_and_prove_exit(marker)
                _preflight(root, ledger)
                with _locked_tree(root, ledger) as held:
                    resources.validate_resources(marker)
                    resources.remove_resources(marker)
                    for name in sorted(
                        (n for n in held if n),
                        key=lambda p: (p != MARKER, len(Path(p).parts), p),
                        reverse=True,
                    ):
                        held[name].delete()
                        evidence["deleted_count"] += 1
                        _write_evidence(stream, evidence)
                    held[""].delete()
                if root.exists():
                    raise ReleaseSecurityError("Root removal not proven")
                evidence["status"] = "removed"
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
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
