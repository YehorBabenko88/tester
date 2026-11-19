"""Offline Ed25519 manifest verification; never trusts the manifest's own key.

The trusted public-key SHA256 must come from an independently approved release
channel. This function performs no I/O or installation side effects.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping

_CONTEXT = b"TESTER-INSTALL-MANIFEST-v1\x00"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX128 = re.compile(r"^[0-9a-f]{128}$")


def verify_signed_manifest(
    manifest: Mapping,
    *,
    signature_hex: str,
    public_key_hex: str,
    trusted_public_key_sha256: str,
) -> bool:
    """Return False on all missing, malformed, mismatched or invalid proofs."""
    if not isinstance(manifest, Mapping):
        return False
    if (
        type(signature_hex) is not str
        or not _HEX128.fullmatch(signature_hex)
        or type(public_key_hex) is not str
        or not _HEX64.fullmatch(public_key_hex)
        or type(trusted_public_key_sha256) is not str
        or not _HEX64.fullmatch(trusted_public_key_sha256)
    ):
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return False
    try:
        public_bytes = bytes.fromhex(public_key_hex)
        if not hmac.compare_digest(
            hashlib.sha256(public_bytes).hexdigest(), trusted_public_key_sha256
        ):
            return False
        canonical = json.dumps(
            dict(manifest), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(
            bytes.fromhex(signature_hex), _CONTEXT + canonical
        )
        return True
    except (InvalidSignature, ValueError, TypeError, OverflowError):
        return False
