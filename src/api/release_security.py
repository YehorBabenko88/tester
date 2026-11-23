"""Pinned-key release verification built on the existing Ed25519 verifier.

The fingerprint and expected source commit must arrive independently of the bundle.
No production key is embedded, and a Boolean is never accepted as release proof.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from src.api.guardians.windows_release_signature import verify_signed_manifest

REPOSITORY = "https://github.com/YehorBabenko88/tester"
MANIFEST = "release-manifest.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ReleaseSecurityError(ValueError):
    """Untrusted, altered, unsupported or unsafe release material."""


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseSecurityError("Duplicate JSON key")
        result[key] = value
    return result


def read_json(path):
    try:
        data = plain(path).read_bytes()
        if len(data) > 16 * 1024 * 1024:
            raise ReleaseSecurityError("Manifest exceeds size budget")
        return json.loads(data, object_pairs_hook=_unique_object)
    except (OSError, ValueError, TypeError) as exc:
        raise ReleaseSecurityError("Unreadable or malformed manifest") from exc


def relative_name(name):
    if type(name) is not str or not name:
        raise ReleaseSecurityError("Invalid inventory path")
    path = PurePosixPath(name)
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if (
        path.is_absolute()
        or str(path) != name
        or ".." in path.parts
        or "\\" in name
        or any(c in name for c in ':<>"|?*')
        or any(ord(c) < 32 for c in name)
        or any(p.endswith((" ", ".")) or p.split(".")[0].upper() in reserved for p in path.parts)
    ):
        raise ReleaseSecurityError("Unsafe inventory path")
    return name


def plain(path):
    path = Path(os.path.abspath(path))
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ReleaseSecurityError("Reparse/symlink paths are forbidden")
        if not stat.S_ISDIR(info.st_mode) and (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        ):
            raise ReleaseSecurityError("Nonregular or hardlinked file forbidden")
    return path


def sha256(path):
    digest = hashlib.sha256()
    with plain(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class VerifiedRelease:
    root: Path
    manifest_sha256: str
    source_sha: str
    release_version: str
    files: tuple[tuple[str, str], ...]
    manifest: bytes


def verify_release(
    bundle, *, trusted_public_key_sha256, expected_source_sha, supported_versions=("1",)
):
    """Reverify signature, exact inventory, version and independently pinned commit."""
    root = plain(bundle)
    envelope = read_json(root / MANIFEST)
    if not isinstance(envelope, dict) or set(envelope) != {
        "manifest",
        "signature_hex",
        "public_key_hex",
    }:
        raise ReleaseSecurityError("Invalid signature envelope")
    manifest = envelope["manifest"]
    if not verify_signed_manifest(
        manifest,
        signature_hex=envelope["signature_hex"],
        public_key_hex=envelope["public_key_hex"],
        trusted_public_key_sha256=trusted_public_key_sha256,
    ):
        raise ReleaseSecurityError("Manifest signature or independently pinned key rejected")
    if (
        set(manifest) != {"schema", "release_version", "source_sha", "repository", "files"}
        or manifest["schema"] != "tester-release/v1"
        or manifest["release_version"] not in supported_versions
        or type(expected_source_sha) is not str
        or not re.fullmatch("[0-9a-f]{40}", expected_source_sha)
        or manifest["source_sha"] != expected_source_sha
        or manifest["repository"] != REPOSITORY
    ):
        raise ReleaseSecurityError("Unsupported release or source provenance")
    inventory = manifest["files"]
    if not isinstance(inventory, dict) or not inventory:
        raise ReleaseSecurityError("Missing artifact inventory")
    folded = set()
    for name, digest in inventory.items():
        relative_name(name)
        if (
            name == MANIFEST
            or name.casefold() in folded
            or type(digest) is not str
            or not HEX64.fullmatch(digest)
        ):
            raise ReleaseSecurityError("Invalid or colliding artifact inventory")
        folded.add(name.casefold())
    actual = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            plain(Path(directory) / name)
        for name in files:
            path = plain(Path(directory) / name)
            relative = path.relative_to(root).as_posix()
            if relative != MANIFEST:
                actual[relative] = sha256(path)
    if actual != inventory:
        raise ReleaseSecurityError("Exact artifact SHA256 inventory mismatch")
    canonical = canonical_json(envelope)
    if (root / MANIFEST).read_bytes() != canonical:
        raise ReleaseSecurityError("Manifest envelope must use canonical JSON bytes")
    return VerifiedRelease(
        root,
        hashlib.sha256(canonical).hexdigest(),
        expected_source_sha,
        manifest["release_version"],
        tuple(sorted(inventory.items())),
        canonical,
    )


def sign_release(
    bundle, *, private_key_path, source_sha, password, release_version="1", manifest_input=None
):
    """Owner-only offline operation: attach a signature using an external PEM key.

    This does not generate or install a trust root. Distribute the public-key SHA256
    through a separately authenticated channel after owner provenance review.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from src.api.guardians.windows_release_signature import _CONTEXT

    root, key_path = plain(bundle), plain(private_key_path)
    if root == key_path or root in key_path.parents:
        raise ReleaseSecurityError("Private signing key must remain outside the release payload")
    if not re.fullmatch("[0-9a-f]{40}", source_sha) or release_version != "1":
        raise ReleaseSecurityError("Exact source SHA and supported release version required")
    if (root / MANIFEST).exists():
        raise ReleaseSecurityError("Never overwrite an existing signed manifest")
    inventory = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            plain(Path(directory) / name)
        for name in files:
            path = plain(Path(directory) / name)
            relative = relative_name(path.relative_to(root).as_posix())
            inventory[relative] = sha256(path)
    if not inventory:
        raise ReleaseSecurityError("Cannot sign an empty release")
    manifest = dict(
        schema="tester-release/v1",
        release_version=release_version,
        source_sha=source_sha,
        repository=REPOSITORY,
        files=inventory,
    )
    if manifest_input is not None and read_json(manifest_input) != manifest:
        raise ReleaseSecurityError(
            "Unsigned manifest differs from exact payload or source provenance"
        )
    private = serialization.load_pem_private_key(key_path.read_bytes(), password=password)
    if not isinstance(private, Ed25519PrivateKey):
        raise ReleaseSecurityError("Signing requires an Ed25519 private key")
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    envelope = dict(
        manifest=manifest,
        public_key_hex=public.hex(),
        signature_hex=private.sign(_CONTEXT + canonical_json(manifest)).hex(),
    )
    with (root / MANIFEST).open("xb") as stream:
        stream.write(canonical_json(envelope))
        stream.flush()
        os.fsync(stream.fileno())
    # Reverify output and exact inventory before reporting a signed package.
    verify_release(
        root,
        trusted_public_key_sha256=hashlib.sha256(public).hexdigest(),
        expected_source_sha=source_sha,
    )
    return envelope


def main(argv=None):
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    signing = commands.add_parser(
        "sign", help="Owner offline signing with an external encrypted PEM key"
    )
    signing.add_argument("--bundle", required=True)
    signing.add_argument("--private-key", required=True)
    signing.add_argument("--source-sha", required=True)
    signing.add_argument("--manifest-input")
    verification = commands.add_parser("verify")
    verification.add_argument("--bundle", required=True)
    verification.add_argument("--trusted-public-key-sha256", required=True)
    verification.add_argument("--source-sha", required=True)
    args = parser.parse_args(argv)
    if args.action == "sign":
        envelope = sign_release(
            args.bundle,
            private_key_path=args.private_key,
            source_sha=args.source_sha,
            password=getpass.getpass("External encrypted signing-key passphrase: ").encode("utf-8"),
            manifest_input=args.manifest_input,
        )
        print(
            "public_key_sha256="
            + hashlib.sha256(bytes.fromhex(envelope["public_key_hex"])).hexdigest()
        )
        print("manifest_sha256=" + sha256(Path(args.bundle) / MANIFEST))
    else:
        result = verify_release(
            args.bundle,
            trusted_public_key_sha256=args.trusted_public_key_sha256,
            expected_source_sha=args.source_sha,
        )
        print("verified_manifest_sha256=" + result.manifest_sha256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
