"""DATA #72 source-only compatibility with OPS private generation prototype.

CI-only filesystem evidence. The manifest hash is self-calculated and therefore
NOT external caller attestation. No PostgreSQL connection, live HTTP, native
launcher or installed checkout changes. Real PG14/18 acceptance is separate.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "docs/coordination/data72-private-generation-blob-manifest.json"


def git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT, stderr=subprocess.PIPE)


def blob_hash(content: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(content)).encode("ascii") + b"\x00" + content
    ).hexdigest()


@unittest.skipUnless(
    os.environ.get("DATA_PRIVATE_GENERATION_CI") == "true",
    "requires dedicated Linux disposable GitHub Actions source-only job",
)
class DataPrivateGenerationCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = json.loads(REVIEW.read_text(encoding="utf-8"))
        cls.source_sha = cls.review["master_source_pinned_data_sha"]
        cls.expected_sha = os.environ["DATA_TESTED_HEAD_SHA"]
        if (os.environ.get("GITHUB_ACTIONS") != "true"
                or os.environ.get("RUNNER_OS") != "Linux"
                or os.environ.get("CI") != "true"):
            raise AssertionError("GitHub Linux disposable CI is required, not external attestation")
        if git("rev-parse", "HEAD").decode().strip() != cls.expected_sha:
            raise AssertionError("source checkout changed during DATA private generation review")
        if cls.review["schema"] != "tester-data-private-generation-review/1":
            raise AssertionError("DATA review version mismatch")
        if cls.review["manifest_authority"] != "IN_BRANCH_REVIEW_MATERIAL_NOT_EXTERNALLY_ATTESTED":
            raise AssertionError("no unsupported external attestation claims permitted")
        module_path = Path(os.environ["DATA_PRIVATE_OPS_SOURCE"])
        if not module_path.is_file() or not module_path.is_relative_to(
            Path(os.environ["RUNNER_TEMP"])
        ):
            raise AssertionError("OPS module must be materialized only in RUNNER_TEMP")
        ops_code = module_path.read_text(encoding="utf-8")
        for forbidden in ("psycopg.connect", "sqlite3.connect", "subprocess.run(",
                          "subprocess.Popen(", "StartService(", "sc.exe",
                          "requests.get(", "urllib.request.urlopen("):
            if forbidden in ops_code:
                raise AssertionError("private prototype may not call installed services or databases")
        spec = importlib.util.spec_from_file_location("ci_data72_private_generation", module_path)
        if spec is None or spec.loader is None:
            raise AssertionError("cannot independently load reviewed OPS prototype")
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.prototype = module

        files = cls.review["reviewed_master_files"]
        paths = [f["path"] for f in files]
        if len(files) != 19 or sorted(paths) != paths or len(set(paths)) != 19:
            raise AssertionError("DATA composer pin must be exactly the 19 reviewed files")
        diff = git("diff", "--name-only", "--no-renames",
                   cls.review["base_sha"], cls.source_sha).decode().splitlines()
        if sorted(diff) != paths:
            raise AssertionError("reviewed producer path allowlist drift")
        cls.blobs = []
        for item in files:
            path = item["path"]
            raw = git("show", cls.source_sha + ":" + path)
            tree = git("ls-tree", cls.source_sha, "--", path).decode().strip()
            if not tree.startswith(item["mode"] + " blob " + item["git_blob_sha1"] + "\t"):
                raise AssertionError("source git object mode or SHA mismatch:" + path)
            if blob_hash(raw) != item["git_blob_sha1"]:
                raise AssertionError("Git object bytes are corrupted:" + path)
            if (ROOT / path).read_bytes() != raw:
                raise AssertionError("current DATA branch rewrote reviewed source:" + path)
            mode = int(item["mode"][-3:], 8)
            cls.blobs.append(module.ReviewedBlob(
                path=path, data=raw, sha256=module.hash256(raw),
                git_blob_sha1=item["git_blob_sha1"], mode=mode,
            ))
        # The test dynamically constructs a self-attested prototype manifest.
        # Its digest is NOT a signer/externally reviewed immutable pin.
        cls.source_pins = {"DATA": cls.source_sha}
        cls.manifest = module.canonical({
            "schema": module.SCHEMA,
            "source_pins": cls.source_pins,
            "files": [
                dict(path=b.path, sha256=b.sha256,
                     git_blob_sha1=b.git_blob_sha1, mode=b.mode)
                for b in cls.blobs
            ],
        })
        cls.manifest_sha = module.hash256(cls.manifest)

    def test_001_to_008_checksum_order_and_immutable_unknown(self):
        self.assertEqual(self.review["migration_order"], list(range(1, 9)))
        migrations = ROOT / "src/storage/migrations"
        files = sorted(migrations.glob("[0-9]*_*.sql"))
        self.assertEqual([int(p.name.split("_", 1)[0]) for p in files], list(range(1, 9)))
        target = migrations / "008_public_unknown_taker_side.sql"
        source = git("show", self.source_sha + ":src/storage/migrations/008_public_unknown_taker_side.sql")
        self.assertEqual(target.read_bytes(), source)
        self.assertEqual(blob_hash(source), self.review["migration_008_blob"])
        sql = source.decode()
        self.assertEqual(sql.count("IN ('Buy','Sell','Unknown')"), 2)
        self.assertIn("price > 0 AND size > 0", sql)
        self.assertIn("trade_id IS NOT NULL", sql)
        self.assertIn("DROP CONSTRAINT raw_records_check2", sql)
        self.assertIn("DROP CONSTRAINT trade_side_required", sql)
        checksum = hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest()
        self.assertEqual(len(checksum), 64)

    def test_private_publish_reopen_pin_and_checkout_not_modified(self):
        m = self.prototype
        baseline = {b.path: blob_hash((ROOT / b.path).read_bytes()) for b in self.blobs}
        with tempfile.TemporaryDirectory(prefix="data72-private-generation-") as td:
            root = Path(td) / "private"
            root.mkdir(mode=0o700)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertFalse(str(root).startswith(str(ROOT) + os.sep))
            kwargs = dict(blobs=self.blobs, manifest=self.manifest,
                          manifest_sha256=self.manifest_sha,
                          source_pins=self.source_pins)
            generation_a = m.publish_generation(root, **kwargs)
            self.assertEqual(m.read_current(root, manifest=self.manifest,
                                             manifest_sha256=self.manifest_sha), generation_a)
            first = root / "generations" / generation_a
            self.assertEqual((first / "src/storage/migrations/008_public_unknown_taker_side.sql").read_bytes(),
                             git("show", self.source_sha + ":src/storage/migrations/008_public_unknown_taker_side.sql"))
            generation_b = m.publish_generation(root, **kwargs)
            self.assertNotEqual(generation_a, generation_b)
            self.assertEqual(m.read_current(root, manifest=self.manifest,
                                             manifest_sha256=self.manifest_sha), generation_b)
            self.assertEqual(m.read_pinned(root, generation_a, manifest=self.manifest,
                                            manifest_sha256=self.manifest_sha), generation_a)
            with self.assertRaises(m.CompositionRefused):
                m.retire_generation(root, generation_a)
            self.assertTrue(first.is_dir())
            def before_current(phase, _path):
                if phase == "after_publish_before_current":
                    raise RuntimeError("injected_before_current_swap")
            with self.assertRaisesRegex(RuntimeError, "injected_before_current_swap"):
                m.publish_generation(root, **kwargs, fault=before_current)
            self.assertEqual(m.read_current(root, manifest=self.manifest,
                                             manifest_sha256=self.manifest_sha), generation_b)
            # Corrupt detached, private generation only. The checkout is untouched.
            damaged = first / "src/ingest/execution_contract.py"
            damaged.write_bytes(damaged.read_bytes() + b"\n# deliberate test-only corruption")
            with self.assertRaises(m.CompositionRefused):
                m.read_pinned(root, generation_a, manifest=self.manifest,
                               manifest_sha256=self.manifest_sha)
            self.assertEqual(m.read_current(root, manifest=self.manifest,
                                             manifest_sha256=self.manifest_sha), generation_b)
        self.assertEqual(
            {b.path: blob_hash((ROOT / b.path).read_bytes()) for b in self.blobs},
            baseline,
        )

    def test_original_sha_decimal_provenance_source_contract_not_modified(self):
        code = (ROOT / "src/ingest/execution_contract.py").read_text(encoding="utf-8")
        self.assertIn("tester-public-execution/1", code)
        for expected in ("http_sha256", "page_sha256", "row_sha256",
                         "exec_id", "publication_ms", "observed_ms",
                         "event_ms", "Unknown", "Decimal"):
            self.assertIn(expected, code)
        self.assertEqual(
            blob_hash((ROOT / "tests/platform/test_research_postgres.py").read_bytes()),
            self.review["shared_pg_hostaddr_blob"],
        )
        # Exact semantic proofs are run using real isolated PG14 and PG18,
        # not claimed by this source-only static check.

    def test_no_database_or_real_launcher_admission(self):
        for key in ("TESTER_AUX_TEST_DSN", "TESTER_TEST_ADMIN_DSN",
                    "TEST_DATABASE_URL", "TEST_RESEARCH_DATABASE_URL",
                    "TESTER_AUX_LIVE_BYBIT"):
            self.assertFalse(os.environ.get(key), key)
        self.assertEqual(self.review["release_status"], "BLOCKED")
        self.assertIn("Existing PostgreSQL 5432", self.review["prohibited"])
        self.assertIn("Live Bybit API", self.review["prohibited"])


if __name__ == "__main__":
    unittest.main()
