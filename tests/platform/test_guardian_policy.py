from src.api.guardian_policy import decide, Decision

EVIDENCE = {"environment": "tester_preview", "evidence_ref": "sha256:verified"}


def event(component, signal, **fields):
    return {**EVIDENCE, "component": component, "signal": signal, **fields}


def test_unknown_and_foreign_events_fail_closed():
    assert decide(None).action == "ALERT"
    assert decide(event("something", "anything")).action == "ALERT"
    assert (
        decide({**event("code", "service_unhealthy"), "environment": "production"}).action
        == "ALERT"
    )
    assert decide({**event("code", "service_unhealthy"), "evidence_ref": ""}).action == "ALERT"


def test_code_restart_requires_ownership_and_budget():
    qualified = event(
        "code",
        "service_unhealthy",
        ownership_verified=True,
        consecutive_failed_probes=3,
        restarts_last_hour=0,
    )
    assert decide(qualified) == Decision("PROPOSE_OWNED_RESTART", "failed_health_probes")
    for overrides in (
        {"ownership_verified": False},
        {"consecutive_failed_probes": 2},
        {"restarts_last_hour": 2},
        {"restarts_last_hour": True},
    ):
        assert decide({**qualified, **overrides}).action == "ALERT"
    assert decide(event("code", "unhandled_exception")).action == "REQUEST_CODE_REVIEW"


def test_data_backfill_requires_verified_catalog_and_fencing():
    qualified = event(
        "data",
        "verified_gap",
        ownership_verified=True,
        source="bybit",
        catalog_verified=True,
        fenced_writer_verified=True,
        window_ms=60_000,
    )
    assert decide(qualified).action == "PROPOSE_BOUNDED_BACKFILL"
    for overrides in (
        {"fenced_writer_verified": False},
        {"catalog_verified": False},
        {"source": "unknown"},
        {"window_ms": 86_400_001},
    ):
        assert decide({**qualified, **overrides}).action == "ALERT"


def test_quarantine_only_with_independent_integrity_evidence():
    assert (
        decide(
            event(
                "data",
                "hash_conflict",
                ownership_verified=True,
                independent_integrity_check=True,
            )
        ).action
        == "PROPOSE_QUARANTINE"
    )
    assert decide(event("data", "hash_conflict")).action == "ALERT"


def test_cleanup_only_owned_old_direct_part_files():
    qualified = event(
        "storage",
        "aged_temp_file",
        ownership_verified=True,
        direct_part_in_owned_tmp=True,
        reparse_checked=True,
        age_seconds=86_400,
    )
    assert decide(qualified).action == "PROPOSE_TEMP_CLEANUP"
    assert decide({**qualified, "reparse_checked": False}).action == "ALERT"
    assert decide({**qualified, "age_seconds": 100}).action == "ALERT"
    assert decide({**qualified, "age_seconds": True}).action == "ALERT"
    assert decide(event("storage", "low_disk", disk_telemetry_verified=True)).action == (
        "PROPOSE_PAUSE_DOWNLOADS"
    )


def test_tls_failures_never_propose_tls_bypass():
    assert decide(event("exchange", "tls_verification_failed")).action == ("PROPOSE_STOP_SOURCE")


def test_rate_limit_and_retry_budgets():
    assert decide(event("exchange", "http_429", retry_after_seconds=30)).action == (
        "PROPOSE_RATE_BACKOFF"
    )
    assert decide(event("exchange", "http_429", retry_after_seconds=3601)).action == "ALERT"
    assert decide(event("exchange", "transient_timeout", attempts=2)).action == (
        "PROPOSE_BOUNDED_RETRY"
    )
    assert decide(event("exchange", "transient_timeout", attempts=3)).action == "ALERT"
