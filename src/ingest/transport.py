"""Single read-only transport with bounded logical budgets and public scope."""

import math
import random
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime

import requests

from src.api.guardians.exchange_guardian import retry_after_seconds

from .public_access import public_endpoint_key


class Transport:
    def __init__(self, session=None, attempts=3, rate=3, sleep=time.sleep):
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
        self.next_request = 0
        self.halted = set()

    def reset_halt(self, url, *, manual_authorized):
        key = public_endpoint_key(url)
        if manual_authorized is not True:
            raise ValueError("Manual review required")
        with self.lock:
            self.halted.discard(key)

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

    def get(self, url, *, cancel=None, **kwargs):
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
        with self.operation():
            while self.local.remaining:
                with self.lock:
                    if key in self.halted or len(self.halted) >= 128:
                        raise ValueError("Public endpoint halted; manual review required")
                self._wait(0, cancel)
                with self.lock:
                    now = time.monotonic()
                    wait = max(0, self.next_request - now)
                    self.next_request = max(now, self.next_request) + 1 / self.rate
                self._wait(wait, cancel)
                self.local.remaining -= 1
                response = None
                try:
                    if self.session is None and not hasattr(self.local, "session"):
                        self.local.session = requests.Session()
                        # Never inherit .netrc credentials, environment proxies or auth.
                        self.local.session.trust_env = False
                    session = self.session if self.session is not None else self.local.session
                    response = session.request(
                        "GET", url, timeout=timeout, allow_redirects=False, **kwargs
                    )
                    if 300 <= response.status_code < 400:
                        response.close()
                        raise ValueError("Unexpected redirect")
                    response.raise_for_status()
                    return response
                except requests.RequestException as exc:
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
                        with self.lock:
                            self.halted.add(key)
                        raise
                    if not transient or not self.local.remaining:
                        raise
                    self.defer(delay)
            raise RuntimeError("Public request retry budget exhausted")
