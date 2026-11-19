"""Read-only, deterministic OHLCV reconciliation and bounded action proposals.

No database connection, SQL execution, network request, or queue mutation exists
in this module. All evidence is supplied by an independently controlled reader.
"""
import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

SHA = re.compile(r"^[0-9a-f]{64}$")
SYMBOL = re.compile(r"^[A-Z0-9]{2,32}$")
INTERVALS = {"1": 60_000, "3": 180_000, "5": 300_000, "15": 900_000,
             "30": 1_800_000, "60": 3_600_000, "120": 7_200_000,
             "240": 14_400_000, "360": 21_600_000, "720": 43_200_000,
             "D": 86_400_000}
MAX_WINDOW_MS = 86_400_000
MAX_ROWS = 1440


@dataclass(frozen=True)
class Finding:
    action: str
    reason: str
    scope: str = ""
    evidence_ref: str = ""
    proposal: dict | None = None


def _alert(reason, scope=""):
    return Finding("ALERT", reason, scope)


def _valid_sha(value):
    return type(value) is str and SHA.fullmatch(value) is not None


def _valid_int(value):
    return type(value) is int and value >= 0


def _positive(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value > 0
    except (OverflowError, ValueError):
        return False


def _nonnegative_finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except (OverflowError, ValueError):
        return False


def _scope(category, symbol, interval):
    return f"bybit/{category}/{symbol}/{interval}"


def reconcile(snapshot: Mapping, *, enabled: bool = False) -> Finding:
    """Validate a bounded independently captured snapshot; propose only.

    Snapshot must contain a verified immutable baseline and independently
    checked source hashes. A gap is actionable only with separate source proof,
    fenced queue ownership, and exact catalog identity. No side effects.
    """
    if not enabled:
        return _alert("guardians_disabled")
    if not isinstance(snapshot, Mapping):
        return _alert("invalid_snapshot")
    if snapshot.get("environment") != "tester_preview":
        return _alert("environment_out_of_scope")
    if snapshot.get("schema_verified") is not True:
        return _alert("postgresql_schema_unverified")
    if (snapshot.get("dsn_isolated") is not True
            or type(snapshot.get("postgres_port")) is not int
            or snapshot["postgres_port"] != 55432
            or snapshot.get("database_name") != "tester_preview"
            or snapshot.get("database_role") != "guardian_readonly"
            or snapshot.get("dsn_identity_verified") is not True):
        return _alert("database_isolation_unverified")
    if snapshot.get("read_only_snapshot") is not True:
        return _alert("snapshot_not_read_only")
    if snapshot.get("baseline_verified") is not True or snapshot.get("history_state") != "bounded_verified":
        return _alert("baseline_unavailable")
    category, symbol, interval = (
        snapshot.get("category"), snapshot.get("symbol"), snapshot.get("interval")
    )
    if category not in ("linear", "inverse", "spot") or (
        type(symbol) is not str or not SYMBOL.fullmatch(symbol)
    ) or type(interval) is not str or interval not in INTERVALS:
        return _alert("invalid_identity")
    scope = _scope(category, symbol, interval)
    start, end = snapshot.get("start_ms"), snapshot.get("end_ms")
    step = INTERVALS[interval]
    if (not _valid_int(start) or not _valid_int(end) or end <= start
            or end - start > MAX_WINDOW_MS or start % step or end % step):
        return _alert("invalid_bounded_window", scope)
    rows = snapshot.get("rows")
    if not isinstance(rows, (list, tuple)) or len(rows) > MAX_ROWS:
        return _alert("invalid_row_batch", scope)
    if (snapshot.get("source") != "bybit"
            or snapshot.get("source_verified") is not True
            or not _valid_sha(snapshot.get("source_evidence_sha256"))):
        return _alert("source_unverified", scope)
    if not _valid_sha(snapshot.get("baseline_sha256")):
        return _alert("baseline_hash_unverified", scope)

    keys = set()
    for row in rows:
        if not isinstance(row, Mapping):
            return _alert("invalid_record", scope)
        ts = row.get("open_time_ms")
        if not _valid_int(ts) or ts < start or ts >= end or (ts - start) % step:
            return _alert("record_out_of_window", scope)
        if ts in keys:
            return _conflict("duplicate_key", scope, snapshot, ts)
        keys.add(ts)
        if (row.get("source") != "bybit"
                or row.get("provenance_verified") is not True
                or row.get("category") != category
                or row.get("symbol") != symbol
                or row.get("interval") != interval):
            return _conflict("source_mismatch", scope, snapshot, ts)
        if not (_valid_sha(row.get("source_sha256"))
                and _valid_sha(row.get("observed_sha256"))
                and row["source_sha256"] == row["observed_sha256"]
                and row.get("original_projection_verified") is True):
            return _conflict("hash_or_projection_conflict", scope, snapshot, ts)
        o, h, low, close, vol = (
            row.get("open"), row.get("high"), row.get("low"),
            row.get("close"), row.get("volume")
        )
        if not all(_positive(x) for x in (o, h, low, close)):
            return _conflict("invalid_ohlc", scope, snapshot, ts)
        if not (low <= o <= h and low <= close <= h):
            return _conflict("invalid_ohlc", scope, snapshot, ts)
        if not _nonnegative_finite(vol):
            return _conflict("invalid_volume", scope, snapshot, ts)
        if row.get("closed") is not True:
            return _alert("unclosed_candle", scope)
        if row.get("close_time_ms") != ts + step - 1:
            return _conflict("candle_close_time_mismatch", scope, snapshot, ts)

    missing = [ts for ts in range(start, end, step) if ts not in keys]
    if not missing:
        return Finding("OBSERVED_CONSISTENT", "sample_consistent_not_full_history", scope,
                       _digest(scope, start, end, sorted(keys)))
    if (snapshot.get("source_gap_proof_verified") is not True
            or not _valid_sha(snapshot.get("source_gap_proof_sha256"))
            or snapshot.get("source_gap_proof_scope") != scope
            or snapshot.get("source_gap_proof_start_ms") != start
            or snapshot.get("source_gap_proof_end_ms") != end):
        return _alert("gap_source_proof_missing", scope)
    if (snapshot.get("catalog_verified") is not True
            or snapshot.get("catalog_handler") != "CatalogHandler"
            or snapshot.get("fenced_writer_verified") is not True
            or not _valid_int(snapshot.get("lease_epoch"))
            or snapshot.get("lease_epoch") == 0
            or snapshot.get("overlapping_writer") is not False
            or snapshot.get("queue_identity_verified") is not True
            or snapshot.get("lease_scope") != scope
            or not _valid_sha(snapshot.get("lease_evidence_sha256"))):
        return _alert("catalog_or_fencing_unverified", scope)
    if len(missing) > MAX_ROWS:
        return _alert("gap_budget_exceeded", scope)
    evidence = _digest(scope, start, end, missing)
    return Finding(
        "PROPOSE_BOUNDED_BACKFILL", "verified_missing_closed_candles", scope, evidence,
        {"adapter": "CatalogHandler", "category": category, "symbol": symbol,
         "history_complete": False, "preserve_raw": True,
         "interval": interval, "start_ms": min(missing),
         "end_ms_exclusive": max(missing) + step,
         "missing_open_times_ms": missing, "lease_epoch": snapshot["lease_epoch"],
         "idempotency_key": evidence, "requires_independent_after_check": True,
         "execution_authorized": False},
    )


def _digest(scope, start, end, timestamps):
    payload = json.dumps([scope, start, end, timestamps], separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _conflict(reason, scope, snapshot, timestamp):
    """Propose only after an independent conflict probe, keyed per candle."""
    if snapshot.get("independent_integrity_check") is not True:
        return _alert("integrity_evidence_not_verified", scope)
    evidence = _digest(
        scope, snapshot["start_ms"], snapshot["end_ms"],
        ["conflict/v2", reason, timestamp, snapshot["baseline_sha256"]],
    )
    return Finding(
        "PROPOSE_QUARANTINE", reason, scope, evidence,
        {"scope": scope, "affected_open_time_ms": timestamp,
         "evidence_ref": evidence, "history_complete": False,
         "execution_authorized": False, "preserve_raw": True},
    )


def assess_data_removal_receipt(evidence: Mapping, *, enabled: bool = False) -> Finding:
    """Evaluate Data-only deletion evidence after MASTER's independent gate.

    Checks only source-data, database, and checkpoint absence. MASTER owns the
    installer, DELETE TESTER confirmation, service lifecycle, shortcuts and paths;
    Storage owns reparse safety. This function cannot delete anything.
    """
    if not enabled:
        return _alert("guardians_disabled")
    if not isinstance(evidence, Mapping):
        return _alert("invalid_removal_evidence")
    if evidence.get("environment") != "disposable_windows_fixture":
        return _alert("removal_fixture_required")
    if (evidence.get("master_gate_verified") is not True
            or not _valid_sha(evidence.get("master_gate_receipt_sha256"))):
        return _alert("master_uninstall_gate_unverified")
    if evidence.get("independent_data_readback_verified") is not True:
        return _alert("independent_data_readback_missing")
    if (evidence.get("dedicated_cluster_identity_verified") is not True
            or evidence.get("system_pg_identity_unchanged") is not True
            or evidence.get("system_pg_data_hash_unchanged") is not True):
        return _alert("database_identity_unverified")
    required = (
        "dedicated_cluster_absent",
        "raw_candles_absent",
        "history_absent",
        "original_projections_absent",
        "provenance_absent",
        "source_hashes_absent",
        "test_results_absent",
        "checkpoints_absent",
        "download_queue_absent",
        "data_backups_absent",
    )
    if any(evidence.get(field) is not True for field in required):
        return _alert("data_removal_incomplete_or_unverified")
    if not _valid_sha(evidence.get("data_receipt_sha256")):
        return _alert("data_receipt_missing")
    return Finding(
        "OBSERVED_DATA_REMOVAL_FIXTURE",
        "data_fixture_only_not_production_certification",
    )


def to_policy_event(snapshot: Mapping, finding: Finding) -> dict | None:
    """Adapt a trusted *proposal* to the foundation policy's event schema.

    Produces data-only metadata; cannot authorize, enqueue, execute or write.
    Returns None on ambiguous scope, untrusted evidence or non-proposal results.
    """
    if not isinstance(snapshot, Mapping) or not isinstance(finding, Finding):
        return None
    if (finding.action not in {"PROPOSE_BOUNDED_BACKFILL", "PROPOSE_QUARANTINE"}
            or not isinstance(finding.proposal, Mapping)
            or finding.proposal.get("execution_authorized") is not False
            or not _valid_sha(finding.evidence_ref.removeprefix("sha256:"))
            or snapshot.get("environment") != "tester_preview"
            or snapshot.get("ownership_verified") is not True):
        return None
    category, symbol, interval = (
        snapshot.get("category"), snapshot.get("symbol"), snapshot.get("interval")
    )
    if (category not in ("linear", "inverse", "spot")
            or type(symbol) is not str or not SYMBOL.fullmatch(symbol)
            or type(interval) is not str or interval not in INTERVALS
            or finding.scope != _scope(category, symbol, interval)):
        return None
    event = {
        "component": "data",
        "environment": "tester_preview",
        "scope": finding.scope,
        "source": "bybit",
        "evidence_ref": finding.evidence_ref,
        "ownership_verified": True,
    }
    if finding.action == "PROPOSE_BOUNDED_BACKFILL":
        if (snapshot.get("catalog_verified") is not True
                or snapshot.get("fenced_writer_verified") is not True
                or finding.proposal.get("adapter") != "CatalogHandler"):
            return None
        start, end = snapshot.get("start_ms"), snapshot.get("end_ms")
        if (not _valid_int(start) or not _valid_int(end)
                or not 0 < end - start <= MAX_WINDOW_MS):
            return None
        event.update(
            signal="verified_gap",
            window_ms=end - start,
            catalog_verified=True,
            fenced_writer_verified=True,
        )
    else:
        if snapshot.get("independent_integrity_check") is not True:
            return None
        event.update(
            signal=(
                "source_mismatch" if finding.reason == "source_mismatch"
                else "hash_conflict"
            ),
            independent_integrity_check=True,
        )
    return event
