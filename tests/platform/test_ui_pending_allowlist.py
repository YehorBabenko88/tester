"""Fail-closed UI74 review handoff for the two MASTER #77 unapproved paths.

This does not mutate MASTER's enforced allowlist or authorize a publisher.
"""
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "docs/integration/ui74-master77-pending-allowlist-additions.json"
UI_MANIFEST = ROOT / "docs/integration/ui74-master77-manifest.json"
TWO_CI_ONLY_PATHS = {
    ".github/workflows/ui74-private-generation-e2e.yml",
    "scripts/ui74_private_generation_ci.py",
}


def _blob(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def _verify(review: dict, manifest: dict) -> None:
    assert review["schema"] == "tester.ui74-unreviewed-additions/1"
    assert review["status"] == "PENDING_MASTER_APPROVAL"
    assert review["source"]["master_inventory_head"] == (
        "298d610771d962ee7b796eac1834d74138768fac"
    )
    assert review["source"]["ui_source_head"] == (
        "8819b86f329a2958a7989800e802b6a40fa58aa2"
    )
    assert review["source"]["enforced_allowlist_blob"] == (
        "08482cd45a3f46b5b8168aab2b7b19f63acb30fb"
    )
    assert review["source"]["master_review_document_blob"] == (
        "fbb49ef29188b8ddb5531b31578ef40e7987dcbc"
    )
    assert review["counts"]["master_enforced_ui"] == 37
    assert review["counts"]["unreviewed_additions"] == 2
    assert review["counts"]["approved_ui_files_missing"] == 0
    assert review["counts"]["current_ui_changed"] == 39
    items = review["unreviewed"]
    assert len(items) == 2
    assert {item["path"] for item in items} == TWO_CI_ONLY_PATHS
    assert all(item["approved"] is False for item in items)
    assert all(item["publisher_input"] is False for item in items)
    assert all(item["mode"] == "100644" for item in items)
    assert all(item["scope"] == "reviewed_test_harness_only" for item in items)
    assert set(review["pr70_dependencies"]) == {
        "frontend/capabilities.js",
        "frontend/tests/capabilities.test.js",
    }
    assert set(review["pr70_dependencies"]).issubset(manifest["all_changed"])
    assert review["notes"]["include_in_master_approved_allowlist"] is False
    assert review["notes"]["apply_to_installed_runtime"] is False
    assert review["limits"]["historical_coverage"] == "UNKNOWN"
    assert review["limits"]["dataset_revision"] == "UNPINNED"
    assert review["limits"]["real_f_g_e_authorized"] is False
    assert review["limits"]["release"] == "NO_GO"
    assert review["limits"]["contracts_v1_openapi_git_blob"] == (
        "fc3f4b20d4bd6d6fed642f9e00ceb099c68c4398"
    )
    for item in items:
        path = item["path"]
        assert path in manifest["all_changed"]
        assert item["git_blob_sha1"] == manifest["nonself_current_ui_file_blobs"][path]["git_blob_sha1"]
        assert _blob((ROOT / path).read_bytes()) == item["git_blob_sha1"]


def test_pending_ui_additions_exact_blobs_and_non_admission():
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    manifest = json.loads(UI_MANIFEST.read_text(encoding="utf-8"))
    _verify(review, manifest)


@pytest.mark.parametrize("mutation", ["missing", "forged_blob", "approved", "publisher_input"])
def test_pending_ui_additions_fail_closed(mutation):
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    manifest = json.loads(UI_MANIFEST.read_text(encoding="utf-8"))
    if mutation == "missing":
        review["unreviewed"].pop()
    elif mutation == "forged_blob":
        review["unreviewed"][0]["git_blob_sha1"] = "0" * 40
    elif mutation == "approved":
        review["unreviewed"][0]["approved"] = True
    else:
        review["unreviewed"][0]["publisher_input"] = True
    with pytest.raises(AssertionError):
        _verify(review, manifest)
