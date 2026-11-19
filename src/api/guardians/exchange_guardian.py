"""Exchange Guardian: deterministic, offline, proposal-only Bybit REST diagnostics.

No HTTP, database, filesystem, process, or trading operations occur here.
The caller supplies independently measured observations and monotonic time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

HOSTS = frozenset({"api.bybit.com", "public.bybit.com"})
MAX_RETRIES = 3
MAX_BACKOFF = 3600


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def endpoint_key(url: str) -> str:
    """Canonical, credential-free endpoint identity; rejects unsafe URLs."""
    if type(url) is not str or len(url) > 2048:
        raise ValueError("invalid_url")
    try:
        p = urlsplit(url)
        if (p.scheme != "https" or p.hostname not in HOSTS or
                any(segment in {".", ".."} for segment in p.path.split("/")) or
                p.port not in (None, 443) or p.username is not None or
                p.password is not None or p.fragment or
                (not p.path.startswith("/v5/market/") and
                 not (p.hostname == "public.bybit.com" and p.path.startswith("/trading/"))) or
                p.query or p.netloc not in HOSTS):
            raise ValueError("unapproved_endpoint")
    except (ValueError, TypeError) as exc:
        raise ValueError("unapproved_endpoint") from exc
    return p.hostname + p.path


def retry_after_seconds(value: object, now_utc: datetime) -> int | None:
    """Parse delta seconds or HTTP date, rejecting invalid/out-of-budget values."""
    if (type(value) is not str or not value or len(value) > 128 or
            not isinstance(now_utc, datetime) or now_utc.tzinfo is None):
        return None
    if value.isascii() and value.isdecimal():
        seconds = int(value) if len(value) <= 6 else MAX_BACKOFF + 1
    else:
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None or now_utc.tzinfo is None:
                return None
            seconds = math.ceil((dt - now_utc).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None
    return seconds if 1 <= seconds <= MAX_BACKOFF else None


@dataclass(frozen=True)
class Observation:
    endpoint: str
    kind: str
    elapsed_ms: float
    evidence_ref: str
    status_code: int | None = None
    retry_after: str | None = None
    source_age_seconds: float | None = None
    freshness_limit_seconds: float = 120
    parse_valid: bool = True
    source_verified: bool = False


@dataclass(frozen=True)
class Verdict:
    status: str
    signal: str
    proposal: str
    reason: str
    retry_at: float | None
    attempts: int
    circuit: str
    evidence_ref: str
    history_complete: bool = False
    gaps_reconciled: bool = False


class ExchangeGuardian:
    """Per-endpoint fail-closed circuit breaker; state is in-memory only.

    OFF by default. Reinstantiate after restart -> no claimed recovery.
    A real executor must reauthorize proposals using the supervisor.
    """

    def __init__(self, *, enabled: bool = False):
        self.enabled = enabled is True
        self._state: dict[str, dict] = {}

    def inspect(self, observation: Observation, *, now: float,
                now_utc: datetime | None = None) -> Verdict:
        if not self.enabled:
            return Verdict("DISABLED", "disabled", "NONE", "guardians_disabled",
                           None, 0, "CLOSED", "", False, False)
        if not _number(now) or not isinstance(observation, Observation):
            return self._invalid()
        try:
            key = endpoint_key(observation.endpoint)
        except ValueError:
            return self._invalid()
        if (observation.kind not in {"http", "timeout", "tls_failure", "malformed"} or
                not _number(observation.elapsed_ms) or
                type(observation.evidence_ref) is not str or
                len(observation.evidence_ref) != 71 or
                not observation.evidence_ref.startswith("sha256:") or
                any(c not in "0123456789abcdef" for c in observation.evidence_ref[7:]) or
                type(observation.parse_valid) is not bool or
                type(observation.source_verified) is not bool or
                not _number(observation.freshness_limit_seconds) or
                observation.freshness_limit_seconds == 0 or
                (observation.source_age_seconds is not None and
                 not _number(observation.source_age_seconds)) or
                (observation.status_code is not None and
                 (type(observation.status_code) is not int or
                  not 100 <= observation.status_code <= 599))):
            return self._invalid()
        if observation.kind == "http" and observation.status_code is None:
            return self._invalid()
        if observation.kind != "http" and observation.status_code is not None:
            return self._invalid()
        state = self._state.setdefault(key, {"attempts": 0, "until": 0.0,
                                             "circuit": "CLOSED", "seen": set(), "last_now": now})
        # Clock rollback cannot shorten a protective cooldown.
        if now < state["last_now"]:
            state["circuit"] = "HALTED"
            return self._verdict(state, observation, "UNAVAILABLE",
                                 "clock_rollback", "NONE",
                                 "monotonic_clock_rollback", None)
        state["last_now"] = now
        # A TLS fault is a safety signal even during a rate-limit cooldown.
        if observation.kind == "tls_failure" and state["circuit"] != "HALTED":
            state["circuit"] = "HALTED"
            state["seen"].add(observation.evidence_ref)
            return self._verdict(state, observation, "UNAVAILABLE",
                                 "tls_verification_failed", "PROPOSE_STOP_SOURCE",
                                 "tls_fail_closed", None)
        if observation.evidence_ref in state["seen"]:
            return self._verdict(state, observation, "DEGRADED", "duplicate",
                                 "NONE", "duplicate_evidence", None)
        # Reject observations while breaker is open; never bypass a TLS halt.
        if state["circuit"] == "HALTED":
            return self._verdict(state, observation, "UNAVAILABLE", "circuit_halted",
                                 "NONE", "manual_reset_required", None)
        if now < state["until"]:
            return self._verdict(state, observation, "DEGRADED", "cooldown",
                                 "NONE", "backoff_active", state["until"])
        state["seen"].add(observation.evidence_ref)
        if len(state["seen"]) > 128:
            state["seen"] = {observation.evidence_ref}
        if observation.kind == "http" and observation.status_code == 429:
            after = retry_after_seconds(observation.retry_after,
                                        now_utc or datetime.now(UTC))
            if after is None:
                state["circuit"] = "OPEN"
                state["until"] = now + MAX_BACKOFF
                return self._verdict(state, observation, "UNAVAILABLE", "http_429",
                                     "NONE", "unqualified_retry_after", state["until"])
            state["circuit"] = "OPEN"
            state["until"] = now + after
            return self._verdict(state, observation, "DEGRADED", "http_429",
                                 "PROPOSE_RATE_BACKOFF", "verified_retry_after",
                                 state["until"])
        if observation.kind == "timeout" or (
                observation.kind == "http" and observation.status_code in {500, 502, 503, 504}):
            state["attempts"] += 1
            if state["attempts"] >= MAX_RETRIES:
                state["circuit"] = "OPEN"
                state["until"] = now + MAX_BACKOFF
                return self._verdict(state, observation, "UNAVAILABLE",
                                     "retry_exhausted", "NONE", "circuit_open",
                                     state["until"])
            delay = min(2 ** (state["attempts"] - 1), 30)
            state["until"] = now + delay
            return self._verdict(state, observation, "DEGRADED",
                                 "transient_timeout", "PROPOSE_BOUNDED_RETRY",
                                 "bounded_retry_proposal", state["until"])
        if observation.kind == "malformed" or (
                observation.kind == "http" and (
                    observation.status_code != 200 or not observation.parse_valid)):
            state["circuit"] = "OPEN"
            state["until"] = now + MAX_BACKOFF
            return self._verdict(state, observation, "UNAVAILABLE",
                                 "malformed_response", "NONE",
                                 "response_untrusted", state["until"])
        if (observation.source_age_seconds is None or
                not observation.source_verified or
                observation.source_age_seconds > observation.freshness_limit_seconds):
            return self._verdict(state, observation, "UNAVAILABLE",
                                 "source_stale", "PROPOSE_MARK_UNAVAILABLE",
                                 "freshness_not_verified", None)
        state["attempts"] = 0
        state["until"] = 0.0
        state["circuit"] = "CLOSED"
        return self._verdict(state, observation, "MONITORING", "http_200",
                             "NONE", "transport_healthy_history_unverified", None)

    def reset_tls_halt(self, endpoint: str, *, manual_authorized: bool) -> bool:
        """Manual gate only; cannot assert source freshness or historical coverage."""
        if manual_authorized is not True:
            return False
        try:
            key = endpoint_key(endpoint)
        except ValueError:
            return False
        if key not in self._state or self._state[key]["circuit"] != "HALTED":
            return False
        self._state.pop(key)
        return True

    @staticmethod
    def _invalid() -> Verdict:
        return Verdict("UNAVAILABLE", "invalid_observation", "NONE",
                       "fail_closed", None, 0, "OPEN", "")

    @staticmethod
    def _verdict(state: dict, observation: Observation, status: str, signal: str,
                 proposal: str, reason: str, retry_at: float | None) -> Verdict:
        return Verdict(status, signal, proposal, reason, retry_at,
                       state["attempts"], state["circuit"],
                       observation.evidence_ref)


def supervisor_event(observation: Observation, verdict: Verdict, *, now: float) -> dict:
    """Minimal sanitized event; never contains query strings or raw HTTP bodies."""
    scope = endpoint_key(observation.endpoint)
    event = {"component": "exchange", "environment": "tester_preview",
             "scope": "bybit/rest/" + scope, "signal": verdict.signal,
             "evidence_ref": verdict.evidence_ref,
             "ownership_verified": True}
    if verdict.signal == "http_429" and verdict.proposal == "PROPOSE_RATE_BACKOFF":
        event["retry_after_seconds"] = math.ceil((verdict.retry_at or 0) - now)
    if verdict.signal == "http_429" and verdict.proposal != "PROPOSE_RATE_BACKOFF":
        event["signal"] = "unqualified_http_429"
    if verdict.signal == "transient_timeout":
        event["attempts"] = verdict.attempts - 1
    return event
