"""Fail-closed source identity audit for MASTER's disposable combined tree.

Run from the composed checkout root, not from the DATA test-driver checkout.
This test never connects to a database or performs an HTTP request. The
subsequent PG14/PG18 integration step exercises the actual source readers.
"""

import hashlib
import os
from pathlib import Path
import subprocess
import unittest

BASE = "3b2eef8a4139195db1a775545e83f1c1f58bf403"
CANONICAL_008_GIT_BLOB = "05600c50e3d4598aaa4645ac58e137a719b0eed2"
COMMON_RESEARCH_TEST_GIT_BLOB = "402c25410ba4cc6704911148b1ec0c5bba720088"
DATA_FILES = (
    ".github/workflows/public-auxiliary-data.yml",
    ".github/workflows/storage.yml",
    "docs/coordination/data72-migration008-candidate77.md",
    "docs/ingest/EXECUTION_EVENT_CONTRACT_V1.md",
    "docs/ingest/PUBLIC_AUXILIARY_DATA.md",
    "src/ingest/execution_contract.py",
    "src/ingest/public_auxiliary.py",
    "src/ingest/public_auxiliary_runner.py",
    "src/storage/migrations/008_public_unknown_taker_side.sql",
    "src/storage/models.py",
    "tests/platform/test_research_postgres.py",
    "tests/storage/test_postgres.py",
    "tests/test_composed_data_research_postgres.py",
    "tests/test_execution_contract_bridge.py",
    "tests/test_execution_contract_postgres.py",
    "tests/test_live_public_bybit_postgres.py",
    "tests/test_public_auxiliary.py",
    "tests/test_public_auxiliary_postgres.py",
    "tests/test_public_auxiliary_runner.py",
)


def git(*args):
    return subprocess.check_output(("git", *args), stderr=subprocess.PIPE).decode().strip()


def blob_sha(data):
    import hashlib as _hashlib

    return _hashlib.sha1(
        ("blob " + str(len(data)) + "\0").encode("ascii") + data
    ).hexdigest()


@unittest.skipUnless(os.environ.get("MASTER_COMPOSED_DATA_TEST") == "true",
                     "only valid in disposable combined source CI")
class MasterComposedDataIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path.cwd().resolve()
        cls.data_sha = os.environ["MASTER_DATA_SOURCE_SHA"]
        cls.research_sha = os.environ["MASTER_RESEARCH_SOURCE_SHA"]
        cls.staging_sha = os.environ["MASTER_STAGING_SHA"]
        if os.environ.get("CI") != "true":
            raise AssertionError("disposable GitHub CI required")

    def test_checkout_is_exact_pinned_staging_source(self):
        self.assertEqual(git("rev-parse", "HEAD"), self.staging_sha)
        self.assertNotEqual(self.staging_sha, self.data_sha)
        self.assertNotEqual(self.staging_sha, self.research_sha)
        for sha in (BASE, self.data_sha, self.research_sha):
            self.assertEqual(git("rev-parse", sha + "^{commit}"), sha)

    def test_all_nineteen_data_files_are_copied_byte_for_byte(self):
        rows = git("diff", "--name-only", "--no-renames", BASE, self.data_sha)
        expected = {line for line in rows.splitlines() if line}
        self.assertEqual(expected, set(DATA_FILES))
        for relative in DATA_FILES:
            target = self.root / relative
            self.assertTrue(target.is_file(), relative)
            self.assertTrue(target.resolve().is_relative_to(self.root), relative)
            original = subprocess.check_output(
                ("git", "show", self.data_sha + ":" + relative),
                stderr=subprocess.PIPE,
            )
            self.assertEqual(target.read_bytes(), original, relative)

    def test_canonical_008_and_shared_research_blob_are_identical(self):
        migration = (self.root / "src/storage/migrations/008_public_unknown_taker_side.sql")
        shared = (self.root / "tests/platform/test_research_postgres.py")
        self.assertEqual(blob_sha(migration.read_bytes()), CANONICAL_008_GIT_BLOB)
        self.assertEqual(blob_sha(shared.read_bytes()), COMMON_RESEARCH_TEST_GIT_BLOB)
        upstream = subprocess.check_output(
            ("git", "show", self.research_sha + ":tests/platform/test_research_postgres.py"),
            stderr=subprocess.PIPE,
        )
        self.assertEqual(shared.read_bytes(), upstream)
        self.assertEqual(blob_sha(upstream), COMMON_RESEARCH_TEST_GIT_BLOB)
        sql = migration.read_text(encoding="utf-8")
        self.assertEqual(sql.count("IN ('Buy','Sell','Unknown')"), 2)
        self.assertIn("price > 0 AND size > 0", sql)
        self.assertIn("trade_id IS NOT NULL", sql)
        all_008 = tuple((self.root / "src/storage/migrations").glob("008_*.sql"))
        self.assertEqual(all_008, (migration,))

    def test_migration_checksum_has_not_been_rewritten(self):
        migration = self.root / "src/storage/migrations/008_public_unknown_taker_side.sql"
        original = subprocess.check_output(
            ("git", "show", self.data_sha + ":src/storage/migrations/008_public_unknown_taker_side.sql")
        )
        expected = hashlib.sha256(original.replace(b"\r\n", b"\n")).hexdigest()
        actual = hashlib.sha256(migration.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        self.assertEqual(actual, expected)
        self.assertEqual(blob_sha(original), CANONICAL_008_GIT_BLOB)

    def test_updated_master_composer_rejects_unsafe_source_or_workspace(self):
        # Run the actual MASTER composer with a mocked Git reader and a
        # throwaway filesystem, never against the checked-out source tree.
        import importlib.util
        import tempfile
        from unittest import mock

        source = self.root / "scripts/master_disposable_compose.py"
        spec = importlib.util.spec_from_file_location(
            "data72_master_composer_negative", source
        )
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for kind, relative, mode, reason in (
            ("source_symlink", "src/probe.txt", "120000", "nonregular producer source"),
            ("parent_symlink", "src/probe.txt", "100644", "symlink destination or parent"),
            ("target_symlink", "src/probe.txt", "100644", "symlink destination or parent"),
            ("traversal", "src/../outside.txt", "100644", "unsafe relative path"),
            ("protected", "src/integration/pc4_runtime.py", "100644", "refusing DATA"),
            ("wrong_actions", "src/probe.txt", "100644", "refuse non-Actions workspace"),
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                parent = Path(temp)
                root = parent / "checkout"
                root.mkdir()
                outside = parent / "outside"
                outside.mkdir()
                sentinel = outside / "sentinel"
                sentinel.write_text("unchanged", encoding="utf-8")
                if kind == "parent_symlink":
                    (root / "src").symlink_to(outside, target_is_directory=True)
                elif kind == "target_symlink":
                    (root / "src").mkdir()
                    (root / "src/probe.txt").symlink_to(sentinel)

                def fake_git(*args):
                    if args == ("rev-parse", "HEAD"):
                        return (self.staging_sha + "\n").encode()
                    if args == ("rev-parse", self.data_sha + "^{commit}"):
                        return (self.data_sha + "\n").encode()
                    if args[:3] == ("diff", "--name-status", "--no-renames"):
                        return ("A\t" + relative + "\n").encode()
                    if args[0] == "ls-tree":
                        return (mode + " blob " + "a" * 40 + "\t" + relative + "\n").encode()
                    if args[0] == "show":
                        return b"only-a-mocked-data-source\n"
                    raise AssertionError("unexpected fake Git query: " + repr(args))

                env = {
                    "CI": "true",
                    "GITHUB_ACTIONS": "false" if kind == "wrong_actions" else "true",
                    "GITHUB_WORKSPACE": str(root),
                    "EXPECTED_STAGING_SHA": self.staging_sha,
                }
                with (
                    mock.patch.object(module, "ROOT", root),
                    mock.patch.object(module, "PRODUCERS", (("DATA", self.data_sha),)),
                    mock.patch.object(module, "git", side_effect=fake_git),
                    mock.patch.dict(os.environ, env),
                ):
                    with self.assertRaisesRegex(SystemExit, reason):
                        module.main()
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
                self.assertFalse((outside / "probe.txt").exists())
                if kind != "target_symlink":
                    self.assertFalse((root / "src/probe.txt").exists())


    def test_live_bybit_is_not_enabled_by_disposable_composition(self):
        self.assertNotEqual(os.environ.get("TESTER_AUX_LIVE_BYBIT"), "yes")
        self.assertNotEqual(os.environ.get("TESTER_LIVE_BYBIT"), "yes")
        source = (self.root / "scripts/master_disposable_compose.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('os.environ.get("CI") != "true"', source)
        self.assertIn("EXPECTED_STAGING_SHA", source)
        self.assertIn("source conflict:", source)
        self.assertIn("PROTECTED", source)
        self.assertNotIn("psycopg.connect", source)
        self.assertNotIn("requests.get(", source)


if __name__ == "__main__":
    unittest.main()
