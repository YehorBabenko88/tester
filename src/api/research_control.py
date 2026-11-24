"""Inert G/E research contract seam. Not mounted by the application.

Only read-only chart projection is admitted. No engine, SQL, shell, import resolver,
SQLite ledger, credentials or provider discovery is implemented here.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated, Literal, Protocol
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,99}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Timestamp = Annotated[int, Field(ge=0, le=2**53 - 1)]


def canonical_digest(value: dict) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class Denied(Exception):
    """Authenticated identity lacks current action/object authority."""


class Unavailable(Exception):
    """A reviewed canonical provider/repository has not been bound."""


@dataclass(frozen=True)
class Identity:
    actor: str


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Binding(Strict):
    run_id: Identifier
    snapshot_id: Identifier
    revision: Revision
    venue: Literal["bybit"]
    category: Literal["spot", "linear", "inverse"]
    interval: Literal["1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W"]
    symbol: Annotated[str, Field(pattern=r"^[A-Z0-9_-]{1,40}$")]
    start_ms: Timestamp
    end_ms: Timestamp
    cutoff_ms: Timestamp

    @model_validator(mode="after")
    def range_is_bounded(self):
        if not 0 < self.end_ms - self.start_ms <= 366 * 86400000:
            raise ValueError("invalid bounded half-open range")
        return self


class Command(Binding):
    schema_version: Literal["tester.research-command/1"] = "tester.research-command/1"
    request_id: Identifier
    expected_artifact_digest: Digest

    @model_validator(mode="after")
    def canonical_request_id(self):
        if str(UUID(self.request_id)) != self.request_id:
            raise ValueError("canonical UUID required")
        return self

    action: Literal["chart.overlay"]


class Marker(Strict):
    role: Literal["entry", "exit"]
    side: Literal["buy", "sell"]
    fill_id: Identifier
    order_id: Identifier
    position_id: Identifier
    closed_trade_id: Identifier | None
    timestamp_ms: Timestamp
    known_at_ms: Timestamp
    window_start_ms: Timestamp
    window_end_ms: Timestamp
    time_precision: Literal["bar", "modeled_open"]
    price: Annotated[str, Field(pattern=r"^[0-9]{1,30}(\.[0-9]{1,18})?$")]

    @model_validator(mode="after")
    def uncertainty(self):
        if not self.window_start_ms <= self.timestamp_ms < self.window_end_ms:
            raise ValueError("timestamp outside half-open uncertainty window")
        if self.time_precision == "bar":
            if self.known_at_ms < self.window_end_ms:
                raise ValueError("bar fill unknown before window close")
        elif self.timestamp_ms != self.window_start_ms or self.known_at_ms != self.window_start_ms:
            raise ValueError("modeled open timestamp and knowledge must equal window start")
        if not any(c in "123456789" for c in self.price):
            raise ValueError("positive Decimal price required")
        return self


class Candle(Strict):
    start_ms: Timestamp
    end_ms: Timestamp
    open: Annotated[str, Field(pattern=r"^[0-9]{1,30}(\.[0-9]{1,18})?$")]
    high: Annotated[str, Field(pattern=r"^[0-9]{1,30}(\.[0-9]{1,18})?$")]
    low: Annotated[str, Field(pattern=r"^[0-9]{1,30}(\.[0-9]{1,18})?$")]
    close: Annotated[str, Field(pattern=r"^[0-9]{1,30}(\.[0-9]{1,18})?$")]


class Artifact(Binding):
    schema_version: Literal["tester.research-chart/2"]
    execution_mode: Literal["SIMULATED"]
    coverage: Literal["complete", "partial", "unavailable"]
    source_ids: Annotated[list[Identifier], Field(min_length=1, max_length=100)]
    synthetic: bool
    candles: Annotated[list[Candle], Field(max_length=2000)]
    markers: Annotated[list[Marker], Field(max_length=500)]

    @model_validator(mode="after")
    def fill_identity_and_visibility(self):
        ids = [marker.fill_id for marker in self.markers]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate fill")
        for marker in self.markers:
            if not self.start_ms <= marker.timestamp_ms < self.end_ms:
                raise ValueError("marker outside view")
            if marker.known_at_ms > self.cutoff_ms:
                raise ValueError("future knowledge")
        return self


class Ledger(Protocol):
    """B/E-owned PostgreSQL port, NEVER a parallel authoritative SQLite store.

    Implementations must atomically persist job+audit on submit, unique(actor,
    request_id) with command-digest comparison; complete must use lease/fencing,
    compare actor/command digest, persist immutable artifact+digest+audit in one
    transaction; acknowledge must atomically compare stored artifact digest and
    unique ACK+audit. Recovery/leases and retention belong to the repository.
    Rows returned by read must be actor scoped. No runtime DDL is permitted.
    This protocol is not a durable implementation or an activation gate bypass.
    """

    def submit(self, actor: str, command: Command, digest: str) -> dict: ...
    def read(self, actor: str, job_id: str) -> dict: ...
    def complete(self, actor: str, job_id: str, digest: str, artifact: dict) -> dict: ...
    def acknowledge(self, actor: str, job_id: str, digest: str) -> dict: ...


class Control:
    def __init__(self, *, store: Ledger, authorize: Callable, provider: Callable):
        self.store, self.authorize, self.provider = store, authorize, provider

    def submit(self, identity: Identity, command: Command):
        command = Command.model_validate(command.model_dump())
        self.authorize(identity.actor, command)
        return self.store.submit(
            identity.actor, command, canonical_digest(command.model_dump(mode="json"))
        )

    def _read(self, identity, job_id):
        try:
            row = self.store.read(identity.actor, job_id)
        except (KeyError, StopIteration) as exc:
            raise Denied("object denied") from exc
        if row["actor"] != identity.actor:
            raise Denied("object denied")
        command = Command.model_validate_json(json.dumps(row["command"]))
        if canonical_digest(row["command"]) != row["digest"]:
            raise ValueError("command digest mismatch")
        self.authorize(identity.actor, command)
        return row, command

    @staticmethod
    def _artifact(value, command):
        artifact = Artifact.model_validate(value)
        for key in Binding.model_fields:
            if getattr(artifact, key) != getattr(command, key):
                raise ValueError("artifact binding mismatch")
        result = artifact.model_dump(mode="json")
        if canonical_digest(result) != command.expected_artifact_digest:
            raise ValueError("pinned artifact digest mismatch")
        return result

    def dispatch(self, identity: Identity, job_id: str):
        """Worker seam only; canonical worker must claim/fence job before calling.

        Provider must be a reviewed, injected, read-only artifact resolver. Repeated
        dispatch may resolve again after crash, so resolution must be side-effect-free.
        """
        row, command = self._read(identity, job_id)
        if row["state"] == "complete":
            return self.result(identity, job_id)
        if row["state"] != "pending":
            raise ValueError("job not dispatchable")
        if self.provider is None:
            raise Unavailable("NOT_INTEGRATED")
        supplied = self.provider(command)
        artifact = self._artifact(
            supplied.model_dump() if isinstance(supplied, Artifact) else supplied, command
        )
        self.authorize(identity.actor, command)  # recheck revocation after resolution
        return self.store.complete(identity.actor, job_id, row["digest"], artifact)

    def result(self, identity: Identity, job_id: str):
        row, command = self._read(identity, job_id)
        if row["state"] != "complete":
            return {"job_id": row["job_id"], "state": row["state"]}
        artifact = self._artifact(row["artifact"], command)
        digest = canonical_digest(artifact)
        if digest != row["artifact_digest"]:
            raise ValueError("artifact digest mismatch")
        return {
            "job_id": row["job_id"],
            "state": "complete",
            "artifact": artifact,
            "artifact_digest": digest,
        }

    def ack(self, identity: Identity, job_id: str, digest: str, rendered_fill_ids: list[str]):
        result = self.result(identity, job_id)
        if result.get("artifact_digest") != digest or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("render acknowledgment digest mismatch")
        expected = [marker["fill_id"] for marker in result["artifact"]["markers"]]
        if len(rendered_fill_ids) != len(set(rendered_fill_ids)) or sorted(
            rendered_fill_ids
        ) != sorted(expected):
            raise ValueError("complete rendered fill set required")
        self.store.acknowledge(identity.actor, job_id, digest, rendered_fill_ids)
        return {"job_id": job_id, "artifact_digest": digest, "acknowledged": True}


class Ack(Strict):
    artifact_digest: Digest
    rendered_fill_ids: Annotated[list[Identifier], Field(max_length=500)]


def create_control_app(control: Control | None, authenticate: Callable) -> FastAPI:
    """Opt-in contract harness, not mounted in src.api.main or installer.

    Authenticator MUST resolve server-side expiring/revocable credentials, never
    caller actor fields. Bind transport to loopback; reviewed product wiring must
    supply current capability+object ACL authorization and PostgreSQL repository.
    """
    app = FastAPI(title="Tester research control (NOT_INTEGRATED)")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.middleware("http")
    async def origin(request: Request, call_next):
        if (
            request.headers.get("origin") is not None
            and request.headers["origin"] != str(request.base_url).rstrip("/")
        ) or request.headers.get("sec-fetch-site") == "cross-site":
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=403, content={"detail": "browser origins denied"})
        return await call_next(request)

    def identity(authorization: str | None = Header(default=None)):
        try:
            value = authenticate(authorization)
            if not isinstance(value, Identity) or not value.actor:
                raise Denied("invalid principal")
            return value
        except Denied as exc:
            raise HTTPException(401, "authentication required") from exc

    def invoke(operation):
        if control is None:
            raise HTTPException(503, "NOT_INTEGRATED")
        try:
            return operation()
        except Denied as exc:
            raise HTTPException(403, "permission denied") from exc
        except Unavailable as exc:
            raise HTTPException(503, "NOT_INTEGRATED") from exc
        except ValueError as exc:
            raise HTTPException(409, "contract or replay conflict") from exc

    @app.post("/api/control/research", status_code=202)
    def submit(command: Command, actor: Identity = Depends(identity)):
        row = invoke(lambda: control.submit(actor, command))
        return {"job_id": row["job_id"], "state": row["state"]}

    @app.get("/api/control/research/{job_id}")
    def result(job_id: UUID, actor: Identity = Depends(identity)):
        return invoke(lambda: control.result(actor, str(job_id)))

    @app.post("/api/control/research/{job_id}/ack")
    def acknowledge(job_id: UUID, ack: Ack, actor: Identity = Depends(identity)):
        return invoke(
            lambda: control.ack(actor, str(job_id), ack.artifact_digest, ack.rendered_fill_ids)
        )

    return app
