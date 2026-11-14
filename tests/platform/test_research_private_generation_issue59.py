"""Pure negative trust-boundary tests for research private generation handoff."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.research_private_generation_issue59 import (
    LEDGER_SCHEMA,
    MOD_MAP,
    _git_bytes_for_ledger,
    _sha1,
    load_source_ledger,
)

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "docs/research_private_generation_source_ledger.json"
EXPECTED_BLOB = "1447406f01ff1c200059a89a7556dd404be3f9be"


def test_exact_external_source_ledger_git_blob_is_pinned():
    doc = load_source_ledger(LEDGER, expected_git_sha1=EXPECTED_BLOB)
    assert doc["schema"] == LEDGER_SCHEMA
    assert len(doc["files"]) == 23
    assert {x["source"] for x in doc["files"]} == {"data", "research", "ui"}
    assert doc["coverage"] == "UNKNOWN"
    assert doc["f_admission"] == "DENIED_UNKNOWN_COVERAGE"
    assert doc["native_launcher"] == "NOT_ACTIVATED"
    assert set(MOD_MAP).issubset({x["path"] for x in doc["files"]})
    assert all(x["mode"] == 0o644 for x in doc["files"])
    assert doc["source_pins"]["research"] == "3966e5465b0b7881da4d23883ec82157fcd54aa1"


def test_ledger_byte_mutation_and_symlink_are_rejected(tmp_path):
    source = LEDGER.read_bytes()
    changed = tmp_path / "ledger.json"
    changed.write_bytes(source.replace(b'"coverage": "UNKNOWN"', b'"coverage": "COMPLETE"'))
    with pytest.raises(ValueError, match="source_ledger_git_blob_mismatch"):
        load_source_ledger(changed, expected_git_sha1=EXPECTED_BLOB)
    changed.write_bytes(source)
    with pytest.raises(ValueError, match="unsafe_or_unpinned_source_ledger"):
        load_source_ledger(changed, expected_git_sha1="0" * 39)
    alias = tmp_path / "alias.json"
    alias.symlink_to(changed)
    with pytest.raises(ValueError, match="unsafe_or_unpinned_source_ledger"):
        load_source_ledger(alias, expected_git_sha1=EXPECTED_BLOB)


def test_duplicate_path_or_revision_forgery_rejected(tmp_path):
    doc = json.loads(LEDGER.read_text())
    for mutation in ("duplicate", "unverified_source", "fake_f", "fake_mode"):
        altered = copy.deepcopy(doc)
        if mutation == "duplicate":
            altered["files"].append(copy.deepcopy(altered["files"][0]))
        elif mutation == "unverified_source":
            altered["files"][0]["git_blob_sha1"] = "0" * 39
        elif mutation == "fake_f":
            altered["f_admission"] = "GRANTED"
        elif mutation == "fake_mode":
            altered["files"][0]["mode"] = 0o777
        raw = (json.dumps(altered, indent=2) + "\n").encode()
        path = tmp_path / "reviewed.json"
        path.write_bytes(raw)
        with pytest.raises(ValueError):
            load_source_ledger(path, expected_git_sha1=_sha1(raw))


def test_git_mode_and_original_git_content_are_independently_checked():
    content = b"private-research-readonly"
    path = "src/analytics/research_metrics.py"
    good_sha = _sha1(content)
    ledger = {
        "source_pins": {"research": "3" * 40},
        "files": [{
            "source": "research", "path": path,
            "git_blob_sha1": good_sha, "mode": 0o644,
        }],
    }
    with patch("scripts.research_private_generation_issue59._git") as git:
        git.side_effect = [
            f"100644 blob {good_sha}\t{path}\n".encode(), content,
        ]
        reviewed = _git_bytes_for_ledger(ledger)
    assert reviewed[0][1] == content
    for mode in ("120000", "160000", "100755"):
        with patch("scripts.research_private_generation_issue59._git") as git:
            git.return_value = f"{mode} blob {good_sha}\t{path}\n".encode()
            with pytest.raises(ValueError, match="source_git_blob_mode_or_sha_mismatch"):
                _git_bytes_for_ledger(ledger)
    with patch("scripts.research_private_generation_issue59._git") as git:
        git.side_effect = [
            f"100644 blob {good_sha}\t{path}\n".encode(), b"forged-bytes",
        ]
        with pytest.raises(ValueError, match="source_git_blob_bytes_mismatch"):
            _git_bytes_for_ledger(ledger)


def test_private_handoff_cannot_invoke_installed_native_or_claim_full_coverage():
    code = (ROOT / "scripts/research_private_generation_issue59.py").read_text()
    for disallowed in (
        "subprocess.Popen(", "os.system(", "StartService(", "ShellExecute(",
        "sc.exe", "force-kill", "PC4_START", "admitted_to_f = True",
    ):
        assert disallowed not in code
    assert '"NOT_AUTHORIZED"' in code
    assert '"DENIED_UNKNOWN_COVERAGE"' in code
    assert '"NOT_PROVEN"' in code
