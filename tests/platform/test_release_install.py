import uuid

import pytest

from src.api import release_install as ri


def test_test_root_requires_unique_name_and_fake_desktop(tmp_path):
    with pytest.raises(ValueError):
        ri.validate_destination(tmp_path / "ordinary", mode="test", desktop=tmp_path / "desktop")
    root = tmp_path / ("TesterReleaseTest_" + uuid.uuid4().hex)
    ri.validate_destination(root, mode="test", desktop=root.parent / ("Desktop_" + root.name))
    with pytest.raises(ValueError):
        ri.validate_destination(tmp_path / "TesterLab", mode="production", desktop=None)


def test_lifecycle_lock_excludes_second_owner(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with ri.lifecycle_lock(root):
        with pytest.raises(ValueError):
            with ri.lifecycle_lock(root):
                pass
    with ri.lifecycle_lock(root):
        pass


def test_marker_authority_remains_fail_closed_under_optimized_python(tmp_path):
    import json
    import subprocess
    import sys

    ident = uuid.uuid4()
    root = tmp_path / ("TesterReleaseTest_" + ident.hex)
    root.mkdir()
    marker = dict(
        schema=ri.SCHEMA,
        root=str(root),
        installation_id=str(ident),
        source_sha="a" * 40,
        release_manifest_sha256="b" * 64,
        mode="test",
        test_desktop=str(tmp_path / ("Desktop_" + ident.hex)),
        services={"app": "ForeignService", "postgres": "PostgreSQL"},
        ports={"api": 5432, "postgres": 5432},
    )
    (root / ri.MARKER).write_text(json.dumps(marker))
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            "from src.api.release_install import load_marker; import sys; load_marker(sys.argv[1])",
            str(root),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "invalid release ownership marker" in result.stderr
