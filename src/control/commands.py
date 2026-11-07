"""Typed, fail-closed control-plane request validation. No shell/SQL/code execution."""
from dataclasses import dataclass
from typing import Any
from uuid import UUID
import re

SCHEMA_VERSION = "1.0"
READ_ONLY = frozenset({"instrument.search","history.coverage","chart.navigate",
                       "system.health","system.config.preview"})
JOBS = frozenset({"history.download","chart.overlay","analytics.run",
                  "strategy.validate","backtest.run","backtest.compare"})
APPROVAL_REQUIRED = frozenset({"system.config.apply","dev.change.request"})
ACTIONS = READ_ONLY | JOBS | APPROVAL_REQUIRED
SYMBOL = re.compile(r"^[A-Z0-9_-]{1,40}$")
CATEGORIES = {"spot","linear","inverse"}
MAX_WINDOW_MS = 366 * 86400000

@dataclass(frozen=True)
class Command:
    request_id: str
    actor: str
    action: str
    category: str | None
    symbol: str | None
    start_ms: int | None
    end_ms: int | None
    params: dict[str, Any]
    schema_version: str = SCHEMA_VERSION

    @property
    def approval_required(self):
        return self.action in APPROVAL_REQUIRED

def parse_command(payload: dict[str, Any]) -> Command:
    if not isinstance(payload, dict):
        raise ValueError("request must be an object")
    allowed = {"request_id","actor","action","category","symbol","start_ms","end_ms","params","schema_version"}
    if set(payload) - allowed:
        raise ValueError("unexpected command fields")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported schema version")
    try:
        request_id = str(UUID(payload["request_id"]))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError("invalid request_id") from exc
    actor = payload.get("actor")
    if not isinstance(actor,str) or not re.fullmatch(r"[a-zA-Z0-9:_-]{1,100}",actor):
        raise ValueError("invalid actor")
    action = payload.get("action")
    if action not in ACTIONS:
        raise ValueError("action not allowed")
    category, symbol = payload.get("category"),payload.get("symbol")
    if category is not None and category not in CATEGORIES:
        raise ValueError("invalid category")
    if symbol is not None and (not isinstance(symbol,str) or not SYMBOL.fullmatch(symbol)):
        raise ValueError("invalid symbol")
    start,end = payload.get("start_ms"),payload.get("end_ms")
    if (start is None) != (end is None):
        raise ValueError("both range boundaries required")
    if start is not None and (type(start) is not int or type(end) is not int
                              or start<0 or end<=start or end-start>MAX_WINDOW_MS):
        raise ValueError("invalid half-open UTC range")
    params=payload.get("params",{})
    if not isinstance(params,dict) or len(params)>32:
        raise ValueError("invalid params")
    return Command(request_id,actor,action,category,symbol,start,end,params)

def required_capability(action: str) -> str:
    if action not in ACTIONS:
        raise ValueError("unknown action")
    return "approval" if action in APPROVAL_REQUIRED else "job" if action in JOBS else "read"

def authorize(command: Command, capabilities: set[str], *, approved: bool=False) -> None:
    cap=required_capability(command.action)
    if cap not in capabilities:
        raise PermissionError("capability denied")
    if command.approval_required and not approved:
        raise PermissionError("human approval required")
