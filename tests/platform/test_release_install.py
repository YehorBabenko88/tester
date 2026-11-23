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
