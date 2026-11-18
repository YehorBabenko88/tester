"""Deterministic guardian proposal coordinator (P0: no action execution).

Inspired by MUE-X swarm roles, but deliberately omits source mutation, shells,
network, service control and arbitrary SQL. This module NEVER performs I/O.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from .guardian_policy import decide

EVIDENCE = re.compile(r"^sha256:[0-9a-f]{64}$")
ACTION_OWNERS = {
    "code": {"PROPOSE_OWNED_RESTART", "REQUEST_CODE_REVIEW"},
    "data": {"PROPOSE_BOUNDED_BACKFILL", "PROPOSE_QUARANTINE"},
    "storage": {"PROPOSE_TEMP_CLEANUP", "PROPOSE_PAUSE_DOWNLOADS"},
    "exchange": {
        "PROPOSE_STOP_SOURCE",
        "PROPOSE_RATE_BACKOFF",
        "PROPOSE_BOUNDED_RETRY",
        "PROPOSE_MARK_UNAVAILABLE",
    },
}


@dataclass(frozen=True)
class Proposal:
    key: str | None
    component: str
    action: str
    reason: str
    allowed: bool


class GuardianSupervisor:
    """Create reproducible proposals; no action is ever executed here."""

    def __init__(self, *, enabled: bool = False):
        self.enabled = enabled is True
        self._seen = set()

    def propose(self, event: Mapping) -> Proposal:
        if not self.enabled:
            return Proposal(None, "", "ALERT", "guardians_disabled", False)
        if not isinstance(event, Mapping):
            return Proposal(None, "", "ALERT", "invalid_event", False)
        component = event.get("component")
        if type(component) is not str or component not in ACTION_OWNERS:
            return Proposal(None, "", "ALERT", "unknown_component", False)
        evidence = event.get("evidence_ref")
        if not isinstance(evidence, str) or not EVIDENCE.fullmatch(evidence):
            return Proposal(None, component, "ALERT", "invalid_evidence_digest", False)
        scope = event.get("scope")
        if type(scope) is not str or not 0 < len(scope) <= 200:
            return Proposal(None, component, "ALERT", "invalid_scope", False)
        if event.get("environment") != "tester_preview":
            return Proposal(None, component, "ALERT", "environment_out_of_scope", False)
        decision = decide(event)
        if decision.action not in ACTION_OWNERS[component]:
            return Proposal(None, component, "ALERT", decision.reason, False)
        try:
            payload = json.dumps(
                {
                    "component": component,
                    "signal": event.get("signal"),
                    "scope": scope,
                    "evidence_ref": evidence,
                    "action": decision.action,
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            return Proposal(None, component, "ALERT", "invalid_event_identity", False)
        key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if key in self._seen:
            return Proposal(key, component, "ALERT", "duplicate_event", False)
        self._seen.add(key)
        return Proposal(key, component, decision.action, decision.reason, True)
