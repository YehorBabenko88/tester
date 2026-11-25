"""Platform-independent guards for the app-only C:\\TesterLab updater (issue #110)."""

import hashlib
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
    lock = text.index("foreach ($Name in @('requirements-windows-lab.lock'))")
    scope = text.index("$NewMigrations=Assert-UpgradeMigrationScope $OldPayload.files")
    stop = text.index("Set-UpgradeStage 'STOP_OWNED_APP'")
    assert lock < scope < stop
    # App-only refusals are unchanged; schema mode is an explicit separate switch.
    # Lock guard plus edited/removed-migration guard.
    assert text.count("Dependency/schema changes require a separate migration proposal.") == 2
    for message in (
        "New migrations are outside app-only upgrade.",
        "Declared schema migrations differ from the published payload.",
        "Schema upgrade mode requires declared new migrations.",
    ):
        assert text.count(message) == 1, message
    assert "if (-not $SchemaMode) {throw 'New migrations are outside app-only upgrade.'}" in text
    assert "[switch]$SchemaUpgrade" in text
    backup = text.index("Set-UpgradeStage 'BACKUP_RESTORE'")
    schema = text.index("Set-UpgradeStage 'SCHEMA_UPGRADE'")
    staged = text.index("Set-UpgradeStage 'STAGE_NEW_SOURCE'")
    assert backup < schema < staged
    assert text.rindex("if ($SchemaUpgrade) {", 0, schema) > backup
    assert "scripts\\lab_schema_upgrade.py') lab-upgrade" in text


def test_schema_upgrader_refuses_unsafe_targets():
    import pytest

    pytest.importorskip("psycopg")
    from scripts.lab_schema_upgrade import UpgradeRefused, safe_conninfo

    good = "host=127.0.0.1 port=55470 dbname=tester_lab user=tester_lab_admin"
    assert safe_conninfo(good)["dbname"] == "tester_lab"
    for dsn, reason in (
        ("host=127.0.0.1 port=5432 dbname=tester_lab user=o", "non_5432"),
        ("host=127.0.0.1 dbname=tester_lab user=o", "non_5432"),
        ("host=db.example port=55470 dbname=tester_lab user=o", "loopback_host"),
        ("host=127.0.0.1 hostaddr=192.0.2.1 port=55470 dbname=tester_lab user=o", "hostaddr"),
        ("host=127.0.0.1 port=55470 dbname=postgres user=o", "dedicated_database"),
        ("host=127.0.0.1 port=55470 dbname=tester_lab", "owner_user"),
        ("service=lab host=127.0.0.1 port=55470 dbname=tester_lab user=o", "explicit_connection"),
    ):
        with pytest.raises(UpgradeRefused, match=reason):
            safe_conninfo(dsn)


def test_lab_schema_upgrade_procedure_is_documented():
    doc = (ROOT / "docs/release/release-process.md").read_text(encoding="utf-8")
    section = doc[doc.index("## Existing lab installation upgrades") :]
    assert "app-only" in section
    assert INSTALLED_LAB_LOCK_SHA256 in section
    assert "008_public_unknown_taker_side.sql" in section
    assert "backup-restore" in section
    assert "UNVERIFIED" in section
    assert (ROOT / "src/storage/migrations/008_public_unknown_taker_side.sql").is_file()
