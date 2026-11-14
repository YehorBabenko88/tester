"""OPS #73 compatibility with immutable #77 producer tree; no processes or DB."""
import os
from pathlib import Path
import unittest

from scripts.ops_candidate_compatibility import (
    SCHEMA, require_installed_native_activation, review_candidate_sources,
)


def fixture():
    return {
        "pc4": "def serve():\n    return 'pc4-shutdown.request'\n",
        "api": "def create_app():\n    return '/ok'\ndef run():\n    return 'stop.request'\n",
        "worker": "def process_one():\n    return 1\ndef run():\n    return 'stop.request'\n",
        "data": "VERSION = 'tester-public-execution/1'\nMAX_ROWS = 100\ndef read_execution_events(*, category, symbol, start_ms, end_ms, limit=100):\n    return None\ndef read_research_trades():\n    return None\n",
        "research": "DATA_CONTRACT_VERSION = 'tester-public-execution/1'\nMAX_EVENTS = 100\ndef read_data_metrics(*, category, symbol, start_ms, end_ms, limit=100):\n    return 'UNKNOWN'\ndef require_strategy_lab_f_input():\n    raise ValueError('UNKNOWN')\n",
    }


UI = "def create_app():\n    return '/api/capabilities'\ndef run():\n    return None\n"


class CandidateCompatibilityTests(unittest.TestCase):
    def test_read_only_expected_interfaces_never_authorize_native(self):
        review = review_candidate_sources(fixture(), ui_api_source=UI)
        self.assertEqual(review["schema"], SCHEMA)
        self.assertEqual(review["installed_native_activation"], "BLOCKED")
        self.assertFalse(review["default_activation_enabled"])
        self.assertFalse(review["pc4_restart_authorized"])
        self.assertFalse(review["pg5432_access"])
        self.assertFalse(review["runtime_source_attested_by_ci"])
        for name in ("pc4", "api", "worker", "data", "research"):
            self.assertTrue(review["interfaces"][name]["required_functions_present"])
        self.assertTrue(review["ui_api"]["capabilities_route"])
        self.assertNotIn("data_research_execution_contract_version_mismatch", review["blockers"])
        self.assertNotIn("data_research_bounded_page_limit_mismatch", review["blockers"])
        self.assertNotIn("research_read_interface_signature_mismatch", review["blockers"])
        self.assertIn("persistent_external_original_handle_guardian_not_implemented", review["blockers"])
        self.assertIn("api_generation_scoped_stop_not_implemented", review["blockers"])
        self.assertIn("worker_generation_scoped_stop_not_implemented", review["blockers"])
        with self.assertRaisesRegex(RuntimeError, "physical_acceptance"):
            require_installed_native_activation(review)

    def test_deterministic_bounded_manifest_and_input_integrity(self):
        first = review_candidate_sources(fixture(), ui_api_source=UI)
        source = fixture()
        source["api"] += "\nREVISED = 'irrelevant-change'\n"
        second = review_candidate_sources(source, ui_api_source=UI)
        self.assertNotEqual(first["manifest_sha256"], second["manifest_sha256"])
        self.assertEqual(first["manifest_sha256"],
                         review_candidate_sources(dict(reversed(list(fixture().items()))),
                                                  ui_api_source=UI)["manifest_sha256"])
        self.assertEqual(len(first["manifest_sha256"]), 64)
        self.assertEqual(second["installed_native_activation"], "BLOCKED")

    def test_version_and_kw_signature_skew_is_reported_but_never_autofixed(self):
        source = fixture()
        source["research"] = source["research"].replace(
            "'tester-public-execution/1'", "'tester-public-execution/2'"
        ).replace("MAX_EVENTS = 100", "MAX_EVENTS = 1000")
        report = review_candidate_sources(source, ui_api_source=UI)
        self.assertIn("data_research_execution_contract_version_mismatch", report["blockers"])
        self.assertIn("data_research_bounded_page_limit_mismatch", report["blockers"])
        self.assertFalse(report["pc4_restart_authorized"])
        source = fixture()
        source["data"] = source["data"].replace("end_ms, limit=100", "limit=100")
        report = review_candidate_sources(source, ui_api_source=UI)
        self.assertIn("data_read_interface_signature_mismatch", report["blockers"])
        with self.assertRaises(RuntimeError):
            require_installed_native_activation(report)

    def test_missing_or_forged_contract_is_never_enabled(self):
        sources = fixture()
        sources["worker"] = "def run():\n    pass\n"
        report = review_candidate_sources(sources, ui_api_source=UI)
        self.assertIn("missing_expected_worker_interface", report["blockers"])
        self.assertEqual(report["installed_native_activation"], "BLOCKED")
        with self.assertRaisesRegex(ValueError, "candidate_source_scope_incomplete"):
            review_candidate_sources({"api": sources["api"]}, ui_api_source=UI)
        with self.assertRaisesRegex(ValueError, "invalid_candidate_python"):
            review_candidate_sources({**fixture(), "api": "def invalid("}, ui_api_source=UI)
        with self.assertRaisesRegex(ValueError, "missing_or_oversized"):
            review_candidate_sources({**fixture(), "api": ""}, ui_api_source=UI)
        with self.assertRaisesRegex(ValueError, "invalid_candidate_contract_report"):
            require_installed_native_activation({"schema": "alien"})

    def test_scoped_markers_are_not_a_credible_external_exit_witness(self):
        sources = fixture()
        extra = "\n".join(sorted((
            "OPS_GENERATION", "OPS_OWNER_MARKER", "OPS_CONFIG_SHA256",
            "OPS_STOP_PATH", "OPS_CHECKPOINT_PATH"
        )))
        for role in ("pc4", "api", "worker"):
            sources[role] += "\nNAMES = " + repr(extra) + "\n"
        report = review_candidate_sources(sources, ui_api_source=UI)
        self.assertNotIn("api_generation_scoped_stop_not_implemented", report["blockers"])
        self.assertFalse(report["pc4_restart_authorized"])
        with self.assertRaises(RuntimeError):
            require_installed_native_activation(report)

    @unittest.skipUnless(
        all(os.environ.get(key) for key in (
            "OPS_CANDIDATE_DATA_ROOT", "OPS_CANDIDATE_RESEARCH_ROOT",
            "OPS_CANDIDATE_UI_ROOT"
        )),
        "NOT_RUN: exact pinned #77 producer checkouts unavailable",
    )
    def test_real_pinned_producer_sources_are_explicitly_not_installed_ready(self):
        root = Path(__file__).parents[2]
        paths = {
            "pc4": root / "src/integration/pc4_runtime.py",
            "api": root / "src/api/main.py",
            "worker": root / "src/api/worker.py",
            "data": Path(os.environ["OPS_CANDIDATE_DATA_ROOT"]) / "src/ingest/execution_contract.py",
            "research": Path(os.environ["OPS_CANDIDATE_RESEARCH_ROOT"]) / "src/integration/data_research_adapter.py",
        }
        ui = Path(os.environ["OPS_CANDIDATE_UI_ROOT"]) / "src/api/main.py"
        report = review_candidate_sources(
            {role: path.read_text(encoding="utf-8") for role, path in paths.items()},
            ui_api_source=ui.read_text(encoding="utf-8"),
        )
        self.assertEqual(report["installed_native_activation"], "BLOCKED")
        self.assertTrue(report["interfaces"]["data"]["required_functions_present"])
        self.assertTrue(report["interfaces"]["research"]["required_functions_present"])
        self.assertTrue(report["ui_api"]["capabilities_route"])
        self.assertIn("api_generation_scoped_stop_not_implemented", report["blockers"])
        self.assertIn("worker_generation_scoped_stop_not_implemented", report["blockers"])
        self.assertIn("pc4_generation_scoped_stop_not_implemented", report["blockers"])
        with self.assertRaises(RuntimeError):
            require_installed_native_activation(report)


if __name__ == "__main__":
    unittest.main()
