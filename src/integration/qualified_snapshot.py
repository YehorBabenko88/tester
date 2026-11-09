"""Typed, bounded B→F seam. Historical visibility is never inferred from event time.

Real canonical reads remain UNVERIFIED: current B does not prove historical
visibility, and this bridge has no authority to upgrade source evidence. The only
admitted input is the byte-pinned explicitly SYNTHETIC F fixture.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.backtest.quality import validate_snapshot
from src.strategy.dsl import digest, integer

SCHEMA = "tester.qualified-snapshot/1"
F_SOURCE_SHA = "998c5bc89d1b35c9ad3047b1e50dcb1295120e31"
MAX_BYTES = 16 * 1024 * 1024
MAX_BARS = 1000  # One B repeatable-read page only; never stitch transaction snapshots.
INTERVAL_MS = {
    "1": 60000,
    "3": 180000,
    "5": 300000,
    "15": 900000,
    "30": 1800000,
    "60": 3600000,
    "120": 7200000,
    "240": 14400000,
    "360": 21600000,
    "720": 43200000,
    "D": 86400000,
    "W": 604800000,
}


def _json(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode()) > MAX_BYTES:
        raise ValueError("snapshot_bytes_limit")
    return raw


@dataclass(frozen=True)
class SnapshotRequest:
    venue: str
    category: str
    symbol: str
    interval: str
    start_ms: int
    end_ms: int
    dataset_revision: str | None = None

    def __post_init__(self):
        if (
            self.venue != "bybit"
            or not isinstance(self.category, str)
            or self.category not in {"spot", "linear"}
            or not isinstance(self.symbol, str)
            or not re.fullmatch(r"[A-Z0-9-]{2,50}", self.symbol)
            or not isinstance(self.interval, str)
            or self.interval not in INTERVAL_MS
        ):
            raise ValueError("unsupported_snapshot_scope")
        integer(self.start_ms, 0, 2**53 - 1)
        integer(self.end_ms, self.start_ms + 1, 2**53 - 1)
        width = self.end_ms - self.start_ms
        if width % INTERVAL_MS[self.interval] or width > MAX_BARS * INTERVAL_MS[self.interval]:
            raise ValueError("single_page_range_limit")
        if self.dataset_revision is not None and (
            not isinstance(self.dataset_revision, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.dataset_revision)
        ):
            raise ValueError("invalid_dataset_revision")

    @property
    def scope(self):
        return dict(
            venue=self.venue,
            category=self.category,
            symbol=self.symbol,
            dataset="candles",
            interval=self.interval,
        )


@dataclass(frozen=True)
class QualifiedSnapshot:
    mode: Literal["SYNTHETIC", "REAL"]
    dataset_revision: str
    snapshot_digest: str | None
    evidence_digest: str
    admitted: bool
    reasons: tuple[str, ...]
    _snapshot_json: str | None
    _provenance_json: str

    @property
    def schema_version(self):
        return SCHEMA

    @property
    def synthetic(self):
        return self.mode == "SYNTHETIC"

    @property
    def certification(self):
        return "SYNTHETIC_ONLY" if self.synthetic and self.admitted else "UNVERIFIED"

    @property
    def snapshot_id(self):
        return (
            "synthetic:" + self.snapshot_digest
            if self.synthetic and self.snapshot_digest
            else "unverified:" + self.dataset_revision
        )

    @property
    def snapshot(self):
        return json.loads(self._snapshot_json) if self._snapshot_json is not None else None

    @property
    def provenance(self):
        return json.loads(self._provenance_json)

    def as_dict(self):
        return dict(
            schema_version=SCHEMA,
            mode=self.mode,
            snapshot_id=self.snapshot_id,
            snapshot_digest=self.snapshot_digest,
            dataset_revision=self.dataset_revision,
            evidence_digest=self.evidence_digest,
            admitted=self.admitted,
            reasons=list(self.reasons),
            certification=self.certification,
            snapshot=self.snapshot,
            provenance=self.provenance,
        )


def certified_fixture_snapshot() -> QualifiedSnapshot:
    """No engine execution or I/O beyond exact packaged producer input files."""
    root = Path(__file__).parent
    pin = json.loads((root / "F_SOURCE_PIN.json").read_bytes())
    if pin["source_sha"] != F_SOURCE_SHA:
        raise ValueError("fixture_source_pin_mismatch")
    values, hashes = [], {}
    for name in ("example.strategy.json", "example.snapshot.json"):
        relative = "src/integration/fixtures/f998/" + name
        raw = (root / "fixtures/f998" / name).read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        if checksum != pin["files"][relative]["sha256"]:
            raise ValueError("fixture_content_pin_mismatch")
        hashes[relative] = checksum
        values.append(json.loads(raw))
    strategy, snapshot = values
    # Existing F validator is sole shape/data-quality authority; no new finance.
    validated, warnings, gaps = validate_snapshot(snapshot, strategy)
    if gaps or not {"SYNTHETIC_DATA", "NON_CERTIFIED"} <= set(warnings):
        raise ValueError("fixture_quality_not_synthetic")
    if not 1 <= len(validated["candles"]) <= MAX_BARS:
        raise ValueError("fixture_rows_limit")
    proof = dict(
        source="SYNTHETIC",
        source_sha=F_SOURCE_SHA,
        fixture_hashes=hashes,
        coverage=dict(status="COMPLETE", scope="PINNED_FIXTURE_ONLY"),
        historical_visibility="SYNTHETIC_FIXTURE",
        real_market_coverage="NOT_PROVEN",
    )
    return QualifiedSnapshot(
        "SYNTHETIC",
        digest(proof),
        digest(snapshot),
        digest(proof),
        True,
        (),
        _json(snapshot),
        _json(proof),
    )


def verify_qualified_snapshot(value: QualifiedSnapshot | dict) -> QualifiedSnapshot:
    """Re-pin exact admitted synthetic input, including caller-rehashed documents.

    A checksum is not source certification. REAL inputs have no admitted path yet.
    Call only on server-resolved repository documents, never as client authorization.
    """
    supplied = value.as_dict() if isinstance(value, QualifiedSnapshot) else value
    supplied = json.loads(_json(supplied))
    expected = certified_fixture_snapshot()
    if supplied != expected.as_dict():
        raise ValueError("snapshot_not_exact_certified_synthetic_fixture")
    return expected


def read_canonical_snapshot(store, request: SnapshotRequest) -> QualifiedSnapshot:
    """Read one bounded B Store page with its full raw provenance; never upgrades it.

    Current B knowledge_time is UNAVAILABLE even for COMPLETE current coverage.
    No fetched_at/inserted_at/transaction clock is repurposed as known_at_ms.
    Real F input stays absent pending a separately reviewed historical admission.
    """
    if not isinstance(request, SnapshotRequest):
        raise ValueError("typed_snapshot_request_required")
    page = store.read_page(
        "candles",
        request.venue,
        request.category,
        request.symbol,
        request.start_ms,
        request.end_ms,
        request.interval,
        limit=MAX_BARS,
        max_bytes=8 * 1024 * 1024,
        max_archive_bytes=0,
    )
    page = json.loads(_json(page))  # Bound and copy before retaining any source facts.
    if not isinstance(page, dict) or page.get("scope") != request.scope:
        raise ValueError("canonical_scope_mismatch")
    reasons = {"real_source_not_admitted"}
    if page.get("outcome") != "OK":
        reasons.add("canonical_read_unavailable")
    if page.get("page_exhausted") is not True or page.get("next_cursor") is not None:
        reasons.add("single_page_limit")
    rows = page.get("rows")
    if not isinstance(rows, list) or not rows:
        reasons.add("no_price_evidence")
    elif len(rows) > MAX_BARS:
        raise ValueError("canonical_rows_limit")
    coverage = page.get("coverage", {})
    if (
        coverage.get("complete") is not True
        or coverage.get("state") != "COMPLETE"
        or not coverage.get("proof_refs")
        or coverage.get("start_ms") != request.start_ms
        or coverage.get("end_ms") != request.end_ms
    ):
        reasons.add("coverage_unknown")
    # Deliberate absolute gate: even supplied positive labels cannot create a new
    # historical visibility certification API beyond the reviewed B contract.
    reasons.add("historical_visibility_not_proven")
    content = {
        k: page.get(k)
        for k in (
            "scope",
            "rows",
            "observations",
            "run_links",
            "evidence",
            "coverage",
            "knowledge_time",
        )
    }
    revision = digest(content)
    if request.dataset_revision is not None and request.dataset_revision != revision:
        raise ValueError("dataset_revision_mismatch")
    proof = dict(
        canonical_page=page,
        requested_range=dict(start_ms=request.start_ms, end_ms=request.end_ms),
        historical_visibility="NOT_PROVEN",
        real_market_coverage="NOT_ADMITTED",
    )
    return QualifiedSnapshot(
        "REAL", revision, None, digest(proof), False, tuple(sorted(reasons)), None, _json(proof)
    )
