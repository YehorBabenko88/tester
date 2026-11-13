"""No live PC4 or PostgreSQL access: pure native admission and crash refusal."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from types import SimpleNamespace
import json
import unittest

from scripts.ops_native_admission import NativeAdmission, NativePin
from scripts.ops_native_windows_witness import NativeRestartBlocked


class NativeAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.pin = NativePin("a"*40, "b"*64, str(self.root), "pg-system-123", "pg-birth-2026")
        self.admission = NativeAdmission(self.root, self.pin)
        self.pg = lambda: {"pg_system_identifier": "pg-system-123",
                           "pg_postmaster_start": "pg-birth-2026"}
        self.lease = lambda: {"job1": "queued", "job2": "completed"}
        self.exited = lambda generation: True
        self.witness = SimpleNamespace(
            generation=0, root=self.root,
            items=((SimpleNamespace(role="api",config_sha256="b"*64),object()),
                   (SimpleNamespace(role="worker",config_sha256="b"*64),object())),
            require_restart_allowed=lambda receipt: None)
        self.receipt = {"schema":"ops-native-win-exit/1","generation":0}


    def reserve(self, generation=0, **kwargs):
        return self.admission.reserve(
            generation,
            previous_exit_validator=kwargs.get("exit", self.exited),
            pg_probe=kwargs.get("pg", self.pg),
            leases_probe=kwargs.get("lease", self.lease),
        )

    def test_first_reservation_and_strict_duplicate_refusal(self):
        folder = self.reserve()
        self.assertTrue((folder/"reservation.json").is_file())
        self.assertFalse((folder/"closure.json").exists())
        with self.assertRaises(NativeRestartBlocked):
            self.reserve()

    def test_controller_crash_refuses_partial_record_without_adoption(self):
        self.reserve()
        other = NativeAdmission(self.root, self.pin)
        with self.assertRaises(NativeRestartBlocked):
            other.reserve(1, previous_exit_validator=lambda g: True,
                          pg_probe=self.pg, leases_probe=self.lease)

    def test_sealed_generation_requires_external_original_handle(self):
        self.reserve()
        self.admission.seal_reservation(
            0, witness=self.witness, receipt=self.receipt,
            pg_probe=self.pg, leases_probe=self.lease)
        with self.assertRaises(NativeRestartBlocked):
            self.reserve(1, exit=lambda g: False)
        folder = self.reserve(1)
        self.assertEqual(folder.name, "0001")

    def test_forged_closure_cannot_override_live_old_process(self):
        self.reserve()
        self.admission.seal_reservation(
            0, witness=self.witness, receipt=self.receipt,
            pg_probe=self.pg, leases_probe=self.lease)
        new = NativeAdmission(self.root, self.pin)
        validator = Mock(return_value=False)
        with self.assertRaises(NativeRestartBlocked):
            new.reserve(1, previous_exit_validator=validator,
                        pg_probe=self.pg, leases_probe=self.lease)
        validator.assert_called()

    def test_pg_incarnation_mismatch_blocks_before_reservation(self):
        with self.assertRaises(NativeRestartBlocked):
            self.reserve(pg=lambda: {"pg_system_identifier": "foreign",
                                    "pg_postmaster_start": "pg-birth-2026"})
        self.assertFalse((self.admission.generations/"0000").exists())

    def test_running_lease_refused_even_with_matching_pg(self):
        with self.assertRaises(NativeRestartBlocked):
            self.reserve(lease=lambda: {"job1": "running"})
        self.assertFalse((self.admission.generations/"0000").exists())

    def test_tampered_reservation_blocks_future_generation(self):
        self.reserve()
        self.admission.seal_reservation(
            0, witness=self.witness, receipt=self.receipt,
            pg_probe=self.pg, leases_probe=self.lease)
        path = self.admission.generations/"0000"/"reservation.json"
        data = json.loads(path.read_text())
        data["pin"]["source_sha"] = "f"*40
        path.write_text(json.dumps(data))
        with self.assertRaises(NativeRestartBlocked):
            self.reserve(1)
        self.assertFalse((self.admission.generations/"0001").exists())

    def test_unknown_generation_entry_refused(self):
        (self.admission.generations/"unexpected.txt").write_text("foreign")
        with self.assertRaises(NativeRestartBlocked):
            self.reserve()

    def test_rollback_record_does_not_permit_successor(self):
        folder = self.reserve()
        (folder/"rollback.json").write_text('{"status":"SEALED_PARTIAL"}')
        with self.assertRaises(NativeRestartBlocked):
            self.reserve(1)

    def test_witness_refusal_blocks_native_closure(self):
        self.reserve()
        self.witness.require_restart_allowed = lambda receipt: (_ for _ in ()).throw(
            NativeRestartBlocked("live old original handle"))
        with self.assertRaises(NativeRestartBlocked):
            self.admission.seal_reservation(
                0, witness=self.witness, receipt=self.receipt,
                pg_probe=self.pg, leases_probe=self.lease)
        self.assertFalse((self.admission.generations/"0000"/"closure.json").exists())

    def test_no_successor_without_lease_fence(self):
        self.reserve()
        with self.assertRaises(NativeRestartBlocked):
            self.admission.seal_reservation(
                0, witness=self.witness, receipt=self.receipt,
                pg_probe=self.pg, leases_probe=lambda: {"running": "running"})
        self.assertFalse((self.admission.generations/"0000"/"closure.json").exists())


if __name__ == "__main__":
    unittest.main()
