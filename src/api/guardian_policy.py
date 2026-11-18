"""Pure fail-closed policy for four planned Tester guardian controllers.

Decision only: NEVER performs I/O, commands, database writes or retries. A later
executor must independently authorize and verify every proposed action.
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str


ALERT = Decision("ALERT", "manual_review_required")


def _int(value, minimum=0):
    return type(value) is int and value >= minimum


def decide(event: Mapping) -> Decision:
    """Classify trusted monitor evidence; unrecognized input never gets authority."""
    if not isinstance(event, Mapping):
        return ALERT
    if event.get("environment") != "tester_preview":
        return Decision("ALERT", "environment_out_of_scope")
    evidence = event.get("evidence_ref")
    if not isinstance(evidence, str) or not 0 < len(evidence) <= 256:
        return Decision("ALERT", "independent_evidence_missing")

    component, signal = event.get("component"), event.get("signal")
    owned = event.get("ownership_verified") is True

    if component == "code":
        if signal == "service_unhealthy":
            failures = event.get("consecutive_failed_probes")
            restarts = event.get("restarts_last_hour")
            if owned and _int(failures, 3) and _int(restarts) and restarts < 2:
                return Decision("PROPOSE_OWNED_RESTART", "failed_health_probes")
            return Decision("ALERT", "restart_budget_or_ownership_not_verified")
        if signal in {"unhandled_exception", "ci_regression"}:
            return Decision("REQUEST_CODE_REVIEW", "draft_only_no_hot_patch")

    if component == "data":
        if signal == "verified_gap":
            window = event.get("window_ms")
            if (
                owned
                and event.get("catalog_verified") is True
                and event.get("fenced_writer_verified") is True
                and event.get("source") == "bybit"
                and _int(window, 1)
                and window <= 86_400_000
            ):
                return Decision("PROPOSE_BOUNDED_BACKFILL", "observed_missing_window")
            return Decision("ALERT", "backfill_scope_not_verified")
        if signal in {"hash_conflict", "source_mismatch"}:
            if owned and event.get("independent_integrity_check") is True:
                return Decision("PROPOSE_QUARANTINE", "independent_conflict_evidence")
            return Decision("ALERT", "integrity_evidence_not_verified")

    if component == "storage":
        if signal == "aged_temp_file":
            age = event.get("age_seconds")
            if (
                owned
                and event.get("direct_part_in_owned_tmp") is True
                and event.get("reparse_checked") is True
                and _int(age, 86_400)
            ):
                return Decision("PROPOSE_TEMP_CLEANUP", "owned_aged_part_file")
            return Decision("ALERT", "cleanup_scope_not_verified")
        if signal == "low_disk":
            if event.get("disk_telemetry_verified") is True:
                return Decision("PROPOSE_PAUSE_DOWNLOADS", "low_disk_verified")
            return Decision("ALERT", "disk_telemetry_not_verified")

    if component == "exchange":
        if signal == "tls_verification_failed":
            return Decision("PROPOSE_STOP_SOURCE", "tls_fail_closed")
        if signal == "http_429":
            after = event.get("retry_after_seconds")
            if _int(after, 1) and after <= 3600:
                return Decision("PROPOSE_RATE_BACKOFF", "verified_retry_after")
            return Decision("ALERT", "unqualified_retry_after")
        if signal == "transient_timeout":
            attempts = event.get("attempts")
            if _int(attempts) and attempts < 3:
                return Decision("PROPOSE_BOUNDED_RETRY", "transient_source_timeout")
            return Decision("ALERT", "source_retry_budget_exhausted")
        if signal == "source_stale":
            return Decision("PROPOSE_MARK_UNAVAILABLE", "source_freshness_unqualified")

    return ALERT
