"""CI-only RESEARCH readback from a privately published OPS generation.

The caller must supply immutable Git commit objects and an independently
reviewed Git-blob ledger. Only Git object READS occur; generation writes stay
inside a 0700 disposable RUNNER_TEMP directory. No launcher, PC4, service,
network exchange, real orders or production PostgreSQL access.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock

SCHEMA = "research-private-generation-acceptance/1"
LEDGER_SCHEMA = "research-private-generation-ledger/1"
OPS_MODULE_BLOB = "1a28603452efaaf9d69e4446b5ee2bf8bf7a5d9e"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
REQUIRED = {"base", "master", "data", "research", "ui", "ops"}
MOD_MAP = {
    "src/storage/models.py": "src.storage.models",
    "src/storage/migrations.py": "src.storage.migrations",
    "src/storage/store.py": "src.storage.store",
    "src/ingest/execution_contract.py": "src.ingest.execution_contract",
    "src/ingest/public_auxiliary.py": "src.ingest.public_auxiliary",
    "src/analytics/research_metrics.py": "src.analytics.research_metrics",
    "src/integration/data_research_adapter.py": "src.integration.data_research_adapter",
    "src/integration/research_ui_projection.py": "src.integration.research_ui_projection",
    "src/integration/research_preview.py": "src.integration.research_preview",
    "tests/test_execution_contract_bridge.py": "tests.test_execution_contract_bridge",
}


def _git(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), timeout=20)


def _sha1(blob: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(blob)).encode("ascii") + b"\x00" + blob
    ).hexdigest()


def load_source_ledger(path: Path, *, expected_git_sha1: str) -> dict:
    if path.is_symlink() or not path.is_file() or not SHA40.fullmatch(expected_git_sha1):
        raise ValueError("unsafe_or_unpinned_source_ledger")
    raw = path.read_bytes()
    if not 0 < len(raw) <= 50_000 or _sha1(raw) != expected_git_sha1:
        raise ValueError("source_ledger_git_blob_mismatch")
    doc = json.loads(raw)
    if doc.get("schema") != LEDGER_SCHEMA or set(doc.get("source_pins", {})) != REQUIRED:
        raise ValueError("source_ledger_missing_pins")
    if any(not SHA40.fullmatch(v) for v in doc["source_pins"].values()):
        raise ValueError("source_ledger_invalid_commit")
    if (doc.get("coverage") != "UNKNOWN"
            or doc.get("f_admission") != "DENIED_UNKNOWN_COVERAGE"
            or doc.get("native_launcher") != "NOT_ACTIVATED"
            or not isinstance(doc.get("files"), list)
            or not 16 <= len(doc["files"]) <= 50):
        raise ValueError("source_ledger_authority_or_scope_invalid")
    paths = set()
    for e in doc["files"]:
        if (not isinstance(e, dict) or set(e) != {"source", "path", "git_blob_sha1", "mode"}
                or e["source"] not in {"data", "research", "ui"}
                or not isinstance(e["path"], str) or not e["path"].startswith(
                    ("src/", "tests/")
                ) or ".." in e["path"].split("/")
                or "\\" in e["path"] or "\x00" in e["path"]
                or not SHA40.fullmatch(e["git_blob_sha1"])
                or e["mode"] != 0o644 or e["path"] in paths):
            raise ValueError("source_ledger_invalid_entry")
        paths.add(e["path"])
    if not set(MOD_MAP).issubset(paths):
        raise ValueError("source_ledger_missing_runtime_modules")
    return doc


def _git_bytes_for_ledger(ledger: dict) -> list[tuple[dict, bytes]]:
    entries = []
    for entry in ledger["files"]:
        sha = ledger["source_pins"][entry["source"]]
        path = entry["path"]
        # Exact Git object path selected by a committed allowlist, never
        # user-supplied shell arguments or a moving branch ref.
        mode_row = _git("ls-tree", sha, "--", path).decode().strip()
        parts = mode_row.split(None, 3)
        if (len(parts) != 4 or parts[0] != "100644" or parts[1] != "blob"
                or parts[2] != entry["git_blob_sha1"] or parts[3] != path):
            raise ValueError("source_git_blob_mode_or_sha_mismatch:" + path)
        raw = _git("show", f"{sha}:{path}")
        if _sha1(raw) != entry["git_blob_sha1"]:
            raise ValueError("source_git_blob_bytes_mismatch:" + path)
        entries.append((entry, raw))
    return entries


def _load_module(name: str, file: Path):
    if not file.is_file() or file.is_symlink():
        raise ValueError("private_module_missing_or_symlink")
    pkg, leaf = name.rsplit(".", 1)
    parent = importlib.import_module(pkg)
    spec = importlib.util.spec_from_file_location(name, file)
    if spec is None or spec.loader is None:
        raise ValueError("private_module_import_unavailable")
    instance = importlib.util.module_from_spec(spec)
    sys.modules[name] = instance
    setattr(parent, leaf, instance)
    spec.loader.exec_module(instance)
    if Path(instance.__file__).resolve() != file.resolve():
        raise AssertionError("module_was_not_loaded_from_private_generation")
    return instance


def _private_execution_assertions(published: Path) -> dict:
    # Dependencies outside these 10 modules are the pinned baseline Python
    # environment; all DATA receipt/projection and RESEARCH math modules
    # being *tested* are loaded by absolute path from the sealed generation.
    # The baseline installs src.* but not tests.* as a Python package.
    # Register a private tests namespace explicitly; never fall back to an
    # unrelated site-packages "tests" distribution or import the fixture
    # from the active checkout by accident.
    import types
    tests_pkg = types.ModuleType("tests")
    tests_pkg.__path__ = [str(published / "tests")]
    tests_pkg.__package__ = "tests"
    sys.modules["tests"] = tests_pkg
    for relative, module_name in MOD_MAP.items():
        _load_module(module_name, published / relative)
    from src.integration import data_research_adapter as adapter
    from src.integration import research_ui_projection as ui
    from tests import test_execution_contract_bridge as bridge

    window = bridge.MS // 60000 * 60000
    scope = {
        "category": "linear", "symbol": "BTCUSDT",
        "start_ms": window, "end_ms": window + 60000,
        "interval_ms": 60000, "tick_size": "0.5", "limit": 10,
    }
    store = Mock()
    store.read.return_value = [bridge.fixture(side="Unknown")]
    result = adapter.read_data_metrics(store, **scope)
    ui_result = ui.project_source_observation(result)
    bar = ui_result["bars"][0]
    assert bar["unknown_side_volume"] == "0.125"
    assert bar["delta_known"] == bar["cvd_known"] == "0"
    assert bar["vwap"] == bar["poc"] == "80000"
    assert bar["footprint"][0]["unknown_side_volume"] == "0.125"
    assert ui_result["source_coverage"] == "UNKNOWN"
    assert ui_result["f_admission"] == "DENIED_UNKNOWN_COVERAGE"
    assert ui_result["g_e_execution"] == "NOT_AUTHORIZED"
    assert ui_result["http_route"] is None
    try:
        adapter.require_strategy_lab_f_input(result)
    except ValueError as exc:
        assert "real_historical_visibility_unknown_f_denied" in str(exc)
    else:
        raise AssertionError("private generation admitted UNKNOWN real F")

    fake_revision = copy.deepcopy(result)
    fake_revision["persisted_dataset_revision"] = "forged-B-revision"
    try:
        ui.project_source_observation(fake_revision)
    except ValueError as exc:
        assert "research_ui_historical_authority_refused" in str(exc)
    else:
        raise AssertionError("private generation trusted a forged B revision")

    corrupted = bridge.fixture(side="Unknown")
    corrupted["provenance"]["observations"][0]["evidence"]["http_sha256"] = "0" * 64
    store.read.return_value = [corrupted]
    try:
        ui.read_verified_ui_observation(store, **scope)
    except ValueError:
        pass
    else:
        raise AssertionError("private generation accepted corrupted HTTP receipt")

    store.read.return_value = [bridge.fixture(side="Unknown")]
    try:
        ui.read_verified_ui_observation(store, **scope, as_of_ms=bridge.MS + 1000)
    except ValueError as exc:
        assert "late_source_knowledge" in str(exc)
    else:
        raise AssertionError("private generation performed lookahead")

    return {
        "source_coverage": "UNKNOWN",
        "historical_known_at_ms": None,
        "f_admission": "DENIED_UNKNOWN_COVERAGE",
        "g_e_execution": "NOT_AUTHORIZED",
        "unknown_side_volume": bar["unknown_side_volume"],
        "vwap": bar["vwap"],
        "poc": bar["poc"],
        "delta_known": bar["delta_known"],
        "cvd_known": bar["cvd_known"],
        "http_tamper_refused": True,
        "fake_dataset_revision_refused": True,
        "lookahead_refused": True,
        "modules_loaded_from_private_generation": len(MOD_MAP),
    }


def run(*, ledger_path: Path, expected_ledger_blob: str, ops_module: Path,
        report_path: Path, run_pytest: bool) -> dict:
    if os.getenv("CI") != "true" or os.getenv("GITHUB_ACTIONS") != "true":
        raise PermissionError("private_generation_ci_only")
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    if Path.cwd().resolve() != workspace or not ops_module.resolve().is_relative_to(workspace):
        raise PermissionError("outside_pinned_ci_workspace")
    runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
    if runner_temp == workspace or runner_temp.is_relative_to(workspace):
        raise PermissionError("private_generation_must_be_outside_checkout")
    ledger = load_source_ledger(ledger_path, expected_git_sha1=expected_ledger_blob)
    for ref in ledger["source_pins"].values():
        if _git("rev-parse", ref + "^{commit}").decode().strip() != ref:
            raise ValueError("missing_exact_producer_commit")
    if not ops_module.is_file() or ops_module.is_symlink() or _sha1(ops_module.read_bytes()) != OPS_MODULE_BLOB:
        raise ValueError("ops_private_prototype_unreviewed")
    spec = importlib.util.spec_from_file_location("reviewed_ops_private_generation", ops_module)
    if spec is None or spec.loader is None:
        raise ValueError("prototype_module_unavailable")
    # The OPS module has no launcher imports and is not invoked at __main__.
    prototype = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = prototype
    spec.loader.exec_module(prototype)
    raw_entries = _git_bytes_for_ledger(ledger)
    blobs = [
        prototype.ReviewedBlob(
            e["path"], raw, hashlib.sha256(raw).hexdigest(),
            e["git_blob_sha1"], e["mode"]
        )
        for e, raw in raw_entries
    ]
    manifest = prototype.canonical({
        "schema": prototype.SCHEMA,
        "source_pins": ledger["source_pins"],
        "files": [
            {"path": b.path, "sha256": b.sha256, "git_blob_sha1": b.git_blob_sha1,
             "mode": b.mode}
            for b in blobs
        ],
    })
    manifest_sha = prototype.hash256(manifest)
    with tempfile.TemporaryDirectory(prefix="research-private-", dir=runner_temp) as dirname:
        root = Path(dirname)
        os.chmod(root, 0o700)
        generation = prototype.publish_generation(
            root, blobs=blobs, manifest=manifest,
            manifest_sha256=manifest_sha, source_pins=ledger["source_pins"],
        )
        assert prototype.read_current(root, manifest=manifest, manifest_sha256=manifest_sha) == generation
        assert prototype.read_pinned(root, generation, manifest=manifest, manifest_sha256=manifest_sha) == generation
        published = root / "generations" / generation
        results = _private_execution_assertions(published)
        if run_pytest:
            import pytest
            tests = [
                str(published / "tests/platform/test_research_metrics_issue59.py"),
                str(published / "tests/platform/test_research_composed_negative_issue59.py"),
            ]
            code = pytest.main(["-q", *tests])
            if code != 0:
                raise AssertionError("private_generation_pytest_failed:" + str(code))
            results["private_pytest"] = "PASS"
        # Independent reader generation remains readable after a second
        # private publication. Neither generation can be garbage-collected.
        next_generation = prototype.publish_generation(
            root, blobs=blobs, manifest=manifest,
            manifest_sha256=manifest_sha, source_pins=ledger["source_pins"],
        )
        assert generation != next_generation
        assert prototype.read_current(root, manifest=manifest,
                                      manifest_sha256=manifest_sha) == next_generation
        assert prototype.read_pinned(root, generation, manifest=manifest,
                                     manifest_sha256=manifest_sha) == generation

        # Test-only malicious same-UID writes to the NEW generation, while
        # the previously pinned original stays untouched. These checks prove
        # detection at read time, NOT same-UID isolation or crash durability.
        next_path = root / "generations" / next_generation
        next_receipt = next_path / "generation.json"
        saved_receipt = next_receipt.read_bytes()
        next_receipt.write_bytes(b'{"status":"FORGED"}')
        try:
            prototype.read_current(root, manifest=manifest, manifest_sha256=manifest_sha)
        except prototype.CompositionRefused:
            pass
        else:
            raise AssertionError("forged_generation_receipt_accepted")
        next_receipt.write_bytes(saved_receipt)

        original = (next_path / "src/ingest/execution_contract.py")
        original_bytes = original.read_bytes()
        original.write_bytes(b"corrupt_private_source")
        try:
            prototype.read_pinned(root, next_generation, manifest=manifest,
                                  manifest_sha256=manifest_sha)
        except prototype.CompositionRefused:
            pass
        else:
            raise AssertionError("corrupt_generation_blob_accepted")
        original.write_bytes(original_bytes)
        current = root / "CURRENT"
        saved_pointer = current.read_bytes()
        current.write_bytes(b'{"generation":"00000000000000000000000000000000"}')
        try:
            prototype.read_current(root, manifest=manifest, manifest_sha256=manifest_sha)
        except prototype.CompositionRefused:
            pass
        else:
            raise AssertionError("forged_current_pointer_accepted")
        current.write_bytes(saved_pointer)

        assert prototype.read_current(root, manifest=manifest,
                                      manifest_sha256=manifest_sha) == next_generation
        assert prototype.read_pinned(root, generation, manifest=manifest,
                                     manifest_sha256=manifest_sha) == generation
        for pinned in (generation, next_generation):
            try:
                prototype.retire_generation(root, pinned)
            except prototype.CompositionRefused as exc:
                assert "gc_disabled_without_reader_leases" in str(exc)
            else:
                raise AssertionError("GC permitted deleting pinned readers")
        receipt_path = published / "generation.json"
        receipt = json.loads(receipt_path.read_bytes())
        assert receipt["tree_sha256"] == json.loads(saved_pointer)["tree_sha256"]
        results.update({
            "reader_pinned_generation": generation,
            "current_generation": next_generation,
            "generation_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            "private_manifest_sha256": manifest_sha,
            "private_tree_sha256": receipt["tree_sha256"],
            "pinning": "reader_remains_pinned_across_current_switch",
            "corrupt_generation_receipt_refused": True,
            "corrupt_generation_file_refused": True,
            "corrupt_current_pointer_refused": True,
            "gc": "BLOCKED_WITHOUT_READER_LEASES",
        })
    report = {
        "schema": SCHEMA,
        "status": "DISPOSABLE_PRIVATE_GENERATION_TEST_PASS",
        "release": "NO_GO",
        "native_pc4_restart": "BLOCKED",
        "checkout_mutation_performed": False,
        "source_ledger_git_blob_sha1": expected_ledger_blob,
        "source_pins": ledger["source_pins"],
        "source_blob_count": len(raw_entries),
        "source_blobs": [
            {"path": e["path"], "role": e["source"],
             "git_blob_sha1": e["git_blob_sha1"],
             "sha256": hashlib.sha256(raw).hexdigest(),
             "mode": e["mode"]}
            for e, raw in raw_entries
        ],
        "results": results,
        "trust_boundary": {
            "external_manifest_signature": "NOT_ATTESTED",
            "source_authority": "REVIEWED_GIT_BLOB_HASHES",
            "crash_durability": "NOT_PROVEN",
            "cross_process_same_uid_writes": "NOT_PROVEN_SAFE",
            "safe_multireader_gc": "NOT_IMPLEMENTED",
            "full_immutable_MASTER_combined_commit_sha": None,
            "pg14_pg18_chromium_physical_acceptance": "NOT_RUN",
        },
    }
    if not report_path.resolve().is_relative_to(runner_temp):
        raise PermissionError("report_must_reside_in_ci_temp")
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--expected-ledger-git-blob", required=True)
    p.add_argument("--ops-module", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--pytest", action="store_true")
    args = p.parse_args(argv)
    report = run(
        ledger_path=args.ledger, expected_ledger_blob=args.expected_ledger_git_blob,
        ops_module=args.ops_module, report_path=args.report,
        run_pytest=args.pytest,
    )
    print("RESEARCH_PRIVATE_GENERATION_PASS", report["source_blob_count"])
    print("F_ADMISSION_DENIED_UNKNOWN_COVERAGE; TRUST_BOUNDARY_NO_GO")


if __name__ == "__main__":
    main()
