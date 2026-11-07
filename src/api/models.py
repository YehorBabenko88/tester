from datetime import datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")
Category = Literal["linear", "inverse", "spot"]
Kind = Literal["candles", "trades", "footprint", "open_interest", "funding"]


class Envelope(BaseModel, Generic[T]):
    schema_version: Literal["1.0"] = "1.0"
    data: T
    next_cursor: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    error: ErrorDetail


class Query(BaseModel):
    category: Category = "linear"
    symbol: str | None = Field(default=None, pattern=r"^[A-Z0-9_-]{1,40}$")
    start_ms: int = Field(default=0, ge=0)
    end_ms: int = Field(default=253402300799000, ge=1, le=253402300799000)
    interval: str = Field(default="60", pattern=r"^[a-zA-Z0-9]{1,12}$")
    limit: int = Field(default=100, ge=1, le=1000)
    cursor: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must exceed start_ms (exclusive end)")
        return self


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    symbol: str = Field(pattern=r"^[A-Z0-9_-]{1,40}$")
    source: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    kind: Kind
    interval: str = Field(default="60", pattern=r"^[a-zA-Z0-9]{1,12}$")
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0, le=253402300799000)

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must exceed start_ms")
        return self


class Job(BaseModel):
    id: UUID
    request: DownloadRequest
    state: Literal["queued", "running", "paused", "completed", "failed"]
    progress: float = Field(ge=0, le=1)
    checkpoint: dict[str, Any]
    attempts: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class Lease(Job):
    lease_token: UUID


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    worker: Literal["ok", "unavailable"]
    storage: Literal["ok", "low_disk", "quota_exceeded", "unavailable"]
    free_bytes: int | None
    used_bytes: int | None


class Instrument(BaseModel):
    symbol: str
    category: Category | None = None
    provenance: str
    status: str | None = None


class Source(BaseModel):
    id: str
    kinds: list[Kind]
    availability: Literal["available", "partial", "unavailable", "unknown"]
    provenance: str


class Coverage(BaseModel):
    symbol: str
    kind: Kind
    start_ms: int
    end_ms: int
    status: Literal["complete", "partial", "missing", "unknown"]
    provenance: str


class Candle(BaseModel):
    timestamp_ms: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    provenance: str


class Trade(BaseModel):
    id: str
    timestamp_ms: int
    price: str
    quantity: str
    side: Literal["buy", "sell", "unknown"]
    provenance: str


class Footprint(BaseModel):
    timestamp_ms: int
    price: str
    buy_volume: str
    sell_volume: str
    provenance: str


class OpenInterest(BaseModel):
    timestamp_ms: int
    value: str
    provenance: str


class Funding(BaseModel):
    timestamp_ms: int
    rate: str
    provenance: str
