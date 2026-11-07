from copy import deepcopy

import pytest
from canonical_fixtures import envelope, trade
from pydantic import ValidationError

from src.api.models import Trade


def test_canonical_trade_preserves_all_metadata_exactly():
    value = trade()
    assert Trade.model_validate(value).model_dump(mode="json") == value


@pytest.mark.parametrize(
    "change",
    [
        {"timestamp_ms": -1},
        {"schema_version": "2.0"},
        {"price": "NaN"},
        {"price": "Infinity"},
        {"price": "1e3"},
        {"price": 1.0},
        {"quantity": "0"},
        {"timestamp_ns": "1700000000123000001", "timestamp_precision": "ms"},
        {"surprise_metadata": "cannot silently drop"},
    ],
)
def test_reject_bad_canonical_trade(change):
    value = trade()
    value.update(change)
    with pytest.raises(ValidationError):
        Trade.model_validate(value)


def test_ms_only_source_keeps_null_ns_and_unavailable_reason():
    value = trade()
    value.update(timestamp_ns=None, timestamp_precision="ms")
    value["provenance"][0].update(timestamp_precision="ms", source_timestamp="1700000000123")
    value["availability"] = dict(status="UNAVAILABLE", reason="source_conflict")
    assert Trade.model_validate(value).model_dump(mode="json") == value


def test_dataset_boundary_validates_rows_and_complete_envelope():
    from src.api.canonical import dataset_response

    value = envelope()
    assert dataset_response("trade", value) == value
    malformed = deepcopy(value)
    malformed["data"][0]["price"] = "NaN"
    with pytest.raises(ValueError):
        dataset_response("trade", malformed)
    promoted = deepcopy(value)
    promoted["availability"] = dict(status="AVAILABLE", reason=None)
    with pytest.raises(ValueError):
        dataset_response("trade", promoted)
    duplicate = deepcopy(value)
    duplicate["data"].append(deepcopy(value["data"][0]))
    with pytest.raises(ValueError):
        dataset_response("trade", duplicate)


def test_empty_does_not_establish_complete_coverage():
    from src.api.canonical import dataset_response

    value = envelope()
    value["data"] = []
    value["coverage"]["status"] = "EMPTY"
    assert dataset_response("trade", value)["coverage"]["status"] == "EMPTY"
    value["coverage"].update(status="COMPLETE", evidence_refs=["fixture"])
    value["availability"] = dict(status="AVAILABLE", reason=None)
    with pytest.raises(ValueError):
        dataset_response("trade", value)


@pytest.mark.parametrize("kind", ["candle", "footprint", "observation"])
def test_other_market_models_roundtrip_without_metadata_loss(kind):
    from src.api.models import Candle, Footprint, Funding

    row = trade()
    for name in [
        "timestamp_ns",
        "timestamp_precision",
        "trade_id",
        "id_origin",
        "price",
        "quantity",
        "taker_side",
    ]:
        row.pop(name)
    if kind == "candle":
        row.update(
            start_ms=1700000000000,
            end_ms=1700000060000,
            interval="1",
            kind="official_trade",
            open="100",
            high="102",
            low="99",
            close="101",
            volume="3",
            is_closed=True,
        )
        model = Candle
    elif kind == "footprint":
        row["provenance"][0].update(
            source_kind="derived", parent_refs=["fixture://trades"], method_version="fixture-v1"
        )
        row.update(
            start_ms=1700000000000,
            end_ms=1700000060000,
            side_basis="taker",
            buy_volume="1",
            sell_volume="0",
            unknown_side_volume="2",
            delta=None,
        )
        model = Footprint
    else:
        row.update(kind="funding", value="-0.0001", unit="rate", interval="event")
        model = Funding
    assert model.model_validate(row).model_dump(mode="json") == row


def test_coverage_envelope_cannot_promote_partial_rows():
    from src.api.canonical import dataset_response

    value = envelope()
    value["data"] = [deepcopy(value["coverage"])]
    value["coverage"].update(status="COMPLETE", evidence_refs=["fixture-proof"])
    value["availability"] = dict(status="AVAILABLE", reason=None)
    with pytest.raises(ValueError):
        dataset_response("coverage", value)


def test_openapi_contains_full_shared_record_contract(tmp_path):
    from jsonschema import Draft202012Validator

    from src.api.config import Settings
    from src.api.main import create_app

    schema = create_app(Settings(data_dir=tmp_path)).openapi()["components"]["schemas"][
        "TradeDataset"
    ]
    validator = Draft202012Validator(schema)
    value = envelope()
    validator.validate(value)
    value["data"][0]["timestamp_ms"] = -1
    assert list(validator.iter_errors(value))
