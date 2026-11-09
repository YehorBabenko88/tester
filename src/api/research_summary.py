"""Bounded digest-bound projection of verified F facts; no financial arithmetic."""

import copy
import json
import re
from typing import Annotated, Any, Literal

from pydantic import Field

from .research_control import Digest, Identifier, Revision, Strict, Timestamp, canonical_digest

MAX_SUMMARY_BYTES = 8 * 1024 * 1024


class SummaryLevel(Strict):
    entry_fill_id: Identifier
    kind: Literal["sl", "tp"]
    price: Annotated[str, Field(min_length=1, max_length=200)]
    start_ms: Timestamp
    end_ms: Timestamp
    known_at_ms: Timestamp


class Certification(Strict):
    scope: Literal["SYNTHETIC_ONLY"]
    real_market_coverage: Literal["NOT_PROVEN"]


class ResearchSummary(Strict):
    schema_version: Literal["tester.research-summary/1"]
    input_digest: Digest
    artifact_digest: Digest
    run_id: Identifier
    source_sha: Revision
    engine_version: Annotated[str, Field(min_length=1, max_length=100)]
    certification: Certification
    # Nested financial facts retain F's original schema/strings and are validated
    # by genuine producer replay, rather than a second financial interpretation.
    metrics: dict[str, Any]
    trades: Annotated[list[dict[str, Any]], Field(max_length=500)]
    equity_curve: Annotated[list[dict[str, Any]], Field(max_length=2001)]
    brackets: Annotated[list[dict[str, Any]], Field(max_length=2000)]
    levels: Annotated[list[SummaryLevel], Field(max_length=1000)]


class SummaryEnvelope(Strict):
    summary: ResearchSummary
    summary_digest: Digest


def _levels(result, chart):
    entries = {m["fill_id"]: m for m in chart["markers"] if m["role"] == "entry"}
    levels = []
    identities = {}
    for trade in result["trades"]:
        entry = entries.get(trade["entry_fill_id"])
        if entry is None:
            raise ValueError("summary entry must exist in bound chart")
        for kind, field in (("sl", "stop_loss"), ("tp", "take_profit")):
            identity = (entry["fill_id"], kind)
            level = dict(
                entry_fill_id=entry["fill_id"],
                kind=kind,
                price=trade[field],
                start_ms=entry["window_start_ms"],
                end_ms=entry["window_end_ms"],
                known_at_ms=entry["known_at_ms"],
            )
            if identity in identities:
                if identities[identity] != level:
                    raise ValueError("conflicting summary level")
                continue  # Partial exits share one unchanged entry bracket.
            identities[identity] = level
            levels.append(level)
    return levels


def project_summary(bundle, input_digest):
    from src.integration.strategy_lab import verify_research_bundle

    if not isinstance(input_digest, str) or not re.fullmatch("[a-f0-9]{64}", input_digest):
        raise ValueError("execution input binding required")
    if (
        len(json.dumps(bundle, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        > MAX_SUMMARY_BYTES
    ):
        raise ValueError("stored research bundle exceeds summary bound")
    # Replays exact pinned J/F. Rehashing forged output is never verification.
    verify_research_bundle(bundle)
    result, chart = bundle["result"], bundle["chart"]
    if (
        len(result["trades"]) > 500
        or len(result["equity_curve"]) > 2001
        or len(result["brackets"]) > 2000
    ):
        raise ValueError("bounded summary rows required")
    levels = _levels(result, chart)
    summary = dict(
        schema_version="tester.research-summary/1",
        input_digest=input_digest,
        artifact_digest=bundle["chart_digest"],
        run_id=result["run_id"],
        source_sha=bundle["source_sha"],
        engine_version=bundle["engine_version"],
        certification=copy.deepcopy(bundle["certification"]),
        metrics=copy.deepcopy(result["metrics"]),
        trades=copy.deepcopy(result["trades"]),
        equity_curve=copy.deepcopy(result["equity_curve"]),
        brackets=copy.deepcopy(result["brackets"]),
        levels=levels,
    )
    if (
        len(json.dumps(summary, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        > MAX_SUMMARY_BYTES
    ):
        raise ValueError("summary exceeds byte bound")
    envelope = SummaryEnvelope.model_validate(
        dict(summary=summary, summary_digest=canonical_digest(summary))
    ).model_dump(mode="json")
    if (
        len(json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        > MAX_SUMMARY_BYTES
    ):
        raise ValueError("summary envelope exceeds byte bound")
    return envelope
