"""Guard tests; no PostgreSQL process or installed service is accessed."""

import json
from pathlib import Path

import pytest

from src.integration.research_lab import LabScopeError, check_layout, load_lab


def test_refuses_adoption_escape_and_reparse(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = workspace / ("research-" + "a" * 32)
    assert check_layout(workspace, root) == root
    for foreign in (workspace, workspace.parent / root.name, workspace / "tester_lab"):
        with pytest.raises(LabScopeError):
            check_layout(workspace, foreign)
    root.mkdir()
    with pytest.raises(LabScopeError, match="already exists"):
        check_layout(workspace, root, new=True)


def test_reparse_is_not_an_owned_cluster(tmp_path):
    workspace = tmp_path
    root = workspace / ("research-" + "a" * 32)
    root.mkdir()
    link = workspace / ("research-" + "b" * 32)
    try:
        link.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink privilege unavailable")
    with pytest.raises(ValueError, match="Reparse|symlink"):
        check_layout(workspace, link)


def test_marker_cannot_redirect_stop_to_another_lab_or_reserved_port(tmp_path):
    root = tmp_path / ("research-" + "a" * 32)
    root.mkdir()
    value = dict(
        schema="tester-research-lab/1",
        root=str(root),
        workspace=str(tmp_path),
        installation_id="a" * 32,
        source_sha="1" * 40,
        postgres_port=55471,
        api_port=18767,
        database="tester_research_" + "a" * 32,
    )
    marker = root / ".research-lab.json"
    marker.write_text(json.dumps(value))
    assert load_lab(root, "1" * 40)["database"] == value["database"]
    for key, wrong in (
        ("postgres_port", 5432),
        ("postgres_port", 55470),
        ("api_port", 18766),
        ("root", str(root.parent)),
        ("database", "tester_lab"),
        ("installation_id", "b" * 32),
    ):
        bad = dict(value, **{key: wrong})
        marker.write_text(json.dumps(bad))
        with pytest.raises(LabScopeError):
            load_lab(root, "1" * 40)
    marker.write_text(json.dumps(value))
    with pytest.raises(LabScopeError):
        load_lab(root, "2" * 40)


def test_workspace_cannot_be_an_installed_lab():
    if Path("C:/TesterLab").is_absolute():
        with pytest.raises(LabScopeError):
            check_layout(Path("C:/TesterLab"), Path("C:/TesterLab/research-" + "a" * 32))
