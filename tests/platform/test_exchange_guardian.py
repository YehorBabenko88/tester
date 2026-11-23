"""Offline negative and recovery acceptance for Exchange Guardian."""

import inspect
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from src.api.guardians import exchange_guardian
from src.api.guardians.exchange_guardian import (
    MAX_ENDPOINTS,
    ExchangeGuardian,
    Observation,
    endpoint_key,
    retry_after_seconds,
    supervisor_event,
)

URL = "https://api.bybit.com/v5/market/kline"
DIGEST = "sha256:" + "a" * 64


def obs(kind="http", **kwargs):
    defaults = dict(
        endpoint=URL,
        kind=kind,
        elapsed_ms=50,
        evidence_ref=DIGEST,
        status_code=200 if kind == "http" else None,
        source_age_seconds=10,
        source_verified=True,
    )
    defaults.update(kwargs)
    return Observation(**defaults)


def test_disabled_default():
    assert ExchangeGuardian().inspect(obs(), now=1).status == "DISABLED"


@pytest.mark.parametrize(
    "url",
    [
        "http://api.bybit.com/v5/market/kline",
        "https://evil.com/v5/market/kline",
        "https://api.bybit.com.evil.com/v5/market/kline",
        "https://user:secret@api.bybit.com/v5/market/kline",
        "https://api.bybit.com:444/v5/market/kline",
        "https://api.bybit.com/v5/order/create",
        "https://api.bybit.com/v5/market/kline?api_key=secret",
        "https://api.bybit.com/v5/market/kline#fragment",
    ],
)
def test_endpoint_allowlist(url):
    with pytest.raises(ValueError):
        endpoint_key(url)


def test_429_respects_retry_after_and_blocks_cooldown():
    g = ExchangeGuardian(enabled=True)
    first = g.inspect(obs(status_code=429, retry_after="30"), now=100)
    assert first.proposal == "PROPOSE_RATE_BACKOFF"
    assert first.retry_at == 130
    assert g.inspect(obs(evidence_ref="sha256:" + "b" * 64), now=101).reason == "backoff_active"


def test_invalid_429_opens_circuit_without_retry():
    g = ExchangeGuardian(enabled=True)
    result = g.inspect(obs(status_code=429, retry_after="garbage"), now=0)
    assert result.proposal == "NONE"
    assert result.circuit == "OPEN"


@pytest.mark.parametrize("value", ["0", "-1", "3601", "nan", "1.5", "999999999", ""])
def test_bad_retry_after(value):
    assert retry_after_seconds(value, datetime.now(UTC)) is None


def test_http_date_retry_after():
    now = datetime(2026, 11, 18, tzinfo=UTC)
    assert retry_after_seconds(format_datetime(now + timedelta(seconds=20)), now) == 20


def test_timeout_budget_and_open_circuit():
    g = ExchangeGuardian(enabled=True)
    for i in range(3):
        result = g.inspect(obs("timeout", evidence_ref="sha256:" + str(i) * 64), now=[0, 2, 5][i])
    assert result.status == "UNAVAILABLE"
    assert result.circuit == "OPEN"
    assert result.proposal == "NONE"


def test_tls_halts_until_manual_reset():
    g = ExchangeGuardian(enabled=True)
    assert g.inspect(obs("tls_failure"), now=0).proposal == "PROPOSE_STOP_SOURCE"
    assert g.inspect(obs(evidence_ref="sha256:" + "b" * 64), now=9999).circuit == "HALTED"
    assert not g.reset_tls_halt(URL, manual_authorized=False)
    assert g.reset_tls_halt(URL, manual_authorized=True)
    assert g.inspect(obs(evidence_ref="sha256:" + "c" * 64), now=10000).status == "MONITORING"


@pytest.mark.parametrize(
    "kind,code", [("malformed", None), ("http", 500), ("http", 403), ("http", 302)]
)
def test_non_success_never_verifies(kind, code):
    g = ExchangeGuardian(enabled=True)
    result = g.inspect(obs(kind, status_code=code), now=0)
    assert result.status != "MONITORING"
    assert not result.history_complete


def test_stale_and_unverified_source():
    for change in (
        {"source_age_seconds": 200},
        {"source_verified": False},
        {"source_age_seconds": None},
    ):
        result = ExchangeGuardian(enabled=True).inspect(obs(**change), now=0)
        assert result.signal == "source_stale"
        assert result.proposal == "PROPOSE_MARK_UNAVAILABLE"


def test_restored_transport_never_claims_history():
    g = ExchangeGuardian(enabled=True)
    g.inspect(obs("timeout"), now=0)
    recovered = g.inspect(obs(evidence_ref="sha256:" + "b" * 64), now=2)
    assert recovered.status == "MONITORING"
    assert recovered.history_complete is False
    assert recovered.gaps_reconciled is False


def test_duplicate_and_invalid_evidence():
    g = ExchangeGuardian(enabled=True)
    g.inspect(obs(), now=0)
    assert g.inspect(obs(), now=1).reason == "duplicate_evidence"
    assert g.inspect(obs(evidence_ref="bad"), now=2).proposal == "NONE"


def test_independent_endpoint_circuits():
    g = ExchangeGuardian(enabled=True)
    g.inspect(obs("tls_failure"), now=0)
    other = obs(
        endpoint="https://api.bybit.com/v5/market/orderbook", evidence_ref="sha256:" + "b" * 64
    )
    assert g.inspect(other, now=0).status == "MONITORING"


def test_no_transport_or_storage_side_effects(monkeypatch):
    import socket

    monkeypatch.setattr(
        socket, "create_connection", lambda *a, **k: pytest.fail("network not allowed")
    )
    g = ExchangeGuardian(enabled=True)
    assert g.inspect(obs(), now=0).status == "MONITORING"


def test_tls_fault_preempts_rate_limit_cooldown():
    g = ExchangeGuardian(enabled=True)
    g.inspect(obs(status_code=429, retry_after="300"), now=10)
    fault = g.inspect(obs("tls_failure", evidence_ref="sha256:" + "d" * 64), now=11)
    assert fault.circuit == "HALTED"
    assert fault.proposal == "PROPOSE_STOP_SOURCE"
    assert g.inspect(obs(evidence_ref="sha256:" + "e" * 64), now=10000).circuit == "HALTED"


def test_archive_url_matches_transport_host_policy():
    assert endpoint_key("https://public.bybit.com/trading/BTCUSDT/BTCUSDT2026-01-01.csv.gz") == (
        "public.bybit.com/trading/BTCUSDT/BTCUSDT2026-01-01.csv.gz"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://api.bybit.com/trading/BTCUSDT/file.csv.gz",
        "https://public.bybit.com/tradingevil/file.csv.gz",
        "https://public.bybit.com/v5/order/create",
        "https://public.bybit.com/trading/BTCUSDT/file.csv.gz?token=abc",
        "https://public.bybit.com:443/trading/BTCUSDT/file.csv.gz",
    ],
)
def test_archive_scope_denials(url):
    with pytest.raises(ValueError):
        endpoint_key(url)


def test_http_429_cannot_shorten_active_backoff():
    g = ExchangeGuardian(enabled=True)
    first = g.inspect(obs(status_code=429, retry_after="180"), now=100)
    second = g.inspect(
        obs(status_code=429, retry_after="1", evidence_ref="sha256:" + "f" * 64), now=101
    )
    assert first.retry_at == 280
    assert second.retry_at == 280
    assert second.proposal == "NONE"


def test_malformed_200_never_healthy():
    result = ExchangeGuardian(enabled=True).inspect(obs(status_code=200, parse_valid=False), now=10)
    assert result.status == "UNAVAILABLE"
    assert result.circuit == "OPEN"
    assert not result.history_complete


def test_500_backoff_bounded():
    g = ExchangeGuardian(enabled=True)
    for i, now in enumerate((0, 2, 5)):
        r = g.inspect(obs(status_code=500, evidence_ref="sha256:" + str(i) * 64), now=now)
        assert r.attempts == i + 1
        assert r.proposal == ("NONE" if i == 2 else "PROPOSE_BOUNDED_RETRY")
    assert r.circuit == "OPEN"


def test_healthy_200_cannot_verify_history_even_after_429():
    g = ExchangeGuardian(enabled=True)
    g.inspect(obs(status_code=429, retry_after="2"), now=0)
    r = g.inspect(obs(evidence_ref="sha256:" + "b" * 64), now=3)
    assert r.status == "MONITORING"
    assert r.history_complete is False and r.gaps_reconciled is False


def test_supervisor_policy_bridge_429_and_timeout():
    from src.api.guardian_policy import decide

    g = ExchangeGuardian(enabled=True)
    first = g.inspect(obs(status_code=429, retry_after="20"), now=0)
    assert (
        decide(
            supervisor_event(
                obs(status_code=429, retry_after="20"), first, now=0, ownership_verified=True
            )
        ).action
        == "PROPOSE_RATE_BACKOFF"
    )

    timeout = ExchangeGuardian(enabled=True).inspect(obs("timeout"), now=0)
    assert decide(
        supervisor_event(obs("timeout"), timeout, now=0, ownership_verified=True)
    ).action == ("PROPOSE_BOUNDED_RETRY")


def test_invalid_time_and_evidence_fail_closed():
    g = ExchangeGuardian(enabled=True)
    assert g.inspect(obs(), now=float("nan")).proposal == "NONE"
    assert g.inspect(obs(evidence_ref="sha256:" + "g" * 64), now=1).proposal == "NONE"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_fault_injection_server_errors_bounded(status):
    g = ExchangeGuardian(enabled=True)
    results = [
        g.inspect(obs(status_code=status, evidence_ref="sha256:" + str(i) * 64), now=t)
        for i, t in enumerate((0, 2, 5))
    ]
    assert [r.proposal for r in results] == [
        "PROPOSE_BOUNDED_RETRY",
        "PROPOSE_BOUNDED_RETRY",
        "NONE",
    ]
    assert results[-1].circuit == "OPEN"
    assert results[-1].history_complete is False


def test_fault_injection_429_http_date_and_recovery():
    utc = datetime(2026, 11, 18, tzinfo=UTC)
    g = ExchangeGuardian(enabled=True)
    delayed = g.inspect(
        obs(status_code=429, retry_after=format_datetime(utc + timedelta(seconds=45))),
        now=100,
        now_utc=utc,
    )
    assert delayed.retry_at == 145
    assert g.inspect(obs(evidence_ref="sha256:" + "b" * 64), now=144).proposal == "NONE"
    recovered = g.inspect(obs(evidence_ref="sha256:" + "c" * 64), now=145)
    assert recovered.status == "MONITORING"
    assert not recovered.history_complete
    assert not recovered.gaps_reconciled


def test_fault_injection_tls_during_timeout_cooldown():
    g = ExchangeGuardian(enabled=True)
    g.inspect(obs("timeout"), now=10)
    halted = g.inspect(obs("tls_failure", evidence_ref="sha256:" + "b" * 64), now=10.1)
    assert halted.circuit == "HALTED"
    assert halted.proposal == "PROPOSE_STOP_SOURCE"
    assert g.inspect(obs(evidence_ref="sha256:" + "c" * 64), now=999999).status == "UNAVAILABLE"


def test_repeated_429_never_shortens_backoff():
    g = ExchangeGuardian(enabled=True)
    first = g.inspect(obs(status_code=429, retry_after="100"), now=10)
    blocked = g.inspect(
        obs(status_code=429, retry_after="1", evidence_ref="sha256:" + "b" * 64), now=11
    )
    assert blocked.retry_at >= first.retry_at
    assert blocked.proposal == "NONE"


def test_unknown_response_type_and_invalid_freshness_fail_closed():
    g = ExchangeGuardian(enabled=True)
    assert g.inspect(obs(kind="redirect"), now=0).proposal == "NONE"
    assert g.inspect(obs(freshness_limit_seconds=0), now=0).status == "UNAVAILABLE"
    assert g.inspect(obs(source_age_seconds=-1), now=0).status == "UNAVAILABLE"


def test_fault_injection_monotonic_clock_rollback_halts_circuit():
    g = ExchangeGuardian(enabled=True)
    first = g.inspect(obs(status_code=429, retry_after="60"), now=100)
    assert first.retry_at == 160
    rolled = g.inspect(obs(evidence_ref="sha256:" + "b" * 64), now=99)
    assert rolled.status == "UNAVAILABLE"
    assert rolled.circuit == "HALTED"
    assert rolled.proposal == "NONE"
    assert not g.reset_tls_halt(URL, manual_authorized=False)


def test_fault_injection_duplicate_clock_time_is_not_rollback():
    g = ExchangeGuardian(enabled=True)
    assert g.inspect(obs(), now=100).status == "MONITORING"
    second = g.inspect(obs(evidence_ref="sha256:" + "b" * 64), now=100)
    assert second.status == "MONITORING"
    assert not second.history_complete


def test_fault_injection_halted_circuit_never_reproposes_stop():
    g = ExchangeGuardian(enabled=True)
    first = g.inspect(obs("tls_failure"), now=1)
    assert first.proposal == "PROPOSE_STOP_SOURCE"
    second = g.inspect(obs("tls_failure", evidence_ref="sha256:" + "b" * 64), now=2)
    assert second.circuit == "HALTED"
    assert second.proposal == "NONE"
    third = g.inspect(obs(evidence_ref="sha256:" + "c" * 64), now=3)
    assert third.proposal == "NONE"
    assert not third.history_complete


def test_adminit_offline_launcher_default_denies_all_proposals():
    guardian = ExchangeGuardian()
    for observation in (
        obs(),
        obs("timeout"),
        obs("tls_failure"),
        obs(status_code=429, retry_after="30"),
        obs(status_code=500),
        obs(status_code=200, parse_valid=False),
    ):
        verdict = guardian.inspect(observation, now=100)
        assert verdict.status == "DISABLED"
        assert verdict.proposal == "NONE"
        assert verdict.history_complete is False


def test_adminit_read_only_transport_has_no_trading_capabilities():
    source = inspect.getsource(exchange_guardian)
    for forbidden in (
        "requests.post(",
        "subprocess.run(",
        "os.system(",
        "DROP DATABASE",
        "DELETE FROM",
        "verify=False",
        "allow_redirects=True",
    ):
        assert forbidden not in source


def test_adminit_offline_rejects_private_and_trading_endpoints():
    for address in (
        "https://api.bybit.com/v5/order/create",
        "https://api.bybit.com/v5/position/list",
        "https://api.bybit.com/v5/account/wallet-balance",
        "https://public.bybit.com/trading/../v5/order/create",
        "https://localhost/v5/market/kline",
        "https://127.0.0.1/v5/market/kline",
    ):
        with pytest.raises(ValueError):
            endpoint_key(address)


@pytest.mark.parametrize(
    "url",
    [
        "https://public.bybit.com/trading/%2e%2e/v5/order/create",
        "https://public.bybit.com/trading/%252e%252e/file.csv",
        "https://public.bybit.com/trading/BTCUSDT/%2fhidden.csv",
        "https://public.bybit.com/trading/BTCUSDT\\..\\bad.csv",
        "https://api.bybit.com/v5/market/%2E%2E/order/create",
    ],
)
def test_fault_injection_encoded_or_backslash_path_fail_closed(url):
    with pytest.raises(ValueError):
        endpoint_key(url)


def test_fault_injection_endpoint_cardinality_budget():
    guardian = ExchangeGuardian(enabled=True)
    for index in range(MAX_ENDPOINTS):
        result = guardian.inspect(
            obs(endpoint=f"https://public.bybit.com/trading/BTCUSDT/fixture{index}.csv.gz"),
            now=1,
        )
        assert result.status == "MONITORING"
    overflow = guardian.inspect(
        obs(endpoint="https://public.bybit.com/trading/BTCUSDT/one-extra.csv.gz"), now=2
    )
    assert overflow.status == "UNAVAILABLE"
    assert overflow.signal == "invalid_observation"
    assert overflow.proposal == "NONE"
    assert len(guardian._state) == MAX_ENDPOINTS


def test_integration_supervisor_dedupes_guardian_rate_proposal():
    guardian_module = pytest.importorskip("src.api.guardian_supervisor")
    supervisor_class = guardian_module.GuardianSupervisor
    observation = obs(status_code=429, retry_after="60")
    guardian = ExchangeGuardian(enabled=True)
    verdict = guardian.inspect(observation, now=100)
    event = supervisor_event(observation, verdict, now=100, ownership_verified=True)
    disabled = supervisor_class().propose(event)
    assert disabled.allowed is False
    assert disabled.reason == "guardians_disabled"

    supervisor = supervisor_class(enabled=True)
    first = supervisor.propose(event)
    assert first.allowed is True
    assert first.action == "PROPOSE_RATE_BACKOFF"
    second = supervisor.propose(event)
    assert second.allowed is False
    assert second.reason == "duplicate_event"


def test_integration_unattested_supervisor_event_never_authorizes():
    observed = obs(status_code=429, retry_after="15")
    result = ExchangeGuardian(enabled=True).inspect(observed, now=50)
    untrusted = supervisor_event(observed, result, now=50)
    assert untrusted["ownership_verified"] is False
    assert untrusted["signal"] == "untrusted_observation"
    from src.api.guardian_policy import decide

    assert decide(untrusted).action == "ALERT"


def test_integration_mismatched_evidence_cannot_claim_ownership():
    from dataclasses import replace

    observed = obs(status_code=429, retry_after="20")
    result = ExchangeGuardian(enabled=True).inspect(observed, now=5)
    tampered = replace(result, evidence_ref="sha256:" + "f" * 64)
    event = supervisor_event(observed, tampered, now=5, ownership_verified=True)
    assert event["signal"] == "untrusted_observation"
    from src.api.guardian_policy import decide

    assert decide(event).action == "ALERT"


@pytest.mark.parametrize("failure_kind", ["dns_failure", "websocket_disconnect"])
def test_fault_injection_connection_loss_budget_and_recovery(failure_kind):
    guardian = ExchangeGuardian(enabled=True)
    for i, clock in enumerate((0, 2, 5)):
        failed = guardian.inspect(
            obs(failure_kind, evidence_ref="sha256:" + str(i) * 64),
            now=clock,
        )
        assert failed.attempts == i + 1
        assert failed.proposal == ("NONE" if i == 2 else "PROPOSE_BOUNDED_RETRY")
    assert failed.circuit == "OPEN"
    still_blocked = guardian.inspect(obs(evidence_ref="sha256:" + "a" * 64), now=6)
    assert still_blocked.proposal == "NONE"
    assert still_blocked.history_complete is False
    restored = guardian.inspect(obs(evidence_ref="sha256:" + "b" * 64), now=3606)
    assert restored.status == "MONITORING"
    assert not restored.history_complete and not restored.gaps_reconciled


def test_fault_injection_http_403_never_retries_or_evasion():
    guardian = ExchangeGuardian(enabled=True)
    first = guardian.inspect(obs(status_code=403), now=10)
    assert first.status == "UNAVAILABLE"
    assert first.signal == "http_403"
    assert first.proposal == "NONE"
    assert first.circuit == "HALTED"
    blocked = guardian.inspect(obs(evidence_ref="sha256:" + "b" * 64), now=100000)
    assert blocked.proposal == "NONE"
    assert blocked.status == "UNAVAILABLE"
    assert not blocked.history_complete


def test_fault_injection_partial_response_never_commits_history():
    guardian = ExchangeGuardian(enabled=True)
    partial = guardian.inspect(obs("partial_response"), now=1)
    assert partial.status == "UNAVAILABLE"
    assert partial.signal == "malformed_response"
    assert partial.circuit == "OPEN"
    assert partial.proposal == "NONE"
    assert partial.history_complete is False
    assert partial.gaps_reconciled is False


def test_fault_injection_tls_preempts_dns_cooldown():
    guardian = ExchangeGuardian(enabled=True)
    guardian.inspect(obs("dns_failure"), now=0)
    tls = guardian.inspect(obs("tls_failure", evidence_ref="sha256:" + "b" * 64), now=0.1)
    assert tls.circuit == "HALTED"
    assert tls.proposal == "PROPOSE_STOP_SOURCE"


def test_fault_injection_rate_limit_not_bypassed_by_ws_disconnect():
    guardian = ExchangeGuardian(enabled=True)
    rate = guardian.inspect(obs(status_code=429, retry_after="120"), now=10)
    disconnect = guardian.inspect(
        obs("websocket_disconnect", evidence_ref="sha256:" + "b" * 64),
        now=11,
    )
    assert disconnect.proposal == "NONE"
    assert disconnect.retry_at == rate.retry_at


def test_fault_injection_http_403_preempts_rate_limit_cooldown():
    guardian = ExchangeGuardian(enabled=True)
    first = guardian.inspect(obs(status_code=429, retry_after="3600"), now=10)
    assert first.circuit == "OPEN"
    denied = guardian.inspect(obs(status_code=403, evidence_ref="sha256:" + "b" * 64), now=11)
    assert denied.circuit == "HALTED"
    assert denied.signal == "http_403"
    assert denied.proposal == "NONE"
    assert denied.history_complete is False
    repeated = guardian.inspect(obs(status_code=403, evidence_ref="sha256:" + "c" * 64), now=12)
    assert repeated.circuit == "HALTED"
    assert repeated.proposal == "NONE"
    assert guardian.reset_tls_halt(URL, manual_authorized=False) is False


def test_fault_injection_http_403_preempts_timeout_cooldown():
    guardian = ExchangeGuardian(enabled=True)
    guardian.inspect(obs("timeout"), now=1)
    denied = guardian.inspect(obs(status_code=403, evidence_ref="sha256:" + "b" * 64), now=1.5)
    assert denied.circuit == "HALTED"
    assert denied.proposal == "NONE"
    assert denied.reason == "forbidden_requires_manual_review"


def test_fault_injection_dns_repeat_evidence_no_duplicate_retry():
    guardian = ExchangeGuardian(enabled=True)
    failed = obs("dns_failure")
    first = guardian.inspect(failed, now=0)
    repeated = guardian.inspect(failed, now=2)
    assert first.proposal == "PROPOSE_BOUNDED_RETRY"
    assert repeated.proposal == "NONE"
    assert repeated.reason == "duplicate_evidence"
    assert repeated.attempts == 1


def test_fault_injection_429_isolated_per_endpoint_and_no_data_writes():
    guardian = ExchangeGuardian(enabled=True)
    blocked = guardian.inspect(obs(status_code=429, retry_after="60"), now=10)
    independent = guardian.inspect(
        obs(
            endpoint="https://api.bybit.com/v5/market/orderbook", evidence_ref="sha256:" + "b" * 64
        ),
        now=11,
    )
    assert blocked.retry_at == 70
    assert independent.status == "MONITORING"
    assert independent.history_complete is False
    assert independent.gaps_reconciled is False


@pytest.mark.parametrize(
    "url",
    [
        "https://public.bybit.com/v5/market/kline",
        "https://api.bybit.com/trading/BTCUSDT/file.csv.gz",
        "https://public.bybit.com/v5/market/orderbook",
        "https://api.bybit.com/v5/order/create",
    ],
)
def test_fault_injection_cross_host_scope_confusion_rejected(url):
    with pytest.raises(ValueError):
        endpoint_key(url)


def test_fault_injection_cross_host_scope_never_allocates_circuit():
    guardian = ExchangeGuardian(enabled=True)
    invalid = guardian.inspect(
        obs(endpoint="https://public.bybit.com/v5/market/kline"),
        now=10,
    )
    assert invalid.status == "UNAVAILABLE"
    assert invalid.signal == "invalid_observation"
    assert invalid.proposal == "NONE"
    assert not invalid.history_complete
    assert len(guardian._state) == 0
