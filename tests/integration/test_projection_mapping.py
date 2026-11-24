"""Original projections stay immutable while new authentic observations may replay."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from src.integration import lab_market
from src.storage.store import DataConflict


def test_inserted_projection_matches_captured_mapping(monkeypatch):
    monkeypatch.setattr(lab_market, "validate_record", lambda *a: None)
    connection = Mock()
    original = {"identity": "fixture", "metadata": {"trusted": True}}
    connection.execute.return_value.fetchone.return_value = (original,)
    assert lab_market.publish_projection(connection, "record:fixture", original) == original
    assert "ON CONFLICT DO NOTHING" in connection.execute.call_args_list[0].args[0]
    changed = deepcopy(original)
    changed["metadata"]["trusted"] = 1
    connection.execute.return_value.fetchone.return_value = (changed,)
    with pytest.raises(DataConflict, match="projection"):
        lab_market.publish_projection(connection, "record:fixture", original)


def test_projection_conflict_preserves_original_for_existing_batch_validation(monkeypatch):
    checked = []
    monkeypatch.setattr(lab_market, "validate_record", lambda kind, value: checked.append(value))
    connection = Mock()
    stored = {"provenance": [{"ingestion_run": "original-authentic-run"}]}
    new = {"provenance": [{"ingestion_run": "new-authentic-run"}]}
    connection.execute.return_value.fetchone.side_effect = [None, (stored,)]
    assert lab_market.publish_projection(connection, "record:fixture", new) == stored
    assert checked == [stored]
    assert all(
        "UPDATE" not in call.args[0] and "DELETE" not in call.args[0]
        for call in connection.execute.call_args_list
    )
