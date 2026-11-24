"""Explicit proposal-only bridges to MASTER; no worker, network or effector.

Ownership/telemetry flags are caller attestations, never proofs manufactured here.
No installed tester_lab identity is accepted. Admission is not execution authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from src.api.guardian_supervisor import GuardianSupervisor
from src.api.guardians.code_guardian import Result
from src.api.guardians.data_guardian import Finding, to_policy_event
from src.api.guardians.exchange_guardian import Observation, Verdict, supervisor_event
from src.api.guardians.supervisor import DurableSupervisor, Receipt


@dataclass(frozen=True)
class Admission:
    receipt: Receipt | None
    reason: str

    @property
    def execution_authorized(self) -> bool:
        return False


class ProposalBridge:
    """Sanitize child proposals, then delegate all durable admission to MASTER.

    Explicit opt-in is required in addition to the supervisor's default-off gate.
    This object cannot authenticate an attestor, reserve resources or execute work.
    """

    def __init__(self, supervisor: DurableSupervisor, *, enabled: bool = False):
        self.supervisor = supervisor
        self.enabled = enabled is True

    def _gate(self, environment: object, ownership_verified: object) -> Admission | None:
        if not self.enabled:
            return Admission(None, "bridge_disabled")
        if environment != "tester_preview":
            return Admission(None, "environment_out_of_scope")
        if ownership_verified is not True:
            return Admission(None, "ownership_unverified")
        return None

    def _submit(self, event: object, role: str, action: str) -> Admission:
        if not isinstance(event, Mapping) or event.get("component") != role:
            return Admission(None, "invalid_child_proposal")
        # Dataclass annotations are not runtime validation. Do not pass mutable
        # or unhashable discriminator fields into the pure policy classifier.
        if any(
            type(event.get(key)) is not str
            for key in ("component", "signal", "environment", "scope", "evidence_ref")
        ):
            return Admission(None, "invalid_child_proposal")
        proposal = GuardianSupervisor(enabled=True).propose(event)
        if not proposal.allowed or proposal.action != action:
            return Admission(None, "invalid_child_proposal")
        receipt = self.supervisor.submit(event)
        return Admission(receipt, receipt.reason)

    def code(
        self, result: Result, *, environment: str, ownership_verified: bool = False
    ) -> Admission:
        denied = self._gate(environment, ownership_verified)
        if denied:
            return denied
        # request_code_review produces review metadata, not an admitted incident.
        if not isinstance(result, Result) or result.action != "PROPOSE_OWNED_RESTART":
            return Admission(None, "invalid_child_proposal")
        if not isinstance(result.event, Mapping):
            return Admission(None, "invalid_child_proposal")
        event = {
            key: result.event.get(key)
            for key in (
                "component",
                "signal",
                "environment",
                "scope",
                "evidence_ref",
                "ownership_verified",
                "consecutive_failed_probes",
                "restarts_last_hour",
            )
        }
        if (
            type(result.fingerprint) is not str
            or re.fullmatch(r"[0-9a-f]{64}", result.fingerprint) is None
            or event["evidence_ref"] != "sha256:" + result.fingerprint
        ):
            return Admission(None, "invalid_child_proposal")
        return self._submit(event, "code", result.action)

    def data(
        self,
        snapshot: Mapping,
        finding: Finding,
        *,
        environment: str,
        ownership_verified: bool = False,
    ) -> Admission:
        denied = self._gate(environment, ownership_verified)
        if denied:
            return denied
        if (
            not isinstance(finding, Finding)
            or any(
                type(value) is not str
                for value in (finding.action, finding.reason, finding.scope, finding.evidence_ref)
            )
            or not isinstance(finding.proposal, Mapping)
        ):
            return Admission(None, "invalid_child_proposal")
        try:
            event = to_policy_event(snapshot, finding)
        except (TypeError, ValueError, OverflowError):
            return Admission(None, "invalid_child_proposal")
        return self._submit(event, "data", finding.action)

    def exchange(
        self,
        observation: Observation,
        verdict: Verdict,
        *,
        now: float,
        environment: str,
        ownership_verified: bool = False,
    ) -> Admission:
        denied = self._gate(environment, ownership_verified)
        if denied:
            return denied
        if (
            not isinstance(observation, Observation)
            or not isinstance(verdict, Verdict)
            or type(now) not in (int, float)
            or not math.isfinite(now)
            or now < 0
        ):
            return Admission(None, "invalid_child_proposal")
        if (
            any(
                type(value) is not str
                for value in (
                    verdict.proposal,
                    verdict.signal,
                    verdict.evidence_ref,
                    verdict.circuit,
                    verdict.status,
                )
            )
            or type(verdict.attempts) is not int
            or verdict.attempts < 0
            or (
                verdict.retry_at is not None
                and (
                    type(verdict.retry_at) not in (int, float)
                    or not math.isfinite(verdict.retry_at)
                    or verdict.retry_at < 0
                )
            )
        ):
            return Admission(None, "invalid_child_proposal")
        if verdict.proposal not in {
            "PROPOSE_STOP_SOURCE",
            "PROPOSE_RATE_BACKOFF",
            "PROPOSE_BOUNDED_RETRY",
            "PROPOSE_MARK_UNAVAILABLE",
        }:
            return Admission(None, "invalid_child_proposal")
        try:
            event = supervisor_event(observation, verdict, now=now, ownership_verified=True)
        except (ValueError, TypeError, OverflowError):
            return Admission(None, "invalid_child_proposal")
        return self._submit(event, "exchange", verdict.proposal)

    def storage(
        self,
        manifest: Mapping,
        *,
        scope: str,
        min_free_bytes: int,
        environment: str,
        ownership_verified: bool = False,
        disk_telemetry_verified: bool = False,
    ) -> Admission:
        """Map independently attested low-disk inventory to pause proposal.

        Cleanup remains advisory: inventory cannot establish current file age,
        ownership or close a filesystem race, so no cleanup event is fabricated.
        """
        denied = self._gate(environment, ownership_verified)
        if denied:
            return denied
        if not isinstance(manifest, Mapping) or disk_telemetry_verified is not True:
            return Admission(None, "storage_evidence_unverified")
        digest = manifest.get("evidence_sha256")
        if (
            manifest.get("schema") != "storage-guardian/v1"
            or manifest.get("enabled") is not True
            or manifest.get("status") != "OBSERVED"
            or type(digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or type(min_free_bytes) is not int
            or min_free_bytes < 0
            or type(manifest.get("free_bytes")) is not int
            or not 0 <= manifest["free_bytes"] < min_free_bytes
            or type(manifest.get("proposals")) is not list
            or "PROPOSE_PAUSE_NEW_DOWNLOADS" not in manifest["proposals"]
        ):
            return Admission(None, "storage_evidence_unverified")
        try:
            payload = json.dumps(
                {k: v for k, v in manifest.items() if k != "evidence_sha256"},
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (ValueError, TypeError):
            return Admission(None, "storage_evidence_unverified")
        if hashlib.sha256(payload.encode()).hexdigest() != digest:
            return Admission(None, "storage_evidence_unverified")
        event = dict(
            component="storage",
            signal="low_disk",
            environment=environment,
            scope=scope,
            evidence_ref="sha256:" + digest,
            ownership_verified=True,
            disk_telemetry_verified=True,
        )
        return self._submit(event, "storage", "PROPOSE_PAUSE_DOWNLOADS")

    @staticmethod
    def authorize_execution(admission: Admission) -> None:
        raise PermissionError("Guardian integration is proposal-only; execution unavailable")
