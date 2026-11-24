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


def test_hosted_query_failure_is_unverified_not_empty(monkeypatch):
    from scripts import repository_inventory as audit

    def unavailable(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(audit.subprocess, "run", unavailable)
    result = audit.hosted_query("owner/repo", "tags")
    assert result["status"] == "NOT_VERIFIED"
    assert result["items"] is None


def test_workflow_filters_preserve_actual_patterns():
    from scripts.repository_inventory import workflow_filters

    result = workflow_filters(
        {
            ".github/workflows/test.yml": "on:\n  pull_request:\n    paths:\n      - 'src/api/**'\n      - 'tests/**'\njobs:\n"
        }
    )
    filters = result[".github/workflows/test.yml"]["filters"]
    assert filters[0]["paths"] == ["src/api/**", "tests/**"]
    assert filters[0]["status"] == "PARSED_STATIC_LIST"


def test_branch_consumers_preserve_open_pr_base_and_deployment_pin():
    from scripts.repository_inventory import reference_consumers

    hosted = {
        "pull_requests": {
            "status": "VERIFIED",
            "items": [{"number": 101, "headRefName": "candidate", "baseRefName": "main"}],
        },
        "deployments": {
            "status": "VERIFIED",
            "items": [{"id": 1, "ref": "candidate", "sha": "a" * 40}],
        },
        "tags": {"status": "NOT_VERIFIED", "items": None},
    }
    result = reference_consumers("refs/remotes/origin/candidate", "a" * 40, hosted)
    assert result["status"] == "PARTIALLY_VERIFIED"
    assert {x["kind"] for x in result["items"]} == {"pull_requests", "deployments"}
