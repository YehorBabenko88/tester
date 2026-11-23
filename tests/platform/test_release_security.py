"""Release verification tests use ephemeral keys, never production trust."""

import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.api import release_security as security


def signed_bundle(tmp_path, **changes):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "app.txt").write_bytes(b"TESTER")
    manifest = dict(
        schema="tester-release/v1",
        release_version="1",
        source_sha="a" * 40,
        repository="https://github.com/YehorBabenko88/tester",
        files={"app.txt": hashlib.sha256(b"TESTER").hexdigest()},
    )
    manifest.update(changes)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    envelope = dict(
        manifest=manifest,
        public_key_hex=public.hex(),
        signature_hex=private.sign(
            b"TESTER-INSTALL-MANIFEST-v1\x00" + security.canonical_json(manifest)
        ).hex(),
    )
    (bundle / "release-manifest.json").write_bytes(security.canonical_json(envelope))
    return bundle, hashlib.sha256(public).hexdigest()


def verify(bundle, pin):
    return security.verify_release(
        bundle, trusted_public_key_sha256=pin, expected_source_sha="a" * 40
    )


def test_valid_release_returns_bound_immutable_inventory(tmp_path):
    bundle, pin = signed_bundle(tmp_path)
    receipt = verify(bundle, pin)
    assert receipt.source_sha == "a" * 40
    assert receipt.files == (("app.txt", hashlib.sha256(b"TESTER").hexdigest()),)
    assert (
        receipt.manifest_sha256
        == hashlib.sha256((bundle / "release-manifest.json").read_bytes()).hexdigest()
    )
    with pytest.raises((AttributeError, TypeError)):
        receipt.source_sha = "b" * 40


@pytest.mark.parametrize(
    "mutation", ["tamper", "foreign", "unknown_key", "signature", "duplicate_json"]
)
def test_tampered_or_unknown_release_rejected(tmp_path, mutation):
    bundle, pin = signed_bundle(tmp_path)
    path = bundle / "release-manifest.json"
    if mutation == "tamper":
        (bundle / "app.txt").write_bytes(b"EVIL")
    elif mutation == "foreign":
        (bundle / "foreign").write_text("foreign")
    elif mutation == "unknown_key":
        pin = "0" * 64
    elif mutation == "signature":
        envelope = json.loads(path.read_bytes())
        envelope["manifest"]["source_sha"] = "b" * 40
        path.write_bytes(security.canonical_json(envelope))
    else:
        path.write_bytes(b'{"manifest":{},"manifest":{}}')
    with pytest.raises(security.ReleaseSecurityError):
        verify(bundle, pin)


@pytest.mark.parametrize(
    "change",
    [
        dict(release_version="2"),
        dict(source_sha="b" * 40),
        dict(repository="https://evil.example/tester"),
        dict(files={"../escape": "0" * 64}),
        dict(files={"C:/escape": "0" * 64}),
        dict(files={"APP.txt": "0" * 64, "app.txt": "0" * 64}),
    ],
)
def test_signed_but_unacceptable_provenance_or_paths_rejected(tmp_path, change):
    bundle, pin = signed_bundle(tmp_path, **change)
    with pytest.raises(security.ReleaseSecurityError):
        verify(bundle, pin)


def test_hardlinked_payload_rejected(tmp_path):
    bundle, pin = signed_bundle(tmp_path)
    (tmp_path / "foreign").hardlink_to(bundle / "app.txt")
    with pytest.raises(security.ReleaseSecurityError):
        verify(bundle, pin)


def test_external_owner_key_signs_exact_release_without_copying_private_material(tmp_path):
    from cryptography.hazmat.primitives import serialization

    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "owner-encrypted.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"test-only-password"),
        )
    )
    bundle = tmp_path / "payload"
    bundle.mkdir()
    (bundle / "app.exe").write_bytes(b"authorized release fixture")
    security.sign_release(
        bundle, private_key_path=key_path, source_sha="a" * 40, password=b"test-only-password"
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    verified = verify(bundle, hashlib.sha256(public).hexdigest())
    assert len(verified.files) == 1
    assert not list(bundle.glob("*.pem"))
    assert key_path.exists()


def test_owner_signer_rejects_key_inside_payload(tmp_path):
    bundle = tmp_path / "payload"
    bundle.mkdir()
    key = bundle / "private.pem"
    key.write_text("must never ship this")
    with pytest.raises(security.ReleaseSecurityError):
        security.sign_release(bundle, private_key_path=key, source_sha="a" * 40, password=None)
