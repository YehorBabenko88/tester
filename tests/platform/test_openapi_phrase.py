import http.client
import json
from pathlib import Path

import pytest

from src.api.config import Settings
from src.api.main import create_app


@pytest.mark.parametrize(
    "default_phrase", ["Unprocessable Entity", "Unprocessable Content", "Future default phrase"]
)
def test_approved_422_description_ignores_interpreter_default(
    monkeypatch, tmp_path, default_phrase
):
    monkeypatch.setitem(http.client.responses, 422, default_phrase)
    document = create_app(Settings(data_dir=tmp_path)).openapi()
    approved = json.loads(
        (Path(__file__).resolve().parents[2] / "contracts/v1/openapi.json").read_text(
            encoding="utf-8"
        )
    )
    responses = [
        operation["responses"]["422"]
        for path in document["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "responses" in operation
    ]
    assert responses
    assert all(response["description"] == "Unprocessable Content" for response in responses)
    assert document == approved
