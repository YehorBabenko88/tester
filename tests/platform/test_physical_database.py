"""Pure preparation guards. Never starts PostgreSQL or accesses physical root."""

import importlib.util
import unittest
from pathlib import Path

PATH = Path(__file__).parents[2] / "scripts/windows/prepare_physical_database.py"


class PhysicalDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("physical_database_preparation", PATH)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def owner(self):
        return {"installation_id": "11111111-1111-4111-8111-111111111111"}

    def test_state_requires_exact_installation_and_completed_phase(self):
        m = self.module
        state = m.identity(self.owner()) | {"phase": "ready", "system_identifier": "123"}
        self.assertEqual(m.validate_state(state, self.owner())["phase"], "ready")
        for changed in (
            {"phase": "initializing"},
            {"installation_id": "22222222-2222-4222-8222-222222222222"},
            {"database": "postgres"},
            {"port": 5432},
            {"system_identifier": ""},
        ):
            with self.subTest(changed=changed), self.assertRaises(m.ScopeViolation):
                m.validate_state(state | changed, self.owner())

    def test_secrets_are_identity_bound_and_never_replaced(self):
        m = self.module
        value = m.identity(self.owner()) | {"admin_password": "a" * 64, "app_password": "b" * 64}
        self.assertIs(m.validate_secrets(value, self.owner()), value)
        for changed in (
            {"installation_id": "foreign"},
            {"admin_password": "short"},
            {"app_password": "a" * 64},
        ):
            with self.subTest(changed=changed), self.assertRaises(m.ScopeViolation):
                m.validate_secrets(value | changed, self.owner())

    def test_data_directory_must_be_exact_owned_cluster(self):
        m = self.module
        self.assertTrue(m.same_directory(m.DATA, str(m.DATA)))
        self.assertFalse(m.same_directory(m.DATA, str(m.DATA.parent / "other")))

    def test_plan_is_default_and_prepare_is_explicit(self):
        m = self.module
        self.assertFalse(m.parser().parse_args([]).prepare)
        self.assertTrue(m.parser().parse_args(["--prepare"]).prepare)
        self.assertEqual(m.PORT, 55469)
        self.assertEqual(m.DATABASE, "tester_physical_20261107")

    def test_default_entrypoint_only_plans_without_mutation(self):
        from unittest.mock import patch

        m = self.module
        with (
            patch.object(m, "preflight", return_value=(self.owner(), None)),
            patch.object(m, "prepare") as prepare,
            patch.object(m, "command") as command,
            patch("builtins.print") as printed,
        ):
            self.assertEqual(m.main([]), 0)
            prepare.assert_not_called()
            command.assert_not_called()
            self.assertIn("READ_ONLY_PLAN", printed.call_args.args[0])

    def test_foreign_owner_is_rejected_before_secrets_or_server_access(self):
        from unittest.mock import patch

        m = self.module
        with (
            patch.object(m, "validate_owner", side_effect=m.ScopeViolation("foreign")),
            patch.object(m, "read_json") as read,
            patch.object(m, "command") as command,
        ):
            self.assertRaises(m.ScopeViolation, m.preflight)
            read.assert_not_called()
            command.assert_not_called()

    def test_subprocess_errors_do_not_expose_captured_credentials(self):
        import subprocess
        from unittest.mock import patch

        m = self.module
        with patch.object(
            m.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, "secret password", "secret DSN"),
        ):
            with self.assertRaises(m.ScopeViolation) as raised:
                m.command(["fixture"])
        self.assertNotIn("secret", str(raised.exception))

    def test_pg_ctl_has_no_inheritable_output_pipes(self):
        import subprocess
        from unittest.mock import patch

        m = self.module
        for action in ("start", "stop"):
            with patch.object(
                m.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
            ) as run:
                m.command([m.PGBIN / "pg_ctl.exe", "-D", m.DATA, "-w", action])
            self.assertFalse(run.call_args.kwargs.get("capture_output", False))
            self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
            self.assertLessEqual(run.call_args.kwargs["timeout"], 180)

    def test_initialized_resume_is_explicit_and_only_pre_ddl_stages(self):
        m = self.module
        for label in ("start_owned_cluster", "verify_cluster_identity"):
            value = m.identity(self.owner()) | dict(phase="preparing", stage=label)
            self.assertRaises(m.ScopeViolation, m.validate_state, value, self.owner())
            self.assertIs(m.validate_state(value, self.owner(), allow_initialized=True), value)
        for label in (
            "initialize_new_cluster",
            "create_owned_application_objects",
            "apply_storage_and_platform_migrations",
        ):
            value = m.identity(self.owner()) | dict(phase="preparing", stage=label)
            with self.assertRaises(m.ScopeViolation):
                m.validate_state(value, self.owner(), allow_initialized=True)

    def test_initialized_configuration_requires_exact_canonical_bytes(self):
        m = self.module
        documents = m.configuration_documents()
        self.assertTrue(m.validate_initialized_documents(documents))
        changed = dict(documents)
        changed["pg_hba.conf"] += b"host all all 0.0.0.0/0 trust\n"
        self.assertRaises(m.ScopeViolation, m.validate_initialized_documents, changed)
