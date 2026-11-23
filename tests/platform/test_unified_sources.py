"""Reject source pin or byte drift in the assembled candidate."""

import json
from pathlib import Path

import pytest

from scripts.verify_unified_sources import verify

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/guardians/unified-candidate-sources.json"


def test_pinned_assembly_matches_ancestors_and_bytes():
    assert verify(ROOT, json.loads(MANIFEST.read_text())) == 16


def test_owner_hash_tamper_rejected():
    manifest = json.loads(MANIFEST.read_text())
    name = next(iter(manifest["owner_files"]["code"]))
    manifest["owner_files"]["code"][name] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        verify(ROOT, manifest)


def test_missing_source_and_unsafe_path_rejected():
    manifest = json.loads(MANIFEST.read_text())
    manifest["sources"].pop("exchange")
    with pytest.raises(ValueError, match="sources"):
        verify(ROOT, manifest)
    manifest = json.loads(MANIFEST.read_text())
    manifest["owner_files"]["code"]["../foreign"] = "0" * 64
    with pytest.raises(ValueError, match="owner paths"):
        verify(ROOT, manifest)
