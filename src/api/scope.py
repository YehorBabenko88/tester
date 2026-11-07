"""Request binding beyond immutable canonical validation; no analytics."""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta

from contracts.v1.validate import ContractError

from .canonical import dataset_response


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ScopeError(ContractError):
    def __init__(self, violation, value):
        self.context = {
            "violation": violation,
            "availability": {"status": "UNAVAILABLE", "reason": "provider_scope_mismatch"},
            "upstream_availability": value["availability"]["status"],
            "upstream_reason_sha256": digest(value["availability"]["reason"])
            if value["availability"]["reason"] is not None
            else None,
            "source_count": len(value["provenance"]),
            "provenance": [
                dict(
                    source_kind=p["source_kind"],
                    source_hash=p["source_hash"],
                    source_ref_sha256=digest(p["source_ref"]),
                    ingestion_run_sha256=digest(p["ingestion_run"]),
                )
                for p in value["provenance"][:16]
            ],
        }
        super().__init__("Provider response does not match requested scope")


def _bar_interval(start, end, interval):
    # Time-boundary validation only, not C price/volume aggregation.
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start < end
        or not isinstance(interval, str)
    ):
        return False
    if interval in {"1", "3", "5", "15", "30", "60", "120", "240", "360", "720"}:
        step = int(interval) * 60000
        return start % step == 0 and end - start == step
    if interval == "D":
        return start % 86400000 == 0 and end - start == 86400000
    if interval == "W":
        # 1970-01-05 is the first nonnegative Monday UTC in the Unix epoch.
        return (start - 345600000) % 604800000 == 0 and end - start == 604800000
    if interval == "M":
        try:
            epoch = datetime(1970, 1, 1, tzinfo=UTC)
            begin = epoch + timedelta(milliseconds=start)
            finish = epoch + timedelta(milliseconds=end)
            return (
                begin.day == finish.day == 1
                and (begin.hour, begin.minute, begin.second, begin.microsecond) == (0, 0, 0, 0)
                and (finish.hour, finish.minute, finish.second, finish.microsecond) == (0, 0, 0, 0)
                and finish.year * 12 + finish.month == begin.year * 12 + begin.month + 1
            )
        except (ValueError, OverflowError, OSError):
            return False
    return False


class SourceIntervalError(ValueError):
    pass


def resource_query(resource, query):
    """Resolve native source cadence; display changes never resample source observations."""
    source = query.source_interval
    if resource == "funding":
        if source not in {None, "event"}:
            raise SourceIntervalError("Funding source interval must be event")
        source = "event"
    elif resource == "open-interest":
        cadences = {"5min", "15min", "30min", "1h", "4h", "1d"}
        if source is None:
            source = "5min"
        if source not in cadences:
            raise SourceIntervalError("Unsupported open-interest source cadence")
    else:
        if source is not None and source != query.interval:
            raise SourceIntervalError("Source interval differs from requested bar interval")
        source = query.interval
    return query.model_copy(
        update={"interval": source, "source_interval": source, "viewport_interval": query.interval}
    )


def bound_response(resource, kind, value, query):
    dataset_response(kind, value)

    def require(condition, violation):
        if not condition:
            raise ScopeError(violation, value)

    def coverage(bounds, *, envelope=False):
        require(type(bounds["start_ms"]) is int and type(bounds["end_ms"]) is int, "coverage")
        if envelope:
            require(
                (bounds["start_ms"], bounds["end_ms"]) == (query.start_ms, query.end_ms), "coverage"
            )
        else:
            require(
                query.start_ms <= bounds["start_ms"] < bounds["end_ms"] <= query.end_ms, "coverage"
            )

    coverage(value["coverage"], envelope=True)
    require(len(value["data"]) <= query.limit, "limit")
    expected_kind = {"open-interest": "oi", "funding": "funding", "candles": query.candle_kind}.get(
        resource
    )
    for row in value["data"]:
        if kind == "coverage":
            coverage(row)
            continue
        if kind == "source":
            # Source capabilities have no market identity/time fields in shared v1.
            continue
        require(row["venue"] == query.venue, "venue")
        require(row["category"] == query.category, "category")
        require(query.symbol is None or row["symbol"] == query.symbol, "symbol")
        require(type(row["timestamp_ms"]) is int, "timestamp")
        require(query.start_ms <= row["timestamp_ms"] < query.end_ms, "timestamp")
        coverage(row["coverage"])
        require(
            row["coverage"]["start_ms"] <= row["timestamp_ms"] < row["coverage"]["end_ms"],
            "coverage",
        )
        if expected_kind:
            require(row["kind"] == expected_kind, "resource")
        if kind in {"candle", "observation"}:
            require(row["interval"] == query.interval, "interval")
        if kind in {"candle", "footprint"}:
            require(query.start_ms <= row["start_ms"] < row["end_ms"] <= query.end_ms, "bar_range")
            require(row["timestamp_ms"] == row["start_ms"], "timestamp")
            require(_bar_interval(row["start_ms"], row["end_ms"], query.interval), "interval")
    return value


class CursorError(ValueError):
    pass


class CursorCodec:
    """Per-app HMAC binding. Restarts invalidate page tokens, not job checkpoints."""

    def __init__(self):
        self._key = secrets.token_bytes(32)

    def _scope(self, resource, query):
        scope = {"resource": resource, **query.model_dump(exclude={"cursor", "viewport_interval"})}
        return digest(json.dumps(scope, sort_keys=True, separators=(",", ":")))

    def encode(self, resource, query, cursor):
        if cursor is None:
            return None
        if not isinstance(cursor, str) or not cursor or len(cursor) > 2048:
            raise ContractError("Invalid provider continuation cursor")
        payload = json.dumps(
            {"scope": self._scope(resource, query), "cursor": cursor},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signed = hmac.digest(self._key, payload, "sha256")
        token = "e1." + base64.urlsafe_b64encode(payload).decode() + "." + signed.hex()
        if len(token) > 4096:
            raise ContractError("Provider continuation cursor exceeds transport budget")
        return token

    def decode(self, resource, query):
        token = query.cursor
        if token is None:
            return None
        try:
            prefix, payload, signature = token.split(".")
            if prefix != "e1" or len(token) > 4096:
                raise ValueError
            data = base64.b64decode(payload, altchars=b"-_", validate=True)
            if not hmac.compare_digest(
                hmac.digest(self._key, data, "sha256"), bytes.fromhex(signature)
            ):
                raise ValueError
            value = json.loads(data)
            if (
                value["scope"] != self._scope(resource, query)
                or not isinstance(value["cursor"], str)
                or not 1 <= len(value["cursor"]) <= 2048
            ):
                raise ValueError
            return value["cursor"]
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise CursorError("Invalid, stale, or cross-scope continuation token") from None
