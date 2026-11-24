"""Release changes retain immutable source provenance and explicit regressions."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.verify_unified_sources import _validate_overrides, load_release_overrides, verify

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/guardians/unified-candidate-sources.json"
FILE = "src/api/guardians/exchange_guardian.py"


@pytest.mark.parametrize(
    "name",
    [
        "src/api/guardians/code_guardian.py",
        "tests/platform/test_code_guardian.py",
        "src/api/guardians/data_guardian.py",
        "tests/platform/test_data_guardian.py",
    ],
)
def test_release_override_rejects_unapproved_code_and_data_paths(name):
    manifest = json.loads(MANIFEST.read_text())
    role = "code" if "code_guardian" in name else "data"
    override = {
        name: {
            "baseline_sha256": manifest["owner_files"][role][name],
            "candidate_sha256": "a" * 64,
            "reason": "An explanation cannot authorize an unapproved source override",
            "regression_tests": ["tests/platform/test_release_source_overrides.py"],
        }
    }
    with pytest.raises(ValueError, match="approved"):
        _validate_overrides(ROOT, override, manifest["owner_files"])


def entry(manifest, candidate):
    result = load_release_overrides(ROOT)
    result[FILE] = {
        "baseline_sha256": manifest["owner_files"]["exchange"][FILE],
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "reason": "Share strict public endpoint policy with the sole transport",
        "regression_tests": ["tests/platform/test_release_source_overrides.py"],
    }
    return result


def test_declared_override_checks_baseline_and_candidate_bytes(monkeypatch):
    manifest = json.loads(MANIFEST.read_text())
    old = (ROOT / FILE).read_bytes()
    changed = old.replace(b"\r\n", b"\n") + b"\n# intentional release regression\n"
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda p: changed if p == ROOT / FILE else original(p))
    assert verify(ROOT, manifest, overrides=entry(manifest, changed)) == 16
    with pytest.raises(ValueError, match="digest"):
        verify(ROOT, manifest, overrides={})


def test_override_cannot_hide_modified_baseline_pin():
    manifest = json.loads(MANIFEST.read_text())
    overrides = entry(manifest, (ROOT / FILE).read_bytes().replace(b"\r\n", b"\n"))
    overrides[FILE]["baseline_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="baseline"):
        verify(ROOT, manifest, overrides=overrides)


def test_override_requires_real_regression_and_owned_path():
    manifest = json.loads(MANIFEST.read_text())
    overrides = entry(manifest, b"changed")
    overrides[FILE]["regression_tests"] = []
    with pytest.raises(ValueError, match="regression"):
        verify(ROOT, manifest, overrides=overrides)
    with pytest.raises(ValueError, match="owner"):
        verify(ROOT, manifest, overrides={"../foreign": overrides[FILE]})


def test_release_override_document_is_versioned(tmp_path):
    target = tmp_path / "docs/release/source-overrides.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"schema": "unknown", "files": {}}))
    with pytest.raises(ValueError, match="schema"):
        load_release_overrides(tmp_path)
