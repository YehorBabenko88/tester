"""DATA #72 full 25-file Git source -> OPS private-generation compatibility.

Isolated Linux CI only. Manifest digest is dynamically produced and therefore
NOT independent caller attestation; no production services, databases or HTTP.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs/coordination/data72-current-head-immutable-review.json"
OPS_GIT_BLOB = "d8e42103c15824d7ff316314127074ee983804b3"


def git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT, stderr=subprocess.PIPE)


def blob_hash(raw: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    ).hexdigest()


@unittest.skipUnless(
    os.getenv("DATA_25_FILE_CI") == "true",
    "requires independent disposable DATA 25-file private-generation workflow",
)
class Data25FilePrivateGeneration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if (os.getenv("CI") != "true"
                or os.getenv("GITHUB_ACTIONS") != "true"
                or os.getenv("RUNNER_OS") != "Linux"):
            raise AssertionError("disposable GitHub Linux job required")
        ledger = json.loads(LEDGER.read_bytes())
        cls.head = ledger["reviewed_data_head"]
        assert cls.head == "4581e415bd8979a4debee1eb56120bb21b9d95fd"
        assert ledger["status"] == "REVIEW_CANDIDATE_NOT_MASTER_APPROVED"
        assert ledger["release"] == "NO_GO"
        if git("rev-parse", cls.head + "^{commit}").decode().strip() != cls.head:
            raise AssertionError("missing frozen DATA source commit")
        diff = git("diff", "--name-only", "--no-renames",
                   ledger["base_sha"], cls.head).decode().splitlines()
        entries = ledger["files"]
        paths = [e["path"] for e in entries]
        if len(entries) != 25 or len(set(paths)) != 25 or paths != sorted(diff):
            raise AssertionError("exact 25 DATA Git source files required")
        module_file = Path(os.environ["DATA_25_OPS_MODULE"])
        runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve()
        if not module_file.is_relative_to(runner_temp):
            raise AssertionError("OPS module must remain outside checkout")
        source = module_file.read_bytes()
        if blob_hash(source) != OPS_GIT_BLOB:
            raise AssertionError("OPS 36774155 module object mismatch")
        spec = importlib.util.spec_from_file_location("ci_data25_ops", module_file)
        if spec is None or spec.loader is None:
            raise AssertionError("invalid OPS source")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.ops = module
        cls.blobs = []
        cls.originals = {}
        for entry in entries:
            path = entry["path"]
            raw = git("show", cls.head + ":" + path)
            sha = entry["git_blob_sha1"]
            mode = git("ls-tree", cls.head, "--", path).decode().strip()
            if (mode != "100644 blob " + sha + "\t" + path
                    or blob_hash(raw) != sha):
                raise AssertionError("untrusted source Git bytes " + path)
            cls.originals[path] = raw
            cls.blobs.append(module.ReviewedBlob(
                path, raw, hashlib.sha256(raw).hexdigest(), sha, 0o644,
            ))
        cls.pins = {"DATA": cls.head}
        cls.manifest = module.canonical({
            "schema": module.SCHEMA, "source_pins": cls.pins,
            "files": [
                dict(path=b.path, sha256=b.sha256,
                     git_blob_sha1=b.git_blob_sha1, mode=b.mode)
                for b in cls.blobs
            ],
        })
        cls.digest = hashlib.sha256(cls.manifest).hexdigest()
        out = runner_temp / "data72-25file-ops-compatible-manifest.json"
        out.write_bytes(cls.manifest)
        print("DATA25_CANONICAL_MANIFEST_JSON", cls.manifest.decode("utf-8"), flush=True)
        print("DATA25_MANIFEST_SHA256", cls.digest, flush=True)
        print("DATA25_MANIFEST_GIT_BLOB", blob_hash(cls.manifest), flush=True)
        print("DATA25_SOURCE_HEAD", cls.head, flush=True)
        print("DATA25_MANIFEST_SCOPE", "SOURCE_ONLY_SELF_ATTESTED", flush=True)

    def test_source_tree_and_private_receipt(self):
        module = self.ops
        originals = {p: (ROOT / p).read_bytes() for p in self.originals}
        with tempfile.TemporaryDirectory(prefix="data25-private-") as tmp:
            root = Path(tmp) / "private"
            root.mkdir(mode=0o700)
            first = module.publish_generation(
                root, blobs=self.blobs, manifest=self.manifest,
                manifest_sha256=self.digest, source_pins=self.pins)
            self.assertEqual(module.read_current(
                root, manifest=self.manifest, manifest_sha256=self.digest), first)
            receipt = json.loads((root / "generations" / first /
                                  "generation.json").read_bytes())
            self.assertEqual(receipt["status"], "VERIFIED")
            self.assertEqual(receipt["manifest_sha256"], self.digest)
            self.assertEqual((root / "generations" / first /
                              "src/storage/migrations/008_public_unknown_taker_side.sql"
                              ).read_bytes(),
                             self.originals["src/storage/migrations/008_public_unknown_taker_side.sql"])
            self.assertEqual(module.inspect_recovery(
                root, manifest=self.manifest,
                manifest_sha256=self.digest)["state"], "CURRENT_VALID")
        self.assertEqual(originals,
                         {p: (ROOT / p).read_bytes() for p in self.originals})

    def test_pin_old_and_refuse_gc(self):
        m = self.ops
        with tempfile.TemporaryDirectory(prefix="data25-pin-") as tmp:
            root = Path(tmp) / "private"
            root.mkdir(mode=0o700)
            kw = dict(blobs=self.blobs, manifest=self.manifest,
                      manifest_sha256=self.digest, source_pins=self.pins)
            old = m.publish_generation(root, **kw)
            new = m.publish_generation(root, **kw)
            self.assertNotEqual(old, new)
            self.assertEqual(m.read_pinned(
                root, old, manifest=self.manifest, manifest_sha256=self.digest), old)
            self.assertEqual(m.read_current(
                root, manifest=self.manifest, manifest_sha256=self.digest), new)
            for target in (old, new):
                with self.assertRaises(m.CompositionRefused):
                    m.retire_generation(root, target)

    def test_crash_preserves_current_and_no_partial_activation(self):
        m = self.ops
        with tempfile.TemporaryDirectory(prefix="data25-crash-") as tmp:
            root = Path(tmp) / "private"
            root.mkdir(mode=0o700)
            kw = dict(blobs=self.blobs, manifest=self.manifest,
                      manifest_sha256=self.digest, source_pins=self.pins)
            old = m.publish_generation(root, **kw)

            def fault(stage, _):
                if stage == "after_publish_before_current":
                    raise OSError("TEST_ONLY_PRE_CURRENT_FAULT")

            with self.assertRaisesRegex(OSError, "TEST_ONLY_PRE_CURRENT_FAULT"):
                m.publish_generation(root, fault=fault, **kw)
            report = m.inspect_recovery(
                root, manifest=self.manifest, manifest_sha256=self.digest)
            self.assertEqual(report["current"], old)
            self.assertEqual(len(report["verified"]), 2)
            self.assertFalse(report["gc_allowed"])
            self.assertFalse(report["auto_switch"])

    def test_corrupt_detached_generation_fails_closed(self):
        m = self.ops
        with tempfile.TemporaryDirectory(prefix="data25-corrupt-") as tmp:
            root = Path(tmp) / "private"
            root.mkdir(mode=0o700)
            kw = dict(blobs=self.blobs, manifest=self.manifest,
                      manifest_sha256=self.digest, source_pins=self.pins)
            first = m.publish_generation(root, **kw)
            second = m.publish_generation(root, **kw)
            file = root / "generations" / first / "src/ingest/execution_contract.py"
            file.write_bytes(file.read_bytes() + b"\n# deliberately corrupted isolated test")
            with self.assertRaises(m.CompositionRefused):
                m.read_pinned(root, first,
                              manifest=self.manifest, manifest_sha256=self.digest)
            self.assertEqual(m.read_current(
                root, manifest=self.manifest, manifest_sha256=self.digest), second)


if __name__ == "__main__":
    unittest.main()
