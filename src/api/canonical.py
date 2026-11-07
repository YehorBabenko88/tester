"""Lossless FastAPI boundaries over the pinned, shared Issue #8 validators."""

from copy import deepcopy
from functools import lru_cache
from typing import Any, ClassVar

from pydantic import RootModel, model_validator

from contracts.v1.validate import ContractError, _schema, validate_record, validate_response


def dataset_response(kind: str, value: dict) -> dict:
    if kind in {"source", "coverage"}:
        validate_record("api_response", value)
        if not isinstance(value["data"], list):
            raise ContractError("Dataset data must be a list")
        if not value["data"] and value["coverage"]["status"] == "COMPLETE":
            raise ContractError("Empty dataset does not establish complete coverage")
        for row in value["data"]:
            if kind == "coverage":
                validate_record("coverage", row)
                if value["coverage"]["status"] == "COMPLETE" and row["status"] != "COMPLETE":
                    raise ContractError("Envelope cannot promote partial coverage rows to COMPLETE")
    else:
        validate_response(kind, value)
    if kind == "trade":
        keys = set()
        for row in value["data"]:
            key = tuple(row[name] for name in ("venue", "category", "symbol", "trade_id"))
            if key in keys:
                raise ContractError("Duplicate or conflicting trade identity in provider page")
            keys.add(key)
    return value


def expanded_schema(kind):
    definitions = _schema()["$defs"]

    def expand(value):
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(deepcopy(definitions[value["$ref"].rsplit("/", 1)[1]]))
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return expand(deepcopy(definitions[kind]))


class CanonicalRecord(RootModel[dict[str, Any]]):
    record_kind: ClassVar[str]

    @model_validator(mode="after")
    def approved_record(self):
        validate_record(self.record_kind, self.root)
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        return expanded_schema(cls.record_kind)


class Dataset(RootModel[dict[str, Any]]):
    record_kind: ClassVar[str]

    @model_validator(mode="after")
    def approved_dataset(self):
        dataset_response(self.record_kind, self.root)
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        result = expanded_schema("api_response")
        row = (
            expanded_schema(cls.record_kind) if cls.record_kind != "source" else {"type": "object"}
        )
        result["properties"]["data"] = {"type": "array", "items": row}
        return result


@lru_cache
def dataset_model(kind):
    return type(kind.title() + "Dataset", (Dataset,), {"record_kind": kind})
