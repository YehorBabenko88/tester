"""OPS #73 immutable producer delta: read-only Git object and admission test.

This is a source inventory gate, NOT authentication of GitHub runner/caller.
Never writes to installed checkout, services, PC4 or PostgreSQL.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs/ops_producer_allowlist_delta_36774155.json"


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


class OpsSourceDeltaLedger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        cls.source = cls.ledger["source"]
        for rev in (cls.source["base_sha"], cls.source["ops_source_sha"],
                    cls.source["master_review_sha"]):
            actual = git("rev-parse", rev + "^{commit}").decode().strip()
            if actual != rev:
                raise AssertionError("unverified immutable commit: " + rev)
        cls.master = json.loads(git(
            "show", cls.source["master_review_sha"] +
            ":docs/coordination/master-reviewed-producer-files.json"
        ))
        cls.entries = cls.ledger["reviewed_delta"]

    def test_exact_master_allowlist_delta_and_classification(self):
        approved = self.master["producers"]["OPS"]
        diff = git("diff", "--name-only", "--no-renames",
                   self.source["base_sha"], self.source["ops_source_sha"]).decode().splitlines()
        expected = sorted(set(diff) - set(approved))
        actual = [entry["path"] for entry in self.entries]
        self.assertEqual(len(approved), 65)
        self.assertEqual(len(diff), 79)
        self.assertEqual(len(expected), 14)
        self.assertEqual(actual, expected)
        self.assertEqual(len(set(diff)), len(diff))
        self.assertEqual(len(set(actual)), 14)
        self.assertFalse(set(approved) - set(diff))
        self.assertEqual(self.ledger["counts"]["production_new_files"], 0)
        for entry in self.entries:
            self.assertFalse(entry["production_admission"])
            path = entry["path"]
            role = entry["role"]
            if path.startswith(".github/"):
                self.assertEqual(role, "ci_only_workflow")
            elif path.startswith("docs/"):
                self.assertEqual(role, "documentation")
            elif path.startswith("tests/"):
                self.assertEqual(role, "ci_only_test")
            else:
                self.assertEqual(path, "scripts/ops_private_generation_prototype.py")
                self.assertEqual(role, "ci_only_prototype")

    def test_exact_git_modes_blob_hashes_and_no_symlinks(self):
        manifest_sha = git(
            "rev-parse", self.source["master_review_sha"] +
            ":docs/coordination/master-reviewed-producer-files.json"
        ).decode().strip()
        self.assertEqual(manifest_sha, self.source["master_allowlist_git_blob_sha1"])
        for entry in self.entries:
            path = entry["path"]
            self.assertTrue(path and not path.startswith("/") and "\\" not in path)
            self.assertNotIn("..", path.split("/"))
            tree_line = git("ls-tree", self.source["ops_source_sha"], "--", path).decode().strip()
            self.assertEqual(
                tree_line,
                entry["mode"] + " blob " + entry["git_blob_sha1"] + "\t" + path,
            )
            self.assertEqual(entry["mode"], "100644")
            raw = git("show", self.source["ops_source_sha"] + ":" + path)
            git_oid = hashlib.sha1(
                b"blob " + str(len(raw)).encode("ascii") + b"\x00" + raw
            ).hexdigest()
            self.assertEqual(git_oid, entry["git_blob_sha1"])

    def test_no_automatic_production_admission_or_native_activation(self):
        self.assertEqual(self.ledger["schema"], "tester-ops-producer-delta-ledger/1")
        self.assertEqual(
            self.ledger["policy"]["status"],
            "REVIEWED_SOURCE_ONLY_NOT_MASTER_ADMITTED",
        )
        self.assertFalse(self.ledger["policy"]["production_admission"])
        self.assertFalse(self.ledger["policy"]["native_launcher_automatic_activation"])
        self.assertTrue(self.ledger["policy"]["prototype_requires_opt_in"])


if __name__ == "__main__":
    unittest.main()
