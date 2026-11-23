import subprocess

from scripts.repository_inventory import STATES, inventory


def test_inventory_preserves_dynamic_consumers_and_unique_branches(tmp_path):
    (tmp_path / "scripts/windows_preview").mkdir(parents=True)
    (tmp_path / "scripts/windows_preview/old.ps1").write_text("legacy")
    (tmp_path / "entry.py").write_text('import importlib\nimportlib.import_module("plugin")\n')
    (tmp_path / "README.md").write_text("[old](scripts/windows_preview/old.ps1) [missing](gone.md)")
    for args in [
        ("init",),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.invalid"),
        ("add", "."),
        ("commit", "-m", "base"),
        ("branch", "preserved"),
    ]:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    result = inventory(tmp_path)
    assert result["coverage"]["tracked_files"] == 3
    assert result["coverage"]["pr_status"] == "NOT_FETCHED"
    assert all(
        f["classification"] in STATES and not f["deletion_authorized"] for f in result["files"]
    )
    old = next(f for f in result["files"] if f["path"].endswith("old.ps1"))
    assert old["classification"] == "REPLACE"
    assert "README.md" in old["text_consumers"]
    assert result["unresolved_document_links"][0]["target"] == "gone.md"
    assert all(not b["delete_now"] for b in result["branches"])
