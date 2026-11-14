"""Regression for exact birth-scoped stop request; no installed runtime."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.ops_native_scoped_stop import validate_scoped_stop_request
from scripts.ops_native_windows_witness import (
    NativeBirth,
    NativeRestartBlocked,
    request_native_scoped_stop,
)


class ScopedStopConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.birth = NativeBirth(
            role="api", generation=0, pid=99901, created=12345.0,
            image="C:/CI/python.exe", cwd=str(self.root),
            argv=("C:/CI/python.exe", "-c", "disposable-only"),
            username="ci-runner", owner_marker="scoped-owner-token-0123456789",
            config_sha256="a" * 64,
        )

    def test_only_birth_scoped_stop_request_is_accepted(self):
        self.assertFalse(validate_scoped_stop_request(self.root, self.birth))
        path = request_native_scoped_stop(self.root, self.birth)
        self.assertTrue(validate_scoped_stop_request(self.root, self.birth))
        self.assertEqual(path.name, "native-0000-api.stop.request")
        self.assertFalse(validate_scoped_stop_request(
            self.root, replace(self.birth, role="worker")
        ))
        with self.assertRaises(NativeRestartBlocked):
            validate_scoped_stop_request(
                self.root, replace(self.birth, owner_marker="foreign-owner-0123456789")
            )
        with self.assertRaises(NativeRestartBlocked):
            request_native_scoped_stop(self.root, self.birth)

    def test_corruption_fails_closed_including_original_birth_and_generation(self):
        path = request_native_scoped_stop(self.root, self.birth)
        original = json.loads(path.read_text())
        mutations = (
            ("pid", self.birth.pid + 1),
            ("birth_sha256", "0" * 64),
            ("config_sha256", "1" * 64),
            ("generation", 1),
            ("role", "worker"),
            ("schema", "unreviewed"),
        )
        for field, replacement in mutations:
            bad = dict(original)
            bad[field] = replacement
            path.write_text(json.dumps(bad))
            with self.subTest(field=field):
                with self.assertRaises(NativeRestartBlocked):
                    validate_scoped_stop_request(self.root, self.birth)
        path.write_text(json.dumps({**original, "approve_restart": True}))
        with self.assertRaises(NativeRestartBlocked):
            validate_scoped_stop_request(self.root, self.birth)
        path.write_text("{this-is-not-json")
        with self.assertRaises(NativeRestartBlocked):
            validate_scoped_stop_request(self.root, self.birth)

    def test_symlink_and_oversized_stop_requests_are_refused(self):
        path = self.root / "native-0000-api.stop.request"
        target = self.root / "outsider.json"
        target.write_text("{}")
        path.symlink_to(target)
        with self.assertRaises(NativeRestartBlocked):
            validate_scoped_stop_request(self.root, self.birth)
        path.unlink()
        path.write_text(" " * 4097)
        with self.assertRaises(NativeRestartBlocked):
            validate_scoped_stop_request(self.root, self.birth)

    def test_invalid_identity_cannot_create_permission(self):
        for birth in (
            replace(self.birth, generation=-1),
            replace(self.birth, pid=0),
            replace(self.birth, role="other"),
        ):
            with self.subTest(birth=birth), self.assertRaises(NativeRestartBlocked):
                validate_scoped_stop_request(self.root, birth)
        with self.assertRaises(NativeRestartBlocked):
            validate_scoped_stop_request(self.root / "nonexistent", self.birth)


if __name__ == "__main__":
    unittest.main()
