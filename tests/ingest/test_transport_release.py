"""Release regressions use the sole production HTTP transport."""

import json
import logging
from unittest.mock import Mock

import pytest
import requests

from src.api.bybit_catalog import AcquisitionError, BybitCatalogSource
from src.api.guardians.exchange_guardian import ExchangeGuardian, endpoint_key
from src.api.logging_setup import JsonFormatter
from src.ingest.archive import ArchiveLoader
from src.ingest.journal import Journal
from src.ingest.rest import API
from src.ingest.transport import Transport

URL = "https://api.bybit.com/v5/market/time"


def response(code=200, headers=None):
    result = requests.Response()
    result.status_code = code
    result.headers.update(headers or {})
    result._content = b"{}"
    result._content_consumed = True
    return result


@pytest.mark.parametrize("failure", [response(403), requests.exceptions.SSLError("untrusted")])
def test_security_failures_are_never_retried(failure):
    session = Mock()
    if isinstance(failure, Exception):
        session.request.side_effect = failure
    else:
        session.request.return_value = failure
    with pytest.raises(requests.RequestException):
        Transport(session=session, sleep=Mock()).get(URL)
    assert session.request.call_count == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://api.bybit.com/v5/user/query-api",
        "https://api.bybit.com/v5/market/unknown",
        "https://api.bybit.com/v5/market/time?api_key=secret",
        "https://public.bybit.com/trading/%252e%252e/private",
        "https://public.bybit.com/trading/../private",
    ],
)
def test_unapproved_inputs_fail_before_network(url):
    session = Mock()
    with pytest.raises(ValueError):
        Transport(session=session).get(url)
    session.request.assert_not_called()


def test_transport_options_cannot_disable_tls_or_add_authentication():
    for options in [{"verify": False}, {"auth": ("a", "b")}, {"headers": {"X-BAPI-API-KEY": "x"}}]:
        session = Mock()
        with pytest.raises(ValueError):
            Transport(session=session).get(URL, **options)
        session.request.assert_not_called()


def test_cancellation_prevents_network():
    session = Mock()
    with pytest.raises(InterruptedError):
        Transport(session=session).get(URL, cancel=lambda: True)
    session.request.assert_not_called()


def test_unbounded_retry_after_fails_closed():
    session = Mock()
    session.request.return_value = response(429, {"Retry-After": "999999999999"})
    with pytest.raises(requests.HTTPError):
        Transport(session=session, sleep=Mock()).get(URL)
    assert session.request.call_count == 1


@pytest.mark.parametrize(
    "root", ["trading", "spot", "premium_index", "spot_index", "kline_for_metatrader4"]
)
def test_valid_archive_consumers_share_guardian_identity(root):
    url = f"https://public.bybit.com/{root}/BTCUSDT/BTCUSDT2026-01-01.csv.gz"
    session = Mock()
    session.request.return_value = response()
    assert Transport(session=session).get(url).status_code == 200
    assert endpoint_key(url) == url.removeprefix("https://")


def test_rest_query_identity_preserves_endpoint_scope_without_credentials():
    assert endpoint_key(URL + "?category=linear&symbol=BTCUSDT") == endpoint_key(URL)


def test_http_and_application_retries_share_one_budget():
    session = Mock()
    busy = response()
    busy._content = b'{"retCode":10006,"retMsg":"busy"}'
    session.request.side_effect = [requests.ConnectionError("offline"), busy, busy]
    with pytest.raises(RuntimeError, match="budget exhausted"):
        API(Transport(session=session, attempts=3, sleep=Mock())).call("/time", {})
    assert session.request.call_count == 3


def test_offline_recovery_then_new_operation_has_fresh_budget():
    session = Mock()
    session.request.side_effect = [requests.Timeout("offline"), response(), response()]
    transport = Transport(session=session, sleep=Mock())
    assert transport.get(URL).status_code == 200
    assert transport.get(URL).status_code == 200
    assert session.request.call_count == 3


def test_cancel_during_backoff_prevents_retry():
    session = Mock()
    session.request.side_effect = requests.Timeout("offline")
    cancelled = False

    def sleep(seconds):
        nonlocal cancelled
        cancelled = True

    with pytest.raises(InterruptedError):
        Transport(session=session, sleep=sleep).get(URL, cancel=lambda: cancelled)
    assert session.request.call_count == 1


@pytest.mark.parametrize("retry_after", ["0", "-1", "nan", "inf", "", "9999999"])
def test_unqualified_429_never_retries(retry_after):
    session = Mock()
    session.request.return_value = response(429, {"Retry-After": retry_after})
    with pytest.raises(requests.HTTPError):
        Transport(session=session, sleep=Mock()).get(URL)
    assert session.request.call_count == 1


@pytest.mark.parametrize(
    "content", [b"[]", b'{"retCode":0,"result":[]}', b'{"retCode":false,"result":{}}', b"invalid"]
)
def test_malformed_public_responses_do_not_retry(content):
    session = Mock()
    result = response()
    result._content = content
    session.request.return_value = result
    with pytest.raises(ValueError):
        API(Transport(session=session)).call("/time", {})
    assert session.request.call_count == 1


def test_forbidden_halts_endpoint_until_manual_reset():
    session = Mock()
    session.request.side_effect = [response(403), response()]
    transport = Transport(session=session)
    with pytest.raises(requests.HTTPError):
        transport.get(URL)
    with pytest.raises(ValueError, match="halted"):
        transport.get(URL)
    assert session.request.call_count == 1
    with pytest.raises(ValueError):
        transport.reset_halt(URL, manual_authorized=False)
    transport.reset_halt(URL, manual_authorized=True)
    assert transport.get(URL).status_code == 200


def test_archive_recovery_and_http_failures_share_one_attempt_budget(tmp_path):
    session = Mock()
    session.request.side_effect = requests.ConnectionError("offline")
    journal = Journal(tmp_path / "journal.sqlite")
    try:
        with pytest.raises((requests.ConnectionError, RuntimeError)):
            ArchiveLoader(journal, Transport(session=session, sleep=Mock())).download(
                "https://public.bybit.com/trading/BTCUSDT/a.csv.gz", tmp_path / "archive.csv.gz"
            )
        assert session.request.call_count == 3
    finally:
        journal.close()


def test_archive_rejects_unapproved_url_before_destination_side_effects(tmp_path):
    journal = Journal(tmp_path / "journal.sqlite")
    destination = tmp_path / "uncreated" / "archive.csv.gz"
    try:
        with pytest.raises(ValueError):
            ArchiveLoader(journal).download("https://api.bybit.com/v5/order/create", destination)
        assert not destination.parent.exists()
    finally:
        journal.close()


def test_actual_get_emits_query_free_guardian_event_and_closed_structured_log(caplog):
    events = []
    session = Mock()
    session.request.return_value = response(403)
    transport = Transport(
        session=session, guardian=ExchangeGuardian(enabled=True), event_sink=events.append
    )
    with caplog.at_level(logging.INFO, logger="tester.worker"):
        with pytest.raises(requests.HTTPError):
            transport.get(URL + "?cursor=DO_NOT_LOG_SECRET")
    observation = transport.last_observation
    assert observation.endpoint == URL and observation.status_code == 403
    assert transport.last_verdict.circuit == "HALTED"
    assert len(events) == 1 and events[0]["ownership_verified"] is False
    assert events[0]["signal"] == "untrusted_observation"
    records = [r for r in caplog.records if r.msg == "guardian_transport"]
    assert len(records) == 1
    encoded = JsonFormatter().format(records[0])
    logged = json.loads(encoded)
    assert logged["guardian_event"] == events[0]
    assert logged["http_status"] == 403
    assert "DO_NOT_LOG_SECRET" not in encoded and "cursor" not in encoded


def test_default_get_observations_keep_guardians_disabled():
    session = Mock()
    session.request.return_value = response()
    transport = Transport(session=session)
    transport.get(URL)
    assert transport.last_verdict.status == "DISABLED"
    assert transport.last_observation.source_verified is False


def test_actual_tls_failure_emits_sanitized_observation_once(caplog):
    session = Mock()
    session.request.side_effect = requests.exceptions.SSLError("DO_NOT_LOG_SECRET")
    events = []
    transport = Transport(
        session=session, guardian=ExchangeGuardian(enabled=True), event_sink=events.append
    )
    with caplog.at_level(logging.INFO, logger="tester.worker"):
        with pytest.raises(requests.exceptions.SSLError):
            transport.get(URL)
    assert transport.last_observation.kind == "tls_failure"
    assert transport.last_verdict.signal == "tls_verification_failed"
    assert len(events) == 1 and "DO_NOT_LOG_SECRET" not in json.dumps(events)
    records = [r for r in caplog.records if r.msg == "guardian_transport"]
    assert len(records) == 1
    assert json.loads(JsonFormatter().format(records[0]))["observation_kind"] == "tls_failure"


def test_failed_diagnostic_sink_cannot_clear_a_forbidden_halt():
    session = Mock()
    session.request.return_value = response(403)

    def broken_sink(event):
        raise RuntimeError("sink unavailable")

    transport = Transport(session=session, event_sink=broken_sink)
    with pytest.raises(RuntimeError):
        transport.get(URL)
    with pytest.raises(ValueError, match="halted"):
        transport.get(URL)
    assert session.request.call_count == 1


def test_structured_transport_log_drops_unrecognized_signal():
    record = logging.LogRecord("tester.worker", logging.INFO, "", 0, "guardian_transport", (), None)
    record.guardian_event = dict(
        component="exchange",
        environment="tester_preview",
        scope="bybit/rest/api.bybit.com/v5/market/time",
        signal="do_not_log_secret",
        evidence_ref="sha256:" + "a" * 64,
        ownership_verified=False,
    )
    record.elapsed_ms = 10**1000
    encoded = JsonFormatter().format(record)
    assert "do_not_log_secret" not in encoded and "guardian_event" not in encoded


def test_catalog_uses_single_transport_and_preserves_http_rate_limit_mapping():
    source = BybitCatalogSource()
    assert isinstance(source.transport, Transport)
    session = Mock()
    session.request.return_value = response(429, {"Retry-After": "1"})
    source.transport.session = session
    with pytest.raises(AcquisitionError, match="rate_limited"):
        source.call("/tickers", {"category": "linear"})
    assert session.request.call_count == 1
    assert source.transport.last_observation.status_code == 429
    assert session.trust_env is False


def test_catalog_forbidden_halt_survives_later_catalog_request():
    source = BybitCatalogSource()
    session = Mock()
    session.request.return_value = response(403)
    source.transport.session = session
    with pytest.raises(requests.HTTPError):
        source.call("/instruments-info", {"category": "linear"})
    with pytest.raises(ValueError, match="halted"):
        source.call("/instruments-info", {"category": "inverse"})
    assert session.request.call_count == 1


def test_default_rest_session_mounts_verified_system_trust_once(monkeypatch):
    import ssl

    session = Mock()
    session.request.return_value = response()
    monkeypatch.setattr("src.ingest.transport.requests.Session", lambda: session)
    transport = Transport(sleep=Mock())
    transport.get(URL)
    transport.get(URL)
    assert session.mount.call_count == 1
    scheme, adapter = session.mount.call_args.args
    context = adapter.poolmanager.connection_pool_kw["ssl_context"]
    assert scheme == "https://" and context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert session.trust_env is False
