"""Published-origin guard, without backend substitutes or installed access."""

import pytest

from src.integration.research_runtime import require_published_origin


def test_refuses_unpinned_source_folder_and_wrong_commit(tmp_path):
    with pytest.raises(ValueError):
        require_published_origin(tmp_path / "checkout", "a" * 40)
    with pytest.raises(ValueError):
        require_published_origin(tmp_path / ("b" * 40), "a" * 40)
    with pytest.raises(ValueError):
        require_published_origin(tmp_path / ("a" * 40), "main")
    assert require_published_origin(tmp_path / ("a" * 40), "a" * 40) == tmp_path / ("a" * 40)
