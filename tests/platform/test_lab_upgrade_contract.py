"""Platform-independent guards for the app-only C:\\TesterLab updater (issue #110)."""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UPDATER = ROOT / "scripts/windows/Update-TesterLabRelease.ps1"
LAB_LOCK = ROOT / "requirements-windows-lab.lock"

# Exact bytes installed from d6059af and recorded in the lab deploy payload.
# Update-TesterLabRelease.ps1 refuses any upgrade whose lock hash differs, so
# even a comment-only edit (07187ab) blocks every lab upgrade.
INSTALLED_LAB_LOCK_SHA256 = "a0b59bdec87a6b96ff7a2bf35d99d3576fed5bcb4f48503593f0c28a3c82f70e"
INSTALLED_LAB_LOCK_FIRST_LINE = (
    b"# CPython 3.14 Windows x64 runtime wheels; see docs/adminit-acceptance/DEPENDENCIES.md.\n"
)


def lock_blob_bytes():
    # Committed (LF) bytes; a Windows autocrlf checkout must not mask a change.
    return LAB_LOCK.read_bytes().replace(b"\r\n", b"\n")


def test_lab_lock_is_byte_identical_to_installed_payload():
    data = lock_blob_bytes()
    assert data.startswith(INSTALLED_LAB_LOCK_FIRST_LINE)
    assert hashlib.sha256(data).hexdigest() == INSTALLED_LAB_LOCK_SHA256


def test_lab_lock_git_blob_matches_d6059af():
    data = lock_blob_bytes()
    blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    assert blob == "f9813f1acfcbbc949275d4ee92388980e838e6c2"


def test_updater_keeps_dependency_and_schema_guards_before_stopping_app():
    text = UPDATER.read_text(encoding="utf-8")
    guard = text.index("'requirements-windows-lab.lock'")
    changed = text.index("Dependency/schema changes require a separate migration proposal.")
    count = text.index("New migrations are outside app-only upgrade.")
    stop = text.index("Set-UpgradeStage 'STOP_OWNED_APP'")
    assert guard < changed < count < stop
    # Both platform and storage migration trees are covered by the guard.
    assert len(re.findall(r"'\^src/\(api\|storage\)/migrations/'", text)) == 3


def test_lab_schema_upgrade_procedure_is_documented():
    doc = (ROOT / "docs/release/release-process.md").read_text(encoding="utf-8")
    section = doc[doc.index("## Existing lab installation upgrades") :]
    assert "app-only" in section
    assert INSTALLED_LAB_LOCK_SHA256 in section
    assert "008_public_unknown_taker_side.sql" in section
    assert "backup-restore" in section
    assert "UNVERIFIED" in section
    assert (ROOT / "src/storage/migrations/008_public_unknown_taker_side.sql").is_file()
