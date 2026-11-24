"""Versioned wire contracts. All input is data, never executable instructions."""
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from uuid import UUID

from .commands import ACTIONS, CATEGORIES, SYMBOL, MAX_WINDOW_MS
from .types import CommandEnvelope, ResultEnvelope

VERSION = "1.1"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,99}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
TIMEFRAMES = {"1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M"}
MARKET_ACTIONS = ACTIONS - {"instrument.search", "system.health", "system.config.preview", "system.config.apply", "dev.change.request"}
PARAMS = {
    "instrument.search": {"query", "limit"}, "history.coverage": {"limit", "cursor"},
    "history.download": {"interval", "data_kind"}, "chart.navigate": {"interval"},
    "chart.overlay": {"run_id", "revision", "interval", "limit", "cursor"},
    "analytics.run": {"analysis_id", "interval"},
    "strategy.validate": {"strategy_id", "strategy_revision"},
    "backtest.run": {"strategy_id", "strategy_revision", "validation_id"},
    "backtest.compare": {"run_ids"}, "system.health": set(),
    "system.config.preview": {"expected_revision", "changes"},
    "system.config.apply": {"expected_revision", "changes"},
    "dev.change.request": {"base_revision", "branch", "task_ref"},
}
REQUIRED = {
    "history.download": {"interval", "data_kind"}, "chart.navigate": {"interval"},
    "chart.overlay": {"run_id", "revision", "interval"},
    "analytics.run": {"analysis_id", "interval"},
    "strategy.validate": {"strategy_id", "strategy_revision"},
    "backtest.run": {"strategy_id", "strategy_revision", "validation_id"},
    "backtest.compare": {"run_ids"}, "system.config.apply": {"expected_revision", "changes"},
    "dev.change.request": {"base_revision", "branch", "task_ref"},
}
CONFIG_LIMITS = {"max_workers": (1, 8), "max_downloads": (1, 8), "memory_mb": (128, 16384)}
SECRET = re.compile(r"(?i)(token|password|secret|authorization|api[_-]?key|cookie|credential)")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def bounded_json(value, max_bytes=262144):
    """Reject executable markup and secret-bearing fields; output remains untrusted."""
    def walk(v, depth=0):
        if depth > 12: raise ValueError("JSON nesting limit")
        if isinstance(v, dict):
            for k, item in v.items():
                if not isinstance(k, str) or SECRET.search(k): raise ValueError("unsafe field")
                walk(item, depth+1)
        elif isinstance(v, list):
            if len(v) > 2000: raise ValueError("array limit")
            for item in v: walk(item, depth+1)
        elif isinstance(v, str):
            if len(v) > 8192 or re.search(r"(?i)(<[^>]*>|javascript:|bearer\s+\S+|(?:gh[pousr]_|sk-)[A-Za-z0-9_-]{12,})", v):
                raise ValueError("unsafe text")
        elif v is not None and type(v) not in (bool, int, float):
            raise ValueError("JSON value required")
    walk(value)
    if len(canonical(value).encode()) > max_bytes: raise ValueError("JSON size limit")


def identifier(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def validate_command(payload: object, now: float) -> CommandEnvelope:
    bounded_json(payload, 32768)
    if not isinstance(payload, dict): raise ValueError("command object required")
    fields = {"schema_version", "request_id", "actor", "scope", "correlation_id", "issued_at", "expires_at", "action", "category", "symbol", "start_ms", "end_ms", "snapshot_id", "params"}
    if set(payload) != fields or payload["schema_version"] != VERSION: raise ValueError("unsupported envelope")
    for name in ("request_id", "correlation_id"):
        try:
            if str(UUID(payload[name])) != payload[name]: raise ValueError("noncanonical UUID")
        except (TypeError, AttributeError, ValueError) as exc: raise ValueError("invalid UUID") from exc
    for name in ("actor", "scope"):
        if not identifier(payload[name]): raise ValueError("invalid identity or scope")
    issued, expiry = payload["issued_at"], payload["expires_at"]
    if not integer(issued, 0, 2**53-1) or not integer(expiry, 0, 2**53-1) or not issued <= now < expiry or expiry-issued > 900:
        raise ValueError("expired or invalid request lifetime")
    action = payload["action"]
    if not isinstance(action, str) or action not in ACTIONS: raise ValueError("action not allowed")
    category, symbol = payload["category"], payload["symbol"]
    if category is not None and (not isinstance(category, str) or category not in CATEGORIES): raise ValueError("invalid category")
    if symbol is not None and (not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol)): raise ValueError("invalid symbol")
    start, end = payload["start_ms"], payload["end_ms"]
    if (start is None) != (end is None): raise ValueError("range boundaries required")
    if start is not None and (not integer(start, 0, 2**53-1) or not integer(end, 1, 2**53-1) or not 0 < end-start <= MAX_WINDOW_MS): raise ValueError("invalid UTC range")
    if action in MARKET_ACTIONS and (category is None or symbol is None or start is None or not identifier(payload["snapshot_id"])):
        raise ValueError("market identity, range and pinned snapshot required")
    if action in MARKET_ACTIONS and end > int(now * 1000): raise ValueError("historical range required")
    if payload["snapshot_id"] is not None and not identifier(payload["snapshot_id"]): raise ValueError("invalid snapshot")
    p = payload["params"]
    if not isinstance(p, dict) or set(p)-PARAMS[action] or REQUIRED.get(action, set())-set(p): raise ValueError("invalid action parameters")
    for key, value in p.items():
        valid = True
        if key == "limit": valid = integer(value, 1, 500)
        elif key in {"revision", "expected_revision"}: valid = integer(value, 0, 2**31-1)
        elif key == "interval": valid = isinstance(value, str) and value in TIMEFRAMES
        elif key == "data_kind": valid = isinstance(value, str) and value in {"trade", "mark", "index", "premium"}
        elif key == "query": valid = isinstance(value, str) and re.fullmatch(r"[A-Z0-9_-]{0,40}", value) is not None
        elif key == "changes":
            valid = isinstance(value, dict) and bool(value) and not set(value)-CONFIG_LIMITS.keys()
            if valid: valid = all(integer(v, *CONFIG_LIMITS[k]) for k, v in value.items())
        elif key in {"base_revision", "strategy_revision"}: valid = isinstance(value, str) and REVISION.fullmatch(value) is not None
        elif key == "branch": valid = isinstance(value, str) and re.fullmatch(r"codex/[a-z0-9][a-z0-9_-]{0,60}", value) is not None
        elif key == "task_ref": valid = isinstance(value, str) and re.fullmatch(r"issue:[1-9][0-9]{0,8}", value) is not None
        elif key == "run_ids": valid = isinstance(value, list) and 2 <= len(value) <= 10 and all(identifier(v) for v in value) and len(set(value)) == len(value)
        else: valid = identifier(value)
        if not valid: raise ValueError("invalid parameter: " + key)
    if "changes" in p and "expected_revision" not in p: raise ValueError("expected_revision required")
    return json.loads(canonical(payload))


def validate_result(result: object, command: CommandEnvelope) -> ResultEnvelope:
    bounded_json(result)
    if not isinstance(result, dict) or set(result) != {"schema_version", "data", "provenance", "artifacts"} or result["schema_version"] != "1.0": raise ValueError("invalid result")
    p = result["provenance"]
    if not isinstance(p, dict) or set(p) != {"snapshot_id", "source_ids", "coverage", "code_revision"}: raise ValueError("provenance required")
    if p["snapshot_id"] != command["snapshot_id"]: raise ValueError("snapshot mismatch")
    if not isinstance(p["source_ids"], list) or not 1 <= len(p["source_ids"]) <= 100 or not all(identifier(x) for x in p["source_ids"]): raise ValueError("source evidence required")
    if p["coverage"] not in ("complete", "partial", "unavailable") or not isinstance(p["code_revision"], str) or not REVISION.fullmatch(p["code_revision"]): raise ValueError("invalid provenance")
    if command["action"] == "backtest.run" and p["coverage"] != "complete": raise ValueError("simulation requires complete coverage")
    if not isinstance(result["artifacts"], list) or len(result["artifacts"]) > 100: raise ValueError("artifact limit")
    for a in result["artifacts"]:
        if not isinstance(a, dict) or set(a) != {"artifact_id", "sha256", "media_type", "size_bytes"} or not identifier(a["artifact_id"]) or not isinstance(a["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", a["sha256"]) or a["media_type"] not in {"application/json", "image/png", "text/plain"} or not integer(a["size_bytes"], 0, 100_000_000): raise ValueError("invalid artifact manifest")
    if command["action"] == "dev.change.request":
        data = result["data"]
        fields = {"mode", "branch", "base_revision", "head_revision", "pr_number", "tests_artifact_id"}
        if not isinstance(data, dict) or set(data) != fields or data["mode"] != "isolated-worktree-draft-pr" or any(data[k] != command["params"][k] for k in ("branch", "base_revision")) or not isinstance(data["head_revision"], str) or not REVISION.fullmatch(data["head_revision"]) or not integer(data["pr_number"], 1, 2**31-1) or data["tests_artifact_id"] not in [a["artifact_id"] for a in result["artifacts"]]: raise ValueError("isolated draft PR with test evidence required")
    return json.loads(canonical(result))


def validate_overlay(value):
    bounded_json(value)
    required = {"schema_version", "run_id", "revision", "venue", "category", "symbol", "interval", "start_ms", "end_ms", "availability", "coverage", "provenance", "markers", "levels", "next_cursor"}
    if not isinstance(value, dict) or set(value) != required or value["schema_version"] != "1.0": raise ValueError("invalid overlay")
    if value["venue"] != "bybit" or value["category"] not in ("spot", "linear", "inverse") or not isinstance(value["symbol"], str) or not SYMBOL.fullmatch(value["symbol"]) or value["interval"] not in tuple(TIMEFRAMES): raise ValueError("invalid market")
    if not identifier(value["run_id"]) or not integer(value["revision"], 0, 2**31-1): raise ValueError("invalid overlay identity")
    start, end = value["start_ms"], value["end_ms"]
    if not integer(start, 0, 2**53-1) or not integer(end, start+1, start+MAX_WINDOW_MS): raise ValueError("invalid range")
    if value["availability"] not in ("AVAILABLE", "PARTIAL", "UNAVAILABLE", "UNVERIFIED") or value["coverage"] not in ("complete", "partial", "unavailable"): raise ValueError("invalid coverage")
    if value["next_cursor"] is not None and not identifier(value["next_cursor"]): raise ValueError("invalid cursor")
    if not isinstance(value["provenance"], dict) or not identifier(value["provenance"].get("snapshot_id")): raise ValueError("pinned provenance required")
    validate_result({"schema_version":"1.0", "data":{}, "provenance":value["provenance"], "artifacts":[]}, {"snapshot_id":value["provenance"]["snapshot_id"], "action":"chart.overlay"})
    def price(v):
        if not isinstance(v, str) or not re.fullmatch(r"[0-9]{1,30}(\.[0-9]{1,18})?", v): raise ValueError("decimal string required")
        try:
            if not Decimal(v).is_finite() or Decimal(v) <= 0: raise ValueError("positive price required")
        except InvalidOperation as exc: raise ValueError("invalid decimal") from exc
    for collection in ("markers", "levels"):
        if not isinstance(value[collection], list) or len(value[collection]) > 500: raise ValueError("overlay page limit")
    for marker in value["markers"]:
        if not isinstance(marker, dict) or set(marker) != {"timestamp_ms", "kind", "price", "trade_id", "fill_id", "reason"}: raise ValueError("invalid marker")
        if not integer(marker["timestamp_ms"], start, end-1) or marker["kind"] not in ("fill", "signal"): raise ValueError("invalid marker range/kind")
        if marker["kind"] == "fill" and not all(identifier(marker[k]) for k in ("trade_id", "fill_id")): raise ValueError("persisted fill references required")
        if marker["kind"] == "signal" and (marker["fill_id"] is not None or marker["trade_id"] is not None): raise ValueError("signal cannot impersonate fill")
        if not identifier(marker["reason"]): raise ValueError("reason reference required")
        price(marker["price"])
    for level in value["levels"]:
        if not isinstance(level, dict) or set(level) != {"start_ms", "end_ms", "price", "kind", "rule_id"}: raise ValueError("invalid level")
        if not integer(level["start_ms"], start, end-1) or not integer(level["end_ms"], level["start_ms"]+1, end) or level["kind"] not in ("entry", "sl", "tp", "research") or not identifier(level["rule_id"]): raise ValueError("invalid level bounds")
        price(level["price"])
    return json.loads(canonical(value))
