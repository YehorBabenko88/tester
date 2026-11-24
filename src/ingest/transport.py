"""Single read-only transport with bounded logical budgets and public scope."""

import hashlib
import json
import logging
import math
import random
import ssl
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import requests
from requests.sessions import Session as RequestsSession

from src.api.guardians.exchange_guardian import (
    ExchangeGuardian,
    Observation,
    retry_after_seconds,
    supervisor_event,
)

from .public_access import public_endpoint_key


class SystemTrustAdapter(requests.adapters.HTTPAdapter):
    """Reuse verified OS trust, including the Windows ROOT certificate store."""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context()
        return super().init_poolmanager(*args, **kwargs)


class UnsafePublicSession(ValueError):
    """A configured HTTP session would add unapproved network behavior."""


class PublicEndpointHalted(ValueError):
    """A queued request was refused before sending any bytes."""


class Transport:
    def __init__(
        self,
        session=None,
        attempts=3,
        rate=3,
        sleep=time.sleep,
        *,
        guardian=None,
        event_sink=None,
        policy_store=None,
        cancel=None,
    ):
        if (
            type(attempts) is not int
            or not 1 <= attempts <= 6
            or type(rate) not in (int, float)
            or not math.isfinite(rate)
            or rate <= 0
        ):
            raise ValueError("Invalid retry/rate settings")
        self.session = session
        self.local = threading.local()
        self.attempts, self.rate, self.sleep = attempts, rate, sleep
        self.lock = threading.Lock()
        self.dispatch_lock = threading.Lock()
        self.next_request = 0
        self.halted = set()
        self.policy_store = policy_store
        if cancel is not None and not callable(cancel):
            raise ValueError("Callable cancellation required")
        self.cancel_callback = cancel
        if guardian is not None and not isinstance(guardian, ExchangeGuardian):
            raise ValueError("Existing Exchange Guardian required")
        if event_sink is not None and not callable(event_sink):
            raise ValueError("Callable diagnostic event sink required")
        self.guardian = guardian if guardian is not None else ExchangeGuardian()
        self.event_sink = event_sink
        self.last_observation = self.last_verdict = None

    def _observe(self, key, started, kind, *, status_code=None, retry_after=None):
        trace = uuid4().hex
        with self.lock:
            now = time.monotonic()
            measured = {
                "endpoint": key,
                "kind": kind,
                "status_code": status_code,
                "elapsed_ms": max(0, (now - started) * 1000),
                "trace_id": trace,
            }
            ref = (
                "sha256:"
                + hashlib.sha256(json.dumps(measured, sort_keys=True).encode()).hexdigest()
            )
            observation = Observation(
                endpoint="https://" + key,
                kind=kind,
                elapsed_ms=measured["elapsed_ms"],
                evidence_ref=ref,
                status_code=status_code,
                retry_after=retry_after,
                source_verified=False,
            )
            verdict = self.guardian.inspect(observation, now=now)
            self.last_observation, self.last_verdict = observation, verdict
        # Request metadata qualifies a network observation, never ownership, body
        # integrity, source freshness or historical completeness.
        event = supervisor_event(observation, verdict, now=now, ownership_verified=False)
        logger = next(
            (
                logging.getLogger("tester." + name)
                for name in ("worker", "api")
                if logging.getLogger("tester." + name).handlers
            ),
            logging.getLogger("tester.worker"),
        )
        logger.info(
            "guardian_transport",
            extra={
                "trace_id": trace,
                "guardian_event": event,
                "http_status": status_code,
                "observation_kind": kind,
                "elapsed_ms": measured["elapsed_ms"],
            },
        )
        if self.event_sink is not None:
            self.event_sink(dict(event))

    def reset_halt(self, url, *, manual_authorized):
        key = public_endpoint_key(url)
        if manual_authorized is not True:
            raise ValueError("Manual review required")
        if self.policy_store is not None:
            self.policy_store.reset(
                key
            )  # Database owner authorization cannot be replaced by a bool.
        with self.lock:
            self.halted.discard(key)
            self.guardian.reset_tls_halt(url, manual_authorized=True)

    def _reserve_policy(self, key, cancel):
        if self.policy_store is None:
            return {
                "token": None,
                "security_token": None,
                "outcome": "allow",
                "lock": threading.RLock(),
            }
        deadline = time.monotonic() + 5
        for _ in range(20):
            self._wait(0, cancel)
            state, token = self.policy_store.reserve(key)
            if state == "GRANTED":
                from uuid import UUID

                return {
                    "token": str(UUID(str(token))),
                    "security_token": str(UUID(str(token))),
                    "outcome": "allow",
                    "lock": threading.RLock(),
                }
            if state not in {"PENDING", "HALTED", "CAPACITY"} or token is not None:
                raise RuntimeError("Public transport policy invalid; manual review required")
            if state != "PENDING" or time.monotonic() >= deadline:
                break
            self._wait(0.25, cancel)
        raise RuntimeError("Public endpoint unavailable; manual review required")

    def _finish_policy(self, key, lease):
        with lease["lock"]:
            if (
                self.policy_store is not None
                and lease["token"] is not None
                and not lease.get("active_readers", 0)
                and not lease.get("active_closers", 0)
                and not lease.get("close_failed", False)
                and not lease.get("reader_close_failed", False)
                and (not lease.get("response_guarded", False) or lease.get("close_complete", False))
            ):
                self.policy_store.finish(key, lease["token"], lease["outcome"])
                lease["token"] = None

    def _security_fault(self, key, lease, reason):
        # Keep the original identity even after another thread completed ordinary
        # close. PostgreSQL fences a late fault with that identity and halts the
        # scope when the reservation has already been released or replaced.
        with lease["lock"]:
            lease["outcome"] = reason
            with self.lock:
                self.halted.add(key)
            if self.policy_store is not None and not lease.get("security_reported", False):
                self.policy_store.finish(key, lease["security_token"], reason)
                lease["security_reported"] = True
                lease["token"] = None

    def _guard_response(self, response, key, lease, started, *, streaming, cancel=None):
        original_iter, original_close = response.iter_content, response.close
        lease["response_guarded"] = True

        def consume(*args, **kwargs):
            with lease["lock"]:
                if lease.get("closing", False):
                    raise ValueError("Public response already closed")
                lease["active_readers"] = lease.get("active_readers", 0) + 1
            iterator = None
            try:
                self._wait(0, cancel)
                iterator = iter(original_iter(*args, **kwargs))
                for block in iterator:
                    self._wait(0, cancel)
                    yield block
                    self._wait(0, cancel)
            except InterruptedError:
                close()
                raise
            except requests.exceptions.SSLError:
                self._security_fault(key, lease, "tls_failure")
                self._observe(key, started, "tls_failure")
                raise
            except requests.RequestException as exc:
                if cancel and cancel():
                    close()
                    raise InterruptedError("Public request cancelled") from exc
                raise
            finally:
                try:
                    closer = getattr(iterator, "close", None)
                    if callable(closer):
                        try:
                            closer()
                        except BaseException as exc:
                            with lease["lock"]:
                                lease["reader_close_failed"] = True
                            if isinstance(exc, requests.exceptions.SSLError):
                                self._security_fault(key, lease, "tls_failure")
                                self._observe(key, started, "tls_failure")
                            raise
                finally:
                    with lease["lock"]:
                        lease["active_readers"] -= 1
                        if lease.get("closing", False):
                            self._finish_policy(key, lease)

        def close():
            with lease["lock"]:
                if lease.get("closing", False):
                    return
                lease["closing"] = True
                lease["active_closers"] = 1
            succeeded = False
            try:
                original_close()
                succeeded = True
            except requests.exceptions.SSLError:
                self._security_fault(key, lease, "tls_failure")
                self._observe(key, started, "tls_failure")
                raise
            finally:
                with lease["lock"]:
                    lease["active_closers"] = 0
                    # Any unconfirmed underlying close leaves its reservation
                    # pending. A successful TLS halt write already consumed it.
                    lease["close_failed"] = not succeeded
                    lease["close_complete"] = succeeded
                    self._finish_policy(key, lease)

        if streaming:
            response.iter_content = consume
        response.close = close
        return response

    @contextmanager
    def operation(self):
        """One budget across HTTP failures and Bybit application-level retries."""
        owner = not hasattr(self.local, "remaining")
        if owner:
            self.local.remaining = self.attempts
        try:
            yield
        finally:
            if owner:
                del self.local.remaining

    def defer(self, seconds):
        if (
            type(seconds) not in (int, float)
            or not math.isfinite(seconds)
            or not 0 <= seconds <= 3600
        ):
            raise ValueError("Invalid backoff")
        with self.lock:
            self.next_request = max(self.next_request, time.monotonic() + seconds)

    def _wait(self, seconds, cancel):
        # Chunk real waits so cancellation is observed within 250ms.
        while seconds > 0:
            if cancel and cancel():
                raise InterruptedError("Public request cancelled")
            step = min(seconds, 0.25) if cancel else seconds
            self.sleep(step)
            seconds -= step
        if cancel and cancel():
            raise InterruptedError("Public request cancelled")

    @staticmethod
    def _validate_session(session):
        # Opaque request protocols are trusted test/integration seams, not a
        # sandbox. The native HttpMeter exposes its underlying real Session.
        candidate = session
        if not isinstance(candidate, RequestsSession):
            candidate = getattr(session, "__dict__", {}).get("session")
        if not isinstance(candidate, RequestsSession):
            return
        safe_headers = {"accept", "accept-encoding", "user-agent", "connection"}
        if (
            candidate.trust_env is not False
            or candidate.verify is not True
            and not (isinstance(candidate.verify, str) and candidate.verify)
            or candidate.auth is not None
            or candidate.cookies
            or candidate.proxies
            or candidate.params
            or candidate.cert
            or any(candidate.hooks.values())
            or any(str(name).lower() not in safe_headers for name in candidate.headers)
        ):
            raise UnsafePublicSession("Unsafe public requests session configuration")
        for adapter in candidate.adapters.values():
            if type(adapter) not in {requests.adapters.HTTPAdapter, SystemTrustAdapter}:
                raise UnsafePublicSession("Unapproved public requests session adapter")
            if adapter.max_retries.total != 0:
                raise UnsafePublicSession("Hidden public requests session retries")
            context = adapter.poolmanager.connection_pool_kw.get("ssl_context")
            if context is not None and (
                context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname
            ):
                raise UnsafePublicSession("Unsafe public requests session TLS context")

    def _request(self, session, url, key, timeout, cancel, kwargs):
        # Reserve the actual send slot after durable admission. Keep this sole
        # dispatch gate until headers/body arrive, so delayed callers cannot
        # send a burst and a 429 publishes cooldown before the next send.
        while not self.dispatch_lock.acquire(timeout=0.25):
            self._wait(0, cancel)
        try:
            while True:
                self._wait(0, cancel)
                with self.lock:
                    deadline = max(time.monotonic(), self.next_request)
                self._wait(max(0, deadline - time.monotonic()), cancel)
                with self.lock:
                    if self.next_request > deadline:
                        continue
                    self.next_request = max(deadline, time.monotonic()) + 1 / self.rate
                break
            self._validate_session(session)
            with self.lock:
                if key in self.halted or len(self.halted) >= 128:
                    raise PublicEndpointHalted("Public endpoint halted; manual review required")
            try:
                response = session.request(
                    "GET", url, timeout=timeout, allow_redirects=False, **kwargs
                )
            except requests.exceptions.SSLError:
                with self.lock:
                    self.halted.add(key)
                raise
            if response.status_code == 403:
                with self.lock:
                    self.halted.add(key)
            if response.status_code == 429:
                after = retry_after_seconds(response.headers.get("Retry-After"), datetime.now(UTC))
                if after is not None:
                    self.defer(after)
            return response
        finally:
            self.dispatch_lock.release()

    def get(self, url, *, cancel=None, **kwargs):
        cancel = cancel if cancel is not None else self.cancel_callback
        key = public_endpoint_key(url)
        if set(kwargs) - {"headers", "stream", "timeout"}:
            raise ValueError("Unsupported public request options")
        headers = kwargs.get("headers", {})
        if not isinstance(headers, dict) or any(
            str(k).lower() not in {"range", "if-range", "accept-encoding", "accept"}
            or type(v) is not str
            or "\r" in v
            or "\n" in v
            for k, v in headers.items()
        ):
            raise ValueError("Unsupported public headers")
        timeout = kwargs.pop("timeout", (15, 60))
        if (
            not isinstance(timeout, tuple)
            or len(timeout) != 2
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= cap
                for v, cap in zip(timeout, (15, 60))
            )
        ):
            raise ValueError("Invalid bounded timeout")
        if self.session is None and not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.mount("https://", SystemTrustAdapter())
            self.local.session.trust_env = False
        session = self.session if self.session is not None else self.local.session
        self._validate_session(session)
        with self.operation():
            while self.local.remaining:
                with self.lock:
                    if key in self.halted or len(self.halted) >= 128:
                        raise ValueError("Public endpoint halted; manual review required")
                self._wait(0, cancel)
                lease = self._reserve_policy(key, cancel)
                try:
                    self._wait(0, cancel)
                except InterruptedError:
                    self._finish_policy(key, lease)
                    raise
                self.local.remaining -= 1
                response = None
                started = time.monotonic()
                try:
                    response = self._request(session, url, key, timeout, cancel, kwargs)
                    streaming = bool(kwargs.get("stream"))
                    self._guard_response(
                        response, key, lease, started, streaming=streaming, cancel=cancel
                    )
                    if not streaming:
                        # Non-streaming Requests responses are already cached.
                        # Finish their owned cleanup before releasing admission;
                        # JSON/content remain readable from the cached bytes.
                        _ = response.content
                    if response.status_code == 403:
                        self._security_fault(key, lease, "http_403")
                    try:
                        self._observe(
                            key,
                            started,
                            "http",
                            status_code=response.status_code,
                            retry_after=response.headers.get("Retry-After"),
                        )
                    except Exception:
                        response.close()
                        raise
                    self._wait(0, cancel)
                    if 300 <= response.status_code < 400:
                        response.close()
                        raise ValueError("Unexpected redirect")
                    response.raise_for_status()
                    if not streaming:
                        response.close()
                    return response
                except requests.RequestException as exc:
                    if isinstance(exc, requests.exceptions.SSLError):
                        self._security_fault(key, lease, "tls_failure")
                    else:
                        self._finish_policy(key, lease)
                    if response is None:
                        kind = (
                            "tls_failure"
                            if isinstance(exc, requests.exceptions.SSLError)
                            else "timeout"
                            if isinstance(exc, requests.Timeout)
                            else "connection_failure"
                        )
                        self._observe(key, started, kind)
                    status = exc.response.status_code if exc.response is not None else None
                    delay = min(
                        2 ** (self.attempts - self.local.remaining - 1), 30
                    ) + random.uniform(0, 0.2)
                    transient = isinstance(exc, (requests.Timeout, requests.ConnectionError))
                    if status == 429:
                        after = retry_after_seconds(
                            response.headers.get("Retry-After"), datetime.now(UTC)
                        )
                        transient = after is not None
                        if after is not None:
                            delay = max(delay, after)
                    elif status is not None:
                        transient = status in {500, 502, 503, 504}
                    if response is not None:
                        response.close()
                    if isinstance(exc, requests.exceptions.SSLError) or status == 403:
                        raise
                    if not transient or not self.local.remaining:
                        raise
                    self.defer(delay)
                except Exception as exc:
                    if isinstance(
                        exc, (InterruptedError, UnsafePublicSession, PublicEndpointHalted)
                    ):
                        self._finish_policy(key, lease)
                    if response is not None:
                        response.close()
                    raise
            raise RuntimeError("Public request retry budget exhausted")
