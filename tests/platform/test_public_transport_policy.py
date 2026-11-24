"""Hard network policy is independent of disabled Guardian effectors."""

import os
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests

from src.api.config import Settings
from src.ingest.transport import Transport

URL = "https://api.bybit.com/v5/market/time"


class ReservationFixture:
    """Protocol fixture only; real durability/privilege tests use PostgreSQL below."""

    def __init__(self):
        self.state = "CLEAR"
        self.token = None

    def reserve(self, key):
        if self.state == "CLEAR":
            self.state, self.token = "PENDING", str(uuid4())
            return "GRANTED", self.token
        return self.state, None

    def finish(self, key, token, outcome):
        assert token == self.token
        self.state = "CLEAR" if outcome == "allow" else "HALTED"

    def reset(self, key):
        raise PermissionError("owner role required")


def response(status=200):
    value = requests.Response()
    value.status_code = status
    value._content = b"{}"
    value._content_consumed = True
    return value


@pytest.mark.parametrize("fault", ["http_403", "tls_failure"])
def test_halt_survives_fresh_transport_instance(fault):
    store = ReservationFixture()
    first = Mock()
    if fault == "http_403":
        first.request.return_value = response(403)
    else:
        first.request.side_effect = requests.exceptions.SSLError("untrusted")
    with pytest.raises(requests.RequestException):
        Transport(session=first, policy_store=store).get(URL)
    second = Mock()
    second.request.return_value = response()
    with pytest.raises(RuntimeError, match="manual review"):
        Transport(session=second, policy_store=store).get(URL)
    second.request.assert_not_called()


def test_stream_tls_latches_before_context_cleanup_and_fresh_instance():
    store = ReservationFixture()
    value = response()

    def broken(*args, **kwargs):
        yield b"prefix"
        raise requests.exceptions.SSLError("stream TLS failure")

    value.iter_content = broken
    session = Mock()
    session.request.return_value = value
    with pytest.raises(requests.exceptions.SSLError):
        with Transport(session=session, policy_store=store).get(URL, stream=True) as stream:
            list(stream.iter_content(10))
    assert store.state == "HALTED"
    fresh = Mock()
    with pytest.raises(RuntimeError):
        Transport(session=fresh, policy_store=store, sleep=Mock()).get(URL)
    fresh.request.assert_not_called()


def close_before_delayed_tls(store, fail_halt_write=False, outage=None):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    reading, resume = Event(), Event()
    original_finish = store.finish

    def finish(key, token, outcome):
        if fail_halt_write and outcome != "allow":
            raise RuntimeError("simulated halt write outage")
        return original_finish(key, token, outcome)

    store.finish = finish
    value = response()

    def delayed(*args, **kwargs):
        yield b"prefix"
        reading.set()
        assert resume.wait(5), "reader release timed out"
        raise requests.exceptions.SSLError("delayed stream TLS failure")

    value.iter_content = delayed
    session = Mock()
    session.request.return_value = value
    stream = Transport(session=session, policy_store=store).get(URL, stream=True)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: list(stream.iter_content(10)))
        try:
            assert reading.wait(5), "reader never entered stream"
            stream.close()
            stream.close()  # ordinary cleanup is idempotent
            if outage:
                outage(False)
        finally:
            resume.set()
        with pytest.raises(
            RuntimeError if fail_halt_write or outage else requests.exceptions.SSLError
        ):
            future.result(timeout=5)
    if outage:
        outage(True)
    store.finish = original_finish
    if not fail_halt_write and not outage:
        stream.close()  # cleanup must never downgrade the late TLS outcome
    fresh = Mock()
    fresh.request.return_value = response()
    with pytest.raises(RuntimeError, match="manual review"):
        Transport(session=fresh, policy_store=store, sleep=Mock()).get(URL)
    fresh.request.assert_not_called()


@pytest.mark.parametrize("reader_count", [1, 2])
def test_stream_close_waits_for_all_readers_and_rejects_reentry(reader_count):
    store = ReservationFixture()
    value = response()
    value.iter_content = lambda *a, **k: iter([b"prefix", b"suffix"])
    session = Mock()
    session.request.return_value = value
    stream = Transport(session=session, policy_store=store).get(URL, stream=True)
    readers = [stream.iter_content(10) for _ in range(reader_count)]
    for reader in readers:
        assert next(reader) == b"prefix"
    stream.close()
    assert store.state == "PENDING"
    with pytest.raises(ValueError, match="already closed"):
        next(stream.iter_content(10))
    for reader in readers[:-1]:
        reader.close()
        assert store.state == "PENDING"
    readers[-1].close()
    assert store.state == "CLEAR"
    stream.close()


def test_actual_pg_stream_close_then_tls_during_real_database_outage(durable_policy):
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from src.api.db import connect

    policy, owner, scope = durable_policy
    database = conninfo_to_dict(owner.dsn)["dbname"]
    admin = make_conninfo(owner.dsn, dbname="postgres")

    def availability(enabled):
        with connect(admin) as conn:
            conn.execute(
                sql.SQL(
                    "ALTER DATABASE {} ALLOW_CONNECTIONS " + ("true" if enabled else "false")
                ).format(sql.Identifier(database))
            )

    try:
        close_before_delayed_tls(policy, outage=availability)
        assert policy.read(URL.removeprefix("https://")) == "PENDING"
    finally:
        availability(True)


def test_stream_active_reader_keeps_pending_if_tls_halt_write_fails():
    store = ReservationFixture()
    close_before_delayed_tls(store, fail_halt_write=True)
    assert store.state == "PENDING"


def test_actual_pg_active_reader_keeps_pending_if_tls_halt_write_fails(durable_policy):
    policy, owner, scope = durable_policy
    close_before_delayed_tls(policy, fail_halt_write=True)
    assert policy.read(URL.removeprefix("https://")) == "PENDING"


def test_stream_close_cannot_discard_later_reader_tls_identity():
    store = ReservationFixture()
    close_before_delayed_tls(store)
    assert store.state == "HALTED"


def test_actual_pg_stream_close_then_delayed_tls_halts_endpoint(durable_policy):
    policy, owner, scope = durable_policy
    close_before_delayed_tls(policy)
    assert policy.read(URL.removeprefix("https://")) == "HALTED"


def test_stream_pending_until_close_and_explicit_abort_releases_clean_request():
    store = ReservationFixture()
    session = Mock()
    session.request.return_value = response()
    stream = Transport(session=session, policy_store=store).get(URL, stream=True)
    assert store.state == "PENDING"
    stream.close()
    assert store.state == "CLEAR"


def test_policy_store_unavailable_never_transmits():
    store = Mock()
    store.reserve.side_effect = RuntimeError("policy unavailable")
    session = Mock()
    with pytest.raises(RuntimeError):
        Transport(session=session, policy_store=store).get(URL)
    session.request.assert_not_called()


def test_native_policy_requires_scope_and_primary_database(tmp_path):
    with pytest.raises(ValueError, match="public transport policy"):
        Settings(data_dir=tmp_path, public_transport_policy_enabled=True)


def test_cancellation_after_reservation_releases_without_network():
    store = ReservationFixture()
    original = store.reserve
    stopped = False

    def reserve(key):
        nonlocal stopped
        result = original(key)
        stopped = True
        return result

    store.reserve = reserve
    session = Mock()
    with pytest.raises(InterruptedError):
        Transport(session=session, policy_store=store, cancel=lambda: stopped).get(URL)
    session.request.assert_not_called()
    assert store.state == "CLEAR"


def test_failed_halt_write_closes_http_response_without_clearing_pending():
    store = ReservationFixture()
    store.finish = Mock(side_effect=RuntimeError("DB unavailable"))
    value = response(403)
    original_close = Mock()
    value.close = original_close
    session = Mock()
    session.request.return_value = value
    with pytest.raises(RuntimeError):
        Transport(session=session, policy_store=store).get(URL, stream=True)
    original_close.assert_called_once()
    assert store.state == "PENDING"


@pytest.fixture
def durable_policy():
    from psycopg.conninfo import conninfo_to_dict

    from src.api.db import connect
    from src.api.transport_policy import PostgresTransportPolicy

    dsn, owner_dsn = (
        os.environ.get("TEST_PUBLIC_POLICY_DSN"),
        os.environ.get("TEST_PUBLIC_POLICY_OWNER_DSN"),
    )
    if not dsn or not owner_dsn:
        pytest.skip(
            "Explicit disposable public-policy App/owner DSNs on reserved 55820..55827 required"
        )
    values = conninfo_to_dict(dsn)
    if values.get("host") != "127.0.0.1" or not 55820 <= int(values.get("port", "0")) <= 55827:
        raise ValueError("Reserved disposable policy database required")
    scope = uuid4()
    own_values = conninfo_to_dict(owner_dsn)
    if any(own_values.get(k) != values.get(k) for k in ("host", "port", "dbname")):
        raise ValueError("Policy owner must reference the same reserved disposable database")
    PostgresTransportPolicy(
        owner_dsn, scope
    )  # Validate owner endpoint before opening any connection.
    with connect(owner_dsn) as conn:
        conn.execute(
            "INSERT INTO tester_platform.transport_policy_scopes(installation_id,runtime_role,owner_role) VALUES(%s,%s,current_user)",
            (scope, values["user"]),
        )
    app, owner = PostgresTransportPolicy(dsn, scope), PostgresTransportPolicy(owner_dsn, scope)
    try:
        yield app, owner, scope
    finally:
        with connect(owner_dsn) as conn:
            # Explicit test-owner cleanup also repairs intentionally corrupted fixture rows.
            conn.execute(
                "DELETE FROM tester_platform.transport_policy_entries WHERE installation_id=%s",
                (scope,),
            )
            conn.execute(
                "DELETE FROM tester_platform.transport_policy_scopes WHERE installation_id=%s",
                (scope,),
            )


@pytest.mark.integration
@pytest.mark.parametrize("fault", ["http_403", "tls_failure"])
def test_actual_pg_halt_persists_across_fresh_transport_and_python_process(durable_policy, fault):
    import subprocess
    import sys

    policy, owner, scope = durable_policy
    session = Mock()
    if fault == "http_403":
        session.request.return_value = response(403)
    else:
        session.request.side_effect = requests.exceptions.SSLError("injected untrusted peer")
    with pytest.raises(requests.RequestException):
        Transport(session=session, policy_store=policy).get(URL)
    assert policy.read(URL.removeprefix("https://")) == "HALTED"
    script = """import os;from unittest.mock import Mock;from src.api.transport_policy import PostgresTransportPolicy;from src.ingest.transport import Transport
s=Mock();p=PostgresTransportPolicy(os.environ['TEST_PUBLIC_POLICY_DSN'],os.environ['POLICY_SCOPE'])
try:Transport(session=s,policy_store=p).get('https://api.bybit.com/v5/market/time')
except RuntimeError:pass
else:raise AssertionError('Fresh process bypassed halt')
s.request.assert_not_called()
"""
    subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "POLICY_SCOPE": str(scope)},
        capture_output=True,
        check=True,
        timeout=15,
    )


@pytest.mark.integration
def test_actual_pg_stream_tls_and_owner_only_reset(durable_policy):
    import psycopg

    from src.api.db import connect

    policy, owner, scope = durable_policy
    value = response()

    def broken(*args, **kwargs):
        yield b"prefix"
        raise requests.exceptions.SSLError("stream TLS")

    value.iter_content = broken
    session = Mock()
    session.request.return_value = value
    with pytest.raises(requests.exceptions.SSLError):
        with Transport(session=session, policy_store=policy).get(URL, stream=True) as stream:
            list(stream.iter_content(10))
    key = URL.removeprefix("https://")
    assert policy.read(key) == "HALTED"
    with pytest.raises(RuntimeError):
        policy.reset(key)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), connect(policy.dsn) as conn:
        conn.execute("DELETE FROM tester_platform.transport_policy_entries")
    assert owner.reset(key)
    fresh = Mock()
    fresh.request.return_value = response()
    assert Transport(session=fresh, policy_store=policy).get(URL).status_code == 200
    assert policy.read(key) == "CLEAR"


@pytest.mark.integration
def test_pending_crash_and_failed_halt_write_cannot_be_bypassed(durable_policy, monkeypatch):
    policy, owner, scope = durable_policy
    original = policy.finish
    monkeypatch.setattr(policy, "finish", Mock(side_effect=RuntimeError("DB write unavailable")))
    session = Mock()
    session.request.return_value = response(403)
    with pytest.raises(RuntimeError):
        Transport(session=session, policy_store=policy).get(URL)
    monkeypatch.setattr(policy, "finish", original)
    assert policy.read(URL.removeprefix("https://")) == "PENDING"
    fresh = Mock()
    with pytest.raises(RuntimeError):
        Transport(session=fresh, policy_store=policy, sleep=Mock()).get(URL)
    fresh.request.assert_not_called()


@pytest.mark.integration
def test_capacity_retains_all_halts_and_refuses_unknown_scope_or_corrupt_state(durable_policy):
    from src.api.db import connect
    from src.api.transport_policy import PostgresTransportPolicy

    policy, owner, scope = durable_policy
    with connect(owner.dsn) as conn:
        conn.execute(
            "INSERT INTO tester_platform.transport_policy_entries(installation_id,endpoint_key,request_token,state,reason) SELECT %s,'public.bybit.com/trading/BTCUSDT/f'||n||'.csv.gz',gen_random_uuid(),'HALTED','http_403' FROM generate_series(1,128)n",
            (scope,),
        )
    session = Mock()
    with pytest.raises(RuntimeError):
        Transport(session=session, policy_store=policy).get(URL)
    session.request.assert_not_called()
    with connect(owner.dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tester_platform.transport_policy_entries WHERE installation_id=%s",
                (scope,),
            ).fetchone()[0]
            == 128
        )
    assert owner.reset()
    unknown = PostgresTransportPolicy(policy.dsn, uuid4())
    with pytest.raises(RuntimeError):
        Transport(session=session, policy_store=unknown).get(URL)
    with connect(owner.dsn) as conn:
        conn.execute(
            "INSERT INTO tester_platform.transport_policy_entries VALUES(%s,%s,gen_random_uuid(),'PENDING',NULL,'infinity')",
            (scope, URL.removeprefix("https://")),
        )
    with pytest.raises(RuntimeError):
        Transport(session=session, policy_store=policy).get(URL)
    session.request.assert_not_called()


@pytest.mark.integration
def test_security_fault_after_owner_fence_requires_all_scope_review(durable_policy):
    policy, owner, scope = durable_policy
    key = URL.removeprefix("https://")
    state, token = policy.reserve(key)
    assert state == "GRANTED"
    assert owner.reset(key)
    policy.finish(key, token, "tls_failure")
    assert policy.read("api.bybit.com/v5/market/kline") == "HALTED"
    owner.reset(key)
    assert policy.read(key) == "HALTED"
    owner.reset()
    assert policy.read(key) == "CLEAR"


@pytest.mark.integration
def test_same_endpoint_concurrent_requests_serialize_without_leaking_reservations(durable_policy):
    import time
    from concurrent.futures import ThreadPoolExecutor

    policy, owner, scope = durable_policy

    def request(index):
        session = Mock()

        def answer(*args, **kwargs):
            time.sleep(0.1)
            return response()

        session.request.side_effect = answer
        return Transport(session=session, policy_store=policy).get(URL).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(request, range(2))) == [200, 200]
    assert policy.read(URL.removeprefix("https://")) == "CLEAR"


@pytest.mark.integration
def test_app_cannot_clear_halt_with_old_token_and_real_db_outage_denies_network(durable_policy):
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from src.api.db import connect

    policy, owner, scope = durable_policy
    key = URL.removeprefix("https://")
    state, token = policy.reserve(key)
    policy.finish(key, token, "http_403")
    with pytest.raises(RuntimeError):
        policy.finish(key, token, "allow")
    assert policy.read(key) == "HALTED"
    owner.reset(key)
    database = conninfo_to_dict(owner.dsn)["dbname"]
    admin = make_conninfo(owner.dsn, dbname="postgres")
    with connect(admin) as conn:
        conn.execute(
            sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(sql.Identifier(database))
        )
    try:
        session = Mock()
        with pytest.raises(RuntimeError):
            Transport(session=session, policy_store=policy).get(URL)
        session.request.assert_not_called()
    finally:
        with connect(admin) as conn:
            conn.execute(
                sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS true").format(sql.Identifier(database))
            )


@pytest.mark.parametrize("port", ["05432", "0005432"])
def test_policy_constructor_rejects_numeric_default_port_without_connect(monkeypatch, port):
    import src.api.transport_policy as module

    connect = Mock(side_effect=AssertionError("No database connection permitted"))
    monkeypatch.setattr(module, "connect", connect)
    with pytest.raises(ValueError):
        module.PostgresTransportPolicy(
            f"host=127.0.0.1 port={port} dbname=tester user=tester_app require_auth=scram-sha-256",
            uuid4(),
        )
    connect.assert_not_called()


def close_inflight_after_clean_reader(store, *, outage=None, close_error="tls"):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    reader_started, reader_release, close_started, close_release = [Event() for _ in range(4)]
    original_finish = store.finish
    close_calls = []

    def finish(key, token, outcome):
        if not outage and close_error == "tls" and outcome != "allow":
            raise RuntimeError("halt write unavailable")
        return original_finish(key, token, outcome)

    def read(*a, **k):
        yield b"prefix"
        reader_started.set()
        assert reader_release.wait(5)

    def original_close():
        close_calls.append(1)
        close_started.set()
        assert close_release.wait(5)
        if close_error == "tls":
            raise requests.exceptions.SSLError("close TLS")
        if close_error == "generic":
            raise OSError("close failed")

    store.finish = finish
    value = response()
    value.iter_content, value.close = read, original_close
    session = Mock()
    session.request.return_value = value
    stream = Transport(session=session, policy_store=store).get(URL, stream=True)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reader = executor.submit(lambda: list(stream.iter_content(10)))
            try:
                assert reader_started.wait(5)
                closer = executor.submit(stream.close)
                assert close_started.wait(5)
                stream.close()  # must not call the underlying close again
                reader_release.set()
                assert reader.result(timeout=5) == [b"prefix"]
                key = URL.removeprefix("https://")
                assert (store.read(key) if hasattr(store, "read") else store.state) == "PENDING"
                if outage:
                    outage(False)
            finally:
                reader_release.set()
                close_release.set()
            if close_error:
                with pytest.raises(RuntimeError if close_error == "tls" else OSError):
                    closer.result(timeout=5)
            else:
                closer.result(timeout=5)
    finally:
        if outage:
            outage(True)
        store.finish = original_finish
    assert close_calls == [1]
    stream.close()
    key = URL.removeprefix("https://")
    state = store.read(key) if hasattr(store, "read") else store.state
    assert state == ("PENDING" if close_error else "CLEAR")
    if close_error:
        fresh = Mock()
        with pytest.raises(RuntimeError, match="manual review"):
            Transport(session=fresh, policy_store=store, sleep=Mock()).get(URL)
        fresh.request.assert_not_called()


@pytest.mark.parametrize("close_error", ["tls", "generic", None])
def test_inflight_close_blocks_cleanup_after_clean_reader(close_error):
    close_inflight_after_clean_reader(ReservationFixture(), close_error=close_error)


def test_actual_pg_inflight_close_tls_with_real_database_outage(durable_policy):
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from src.api.db import connect

    policy, owner, scope = durable_policy
    database = conninfo_to_dict(owner.dsn)["dbname"]
    admin = make_conninfo(owner.dsn, dbname="postgres")

    def availability(enabled):
        with connect(admin) as conn:
            conn.execute(
                sql.SQL(
                    "ALTER DATABASE {} ALLOW_CONNECTIONS " + ("true" if enabled else "false")
                ).format(sql.Identifier(database))
            )

    close_inflight_after_clean_reader(policy, outage=availability)


@pytest.mark.integration
@pytest.mark.parametrize("status", [302, 429, 503])
def test_actual_pg_error_stream_close_tls_with_database_outage(durable_policy, status):
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from src.api.db import connect
    from tests.platform.test_public_transport_edges import assert_error_stream_close_pending

    policy, owner, scope = durable_policy
    database = conninfo_to_dict(owner.dsn)["dbname"]
    admin = make_conninfo(owner.dsn, dbname="postgres")

    def availability(enabled):
        with connect(admin) as conn:
            conn.execute(
                sql.SQL(
                    "ALTER DATABASE {} ALLOW_CONNECTIONS " + ("true" if enabled else "false")
                ).format(sql.Identifier(database))
            )

    try:
        assert_error_stream_close_pending(policy, status, availability)
    finally:
        availability(True)
