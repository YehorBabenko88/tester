"""Code Guardian P0: pure fail-closed incident decisions, no runtime effects."""

import hashlib
import json
import re
from dataclasses import dataclass

HEX40 = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SOURCES = frozenset(("scm", "http_health", "worker_heartbeat"))


@dataclass(frozen=True)
class Identity:
    service: str
    expected_service: str
    head: str
    expected_head: str
    sid_verified: bool
    image_verified: bool
    config_verified: bool
    lease_clear: bool

    def owned(self):
        return (
            isinstance(self.service, str)
            and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", self.service))
            and self.service == self.expected_service
            and isinstance(self.head, str)
            and bool(HEX40.fullmatch(self.head))
            and self.head == self.expected_head
            and self.sid_verified is True
            and self.image_verified is True
            and self.config_verified is True
            and self.lease_clear is True
        )


@dataclass(frozen=True)
class Probe:
    source: str
    sample_id: str
    timestamp: int
    healthy: bool
    evidence: str

    def valid(self, now):
        return (
            type(self.source) is str
            and self.source in SOURCES
            and isinstance(self.sample_id, str)
            and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.sample_id))
            and type(self.timestamp) is int
            and type(now) is int
            and 0 <= now - self.timestamp <= 30
            and type(self.healthy) is bool
            and isinstance(self.evidence, str)
            and bool(DIGEST.fullmatch(self.evidence))
        )


@dataclass(frozen=True)
class Result:
    action: str
    reason: str
    fingerprint: str = ""
    event: dict | None = None


class CodeGuardian:
    """Only proposes action; executor, attestor and durable budget are external."""

    def __init__(self, *, enabled=False):
        self.enabled = enabled is True
        self._seen = set()

    def evaluate(self, identity, probes, *, now, restarts_last_hour, circuit_open=False):
        if not self.enabled:
            return Result("ALERT", "disabled")
        if not isinstance(identity, Identity) or not identity.owned():
            return Result("ALERT", "ownership_unverified")
        if type(now) is not int or type(restarts_last_hour) is not int or restarts_last_hour < 0:
            return Result("ALERT", "invalid_metrics")
        if type(circuit_open) is not bool or circuit_open:
            return Result("ALERT", "circuit_open")
        if not isinstance(probes, tuple) or len(probes) != 3:
            return Result("ALERT", "three_probes_required")
        if any(not isinstance(p, Probe) or not p.valid(now) for p in probes):
            return Result("ALERT", "invalid_or_stale_probe")
        if {p.source for p in probes} != SOURCES or len({p.sample_id for p in probes}) != 3:
            return Result("ALERT", "independence_unverified")
        if len({p.evidence for p in probes}) != 3:\n            return Result("ALERT", "evidence_not_independent")\n        if any(p.healthy for p in probes):
            return Result("ALERT", "failure_unconfirmed")
        if restarts_last_hour >= 2:
            return Result("ALERT", "restart_budget_exhausted")
        payload = json.dumps(
            {"service": identity.service, "head": identity.head,
             "evidence": sorted(p.evidence for p in probes)},
            sort_keys=True, separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if fingerprint in self._seen:
            return Result("ALERT", "duplicate_incident", fingerprint)
        self._seen.add(fingerprint)
        return Result("PROPOSE_OWNED_RESTART", "independent_failures", fingerprint, {
            "schema_version": "guardian-event/v1",
            "component": "code",
            "signal": "service_unhealthy",
            "environment": "tester_preview",
            "scope": identity.service,
            "evidence_ref": "sha256:" + fingerprint,
            "ownership_verified": True,
            "consecutive_failed_probes": 3,
            "restarts_last_hour": restarts_last_hour,
        })

    @staticmethod
    def verify(identity, probes, *, now, independent_verifier=False):
        if not isinstance(identity, Identity) or not identity.owned():
            return Result("ALERT", "ownership_unverified")
        if independent_verifier is not True:
            return Result("ALERT", "independent_verifier_missing")
        if not isinstance(probes, tuple) or len(probes) != 3:
            return Result("ALERT", "after_check_missing")
        if any(not isinstance(p, Probe) or not p.valid(now) or not p.healthy for p in probes):
            return Result("ALERT", "after_check_failed")
        if {p.source for p in probes} != SOURCES or len({p.sample_id for p in probes}) != 3:
            return Result("ALERT", "after_check_not_independent")
        if len({p.evidence for p in probes}) != 3:\n            return Result("ALERT", "after_check_evidence_not_independent")\n        return Result("VERIFIED_RECOVERY", "independent_after_check_passed")


def request_code_review(error_type, evidence_ref, head):
    """Never copy raw exception content or execute a suggested patch."""
    if (not isinstance(error_type, str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", error_type)
        or not isinstance(evidence_ref, str)
        or not DIGEST.fullmatch(evidence_ref)
        or not isinstance(head, str)
        or not HEX40.fullmatch(head)):
        return Result("ALERT", "untrusted_error")
    return Result("REQUEST_CODE_REVIEW", "draft_pr_and_manual_gate", event={
        "error_type": error_type, "evidence_ref": evidence_ref,
        "source_head": head, "allowed": "isolated_tests_only",
    })
