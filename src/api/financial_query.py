"""Versioned, explicit financial inputs; no clock or instrument metadata defaults."""

import re
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt, model_validator

MAX_TIME = 9007199254740991


def integer_time(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,16}", value):
        value = int(value)
    if type(value) is not int or not 0 <= value <= MAX_TIME:
        raise ValueError("Expected explicit integer epoch milliseconds")
    return value


def positive_step(value):
    if (
        type(value) is not str
        or len(value) > 256
        or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)
        or not any(c in "123456789" for c in value)
    ):
        raise ValueError("Expected positive plain decimal string")
    return value


HttpTime = Annotated[int, BeforeValidator(integer_time), Field(ge=0, le=MAX_TIME)]
PriceStep = Annotated[
    str,
    BeforeValidator(positive_step),
    Field(
        max_length=256,
        pattern=r"^[0-9]+(?:\.[0-9]+)?$",
        json_schema_extra={
            "pattern": r"^[0-9]+(?:\.[0-9]+)?$",
            "not": {"anyOf": [{"pattern": r"^0+(?:\.0+)?$"}, {"pattern": r"[^0-9.]"}]},
        },
    ),
]


class FinancialRequest(BaseModel):
    """Frozen normalized provider input. Availability is supplied independently by storage."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"price_step_mode": {"const": "instrument_tick"}}},
                    "then": {"properties": {"instrument_tick_ref": {"type": "string"}}},
                    "else": {"properties": {"instrument_tick_ref": {"type": "null"}}},
                }
            ]
        },
    )
    query_schema_version: Literal["1.0"]
    venue: Literal["bybit"]
    category: Literal["linear", "inverse", "spot"]
    symbol: str = Field(pattern=r"^[A-Z0-9_-]{1,40}$")
    start_ms: StrictInt = Field(ge=0, le=MAX_TIME)
    end_ms: StrictInt = Field(gt=0, le=MAX_TIME)
    information_cutoff_ms: StrictInt = Field(ge=0, le=MAX_TIME)
    interval: str = Field(pattern=r"^[a-zA-Z0-9]{1,12}$")
    source_interval: str | None = Field(pattern=r"^[a-zA-Z0-9]{1,12}$")
    price_step: PriceStep
    price_step_mode: Literal["explicit", "instrument_tick"]
    instrument_tick_ref: str | None = Field(
        min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_:.@/-]+$"
    )
    limit: StrictInt = Field(ge=1, le=1000)
    cursor: str | None = Field(max_length=2048)

    @model_validator(mode="after")
    def valid_selection(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must exceed start_ms")
        if (self.price_step_mode == "instrument_tick") != (self.instrument_tick_ref is not None):
            raise ValueError("Step selection requires matching reference policy")
        return self


class FinancialReader(Protocol):
    def read_derived(self, resource: str, request: FinancialRequest) -> dict: ...


class FinancialQueryError(ValueError):
    def __init__(self, code, status=422):
        self.code = code
        self.status = status


FIELDS = {
    "query_schema_version",
    "information_cutoff_ms",
    "price_step",
    "price_step_mode",
    "instrument_tick_ref",
}


def is_derived(resource, query):
    derived = resource == "footprint" or (resource == "candles" and query.candle_kind == "derived")
    if not derived:
        if any(getattr(query, key) is not None for key in FIELDS):
            raise FinancialQueryError("financial_query_unsupported")
        return False
    required = FIELDS - {"instrument_tick_ref"}
    if (
        any(getattr(query, key) is None for key in required)
        or query.symbol is None
        or not {"start_ms", "end_ms"} <= query.model_fields_set
    ):
        raise FinancialQueryError("financial_query_required")
    if (query.price_step_mode == "instrument_tick") != (query.instrument_tick_ref is not None):
        raise FinancialQueryError("price_step_selection_invalid")
    return True


def read_derived(reader, resource, query, raw_cursor):
    request = FinancialRequest.model_validate(
        {key: getattr(query, key) for key in FinancialRequest.model_fields} | {"cursor": raw_cursor}
    )
    if request.price_step_mode == "instrument_tick":
        raise FinancialQueryError("instrument_tick_unavailable", 503)
    port = getattr(reader, "read_derived", None)
    if not callable(port):
        raise FinancialQueryError("derived_provider_unavailable", 503)
    return port(resource, request)
