"""Hash-bound observed-grid receipts, never F admission or historical proof.

No I/O occurs here. Original HTTP bodies and exchange signatures are absent from
the public snapshot: their declared hashes cannot be independently recomputed.
"""

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from contracts.v1.validate import validate_record
from src.quality.candles import next_boundary

MAX_TIME = 9007199254740991


def _integer(value):
    if type(value) is not int or not 0 <= value <= MAX_TIME:
        raise ValueError("Explicit integer epoch milliseconds required")
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _unknown_coverage(value, start, end):
    if (not isinstance(value, dict) or value.get("status") != "UNKNOWN"
            or value.get("evidence_refs") != [] or type(value.get("start_ms")) is not int
            or type(value.get("end_ms")) is not int
            or (value["start_ms"], value["end_ms"]) != (start, end)):
        raise ValueError("Observed receipt requires original UNKNOWN coverage without proof refs")


def build_observed_grid_receipt(snapshot_bytes, *, expected_snapshot_sha256, venue, category,
                                symbol, interval, start_ms, end_ms, source_cutoff_ms):
    """Validate an observed public wrapper while keeping F admission NOT_READY.

    source_cutoff_ms is an input observation bound, not trusted visibility time.
    The supported current wrapper has PARTIAL availability and UNKNOWN coverage.
    Hash equality proves input integrity against the supplied pin, not exchange
    authenticity or exhaustive historical acquisition.
    """
    if (type(snapshot_bytes) is not bytes or len(snapshot_bytes) > 16 * 1024 * 1024
            or not isinstance(expected_snapshot_sha256, str)
            or not re.fullmatch("[0-9a-f]{64}", expected_snapshot_sha256)
            or hashlib.sha256(snapshot_bytes).hexdigest() != expected_snapshot_sha256):
        raise ValueError("Snapshot hash or size mismatch")
    start_ms, end_ms, cutoff = map(_integer, (start_ms, end_ms, source_cutoff_ms))
    if not start_ms < end_ms <= cutoff:
        raise ValueError("Observed bounds exceed input cutoff")
    value = json.loads(snapshot_bytes)
    query = value["query"]
    scope = dict(venue=venue, category=category, symbol=symbol, interval=interval)
    if (any(query.get(key) != expected for key, expected in scope.items())
            or type(query.get("start_ms")) is not int or type(query.get("end_ms")) is not int
            or (query["start_ms"], query["end_ms"]) != (start_ms, end_ms)
            or value["availability"] != "PARTIAL" or value["coverage_status"] != "UNKNOWN"):
        raise ValueError("Original snapshot scope or declared quality differs")
    captured = datetime.fromisoformat(value["captured_at"])
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError("Captured time requires explicit timezone")
    delta = captured.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    captured_ms = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
    if cutoff > _integer(captured_ms):
        raise ValueError("Input observation cutoff exceeds snapshot capture")
    records, pages = value["records"], value["api_pages"]
    if not isinstance(records, list) or not 1 <= len(records) <= 10000 or not isinstance(pages, list) or not pages:
        raise ValueError("Bounded nonempty observations and page receipts required")
    declared_pages = []
    for page in pages:
        _unknown_coverage(page["coverage"], start_ms, end_ms)
        if (type(page["records"]) is not int or not 1 <= page["records"] <= 1000
                or page["availability"].get("status") != "PARTIAL"
                or not isinstance(page["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", page["sha256"])):
            raise ValueError("Original page observation receipt invalid")
        declared_pages.append({key: copy.deepcopy(page[key]) for key in ("records", "sha256", "coverage", "availability")})
    if sum(page["records"] for page in pages) != len(records):
        raise ValueError("Page counts differ from observed records")
    provenance, provenance_index, bindings, quality = [], {}, [], {}
    expected_time = start_ms
    for record in records:
        validate_record("candle", record)
        following, aligned = next_boundary(record["timestamp_ms"], interval)
        if (any(record.get(key) != expected for key, expected in scope.items())
                or record["timestamp_ms"] != expected_time or record["start_ms"] != expected_time
                or not aligned or record["end_ms"] != following or following > end_ms
                or following > cutoff or record["is_closed"] is not True or record["kind"] != "official_trade"):
            raise ValueError("Observed closed grid is missing, duplicated, unordered or out of scope")
        prices = {key: Decimal(record[key]) for key in ("open", "high", "low", "close", "volume")}
        if (min(prices[key] for key in ("open", "high", "low", "close")) <= 0 or prices["volume"] < 0
                or prices["low"] > min(prices["open"], prices["close"])
                or prices["high"] < max(prices["open"], prices["close"]) or prices["low"] > prices["high"]):
            raise ValueError("Observed OHLCV is inconsistent")
        _unknown_coverage(record["coverage"], start_ms, end_ms)
        if record["availability"].get("status") != "PARTIAL" or not record["provenance"]:
            raise ValueError("Original observation quality or provenance missing")
        refs = []
        for source in record["provenance"]:
            parsed = urlsplit(source["source_ref"])
            params = parse_qs(parsed.query)
            if (source["source_kind"] != "bybit_rest" or parsed.scheme != "https"
                    or parsed.netloc != "api.bybit.com" or parsed.path != "/v5/market/kline"
                    or parsed.fragment or source["timestamp_precision"] != "ms"
                    or source["source_timestamp"] != str(record["timestamp_ms"])
                    or not isinstance(source["source_hash"], str) or not re.fullmatch("[0-9a-f]{64}", source["source_hash"])
                    or not isinstance(source["ingestion_run"], str) or not source["ingestion_run"]
                    or any(params.get(key) != [expected] for key, expected in (("category", category), ("symbol", symbol), ("interval", interval)))):
                raise ValueError("Original source scope, hash or timestamp cannot be bound")
            key = _digest(source)
            if key not in provenance_index:
                provenance_index[key] = len(provenance)
                provenance.append(copy.deepcopy(source))
            refs.append(provenance_index[key])
        bindings.append(dict(timestamp_ms=record["timestamp_ms"], provenance_indexes=refs))
        declared = dict(availability=record["availability"], coverage=record["coverage"])
        quality.setdefault(_digest(declared), copy.deepcopy(declared))
        expected_time = following
    if expected_time != end_ms:
        raise ValueError("Observed grid does not reach the requested end")
    return dict(schema_version="observed-grid-receipt/v1",
        evidence_scope="observed_grid_only_NOT_HISTORICAL_COMPLETENESS", historical_completeness="UNPROVEN",
        f_admission="NOT_READY", grid_checks_passed=True, observed_records=len(records), expected_grid_records=len(records),
        scope=scope, start_ms=start_ms, end_ms=end_ms, source_cutoff_ms=cutoff,
        source_cutoff_semantics="input_observation_cutoff_not_historical_visibility",
        snapshot_sha256=expected_snapshot_sha256, records_sha256=_digest(records),
        snapshot_kind=value.get("kind"), captured_at=value["captured_at"],
        declared_source_coverage_status=value["coverage_status"], declared_source_availability=value["availability"],
        declared_pages=declared_pages, declared_record_quality=list(quality.values()),
        provenance=provenance, record_provenance_bindings=bindings,
        source_hashes=sorted({source["source_hash"] for source in provenance}),
        integrity_scope="Hash-bound input snapshot; declared page/source hashes preserved, not recomputed from absent original HTTP bodies",
        historical_visibility_proven=False)
