"""Release regressions use the sole production HTTP transport."""

from unittest.mock import Mock

import pytest
import requests

from src.api.guardians.exchange_guardian import endpoint_key
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
