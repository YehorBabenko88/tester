import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.api.guardians.windows_release_signature import verify_signed_manifest


def proof(manifest):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    payload = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return {
        "signature_hex": private.sign(b"TESTER-INSTALL-MANIFEST-v1\x00" + payload).hex(),
        "public_key_hex": public.hex(),
        "trusted_public_key_sha256": hashlib.sha256(public).hexdigest(),
    }


def test_signed_manifest_requires_independently_pinned_key():
    manifest = {
        "schema": "tester-install/v1",
        "source_sha": "a" * 40,
        "root": "c:\\testerlab",
        "guardians_enabled": False,
    }
    signed = proof(manifest)
    assert verify_signed_manifest(manifest, **signed)
    assert not verify_signed_manifest({**manifest, "source_sha": "b" * 40}, **signed)
    assert not verify_signed_manifest({**manifest, "guardians_enabled": True}, **signed)
    assert not verify_signed_manifest(
        manifest, **{**signed, "trusted_public_key_sha256": "0" * 64}
    )
    assert not verify_signed_manifest(
        manifest, **{**signed, "signature_hex": "0" * 128}
    )


def test_bad_proof_or_noncanonical_manifest_fail_closed():
    assert not verify_signed_manifest(
        {}, signature_hex="", public_key_hex="", trusted_public_key_sha256=""
    )
    manifest = {"ratio": float("nan")}
    signed = proof({"ratio": 1})
    assert not verify_signed_manifest(manifest, **signed)
    assert not verify_signed_manifest(None, **signed)
