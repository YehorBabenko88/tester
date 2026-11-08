"""Typed research v2 binding of exact pinned G HTTP client, never G SQLite plane."""

import time
from uuid import uuid4

from src.control.bridge import GatewayClient
from src.control.contracts import validate_command

from .research_control import Command, Control, canonical_digest

G_SOURCE_SHA = "5edb722fc936a56e81a174cfe654839de9a19a9d"


def g_research_envelope(command: Command, actor: str, *, now=None):
    """Validate G v1.1 historical chart action before typed v2 transport extension.

    G's revision integer identifies an overlay revision, not F's code SHA. The
    new request retains its exact code SHA/digest and never calls old lossy G
    validate_overlay. No G local job/config/audit authority is instantiated.
    """
    now = int(time.time()) if now is None else int(now)
    return validate_command(
        dict(
            schema_version="1.1",
            request_id=command.request_id,
            actor=actor,
            scope="synthetic-research",
            correlation_id=str(uuid4()),
            issued_at=now,
            expires_at=now + 300,
            action="chart.overlay",
            category=command.category,
            symbol=command.symbol,
            start_ms=command.start_ms,
            end_ms=command.end_ms,
            snapshot_id=command.snapshot_id,
            params=dict(run_id=command.run_id, revision=0, interval=command.interval),
        ),
        now,
    )


class ResearchGatewayClient(GatewayClient):
    """Actual pinned G loopback transport with fixed new E routes; no new URL input."""

    def research_submit(self, command: Command, *, actor: str):
        command = Command.model_validate(command.model_dump())
        g_research_envelope(command, actor)
        result = self._call("POST", "research", command.model_dump(mode="json"))
        if set(result) != {"job_id", "state"} or result["state"] not in {
            "pending",
            "running",
            "complete",
        }:
            raise ValueError("invalid research job response")
        self._id(result["job_id"])
        return result

    def research_result(self, job_id, command: Command):
        result = self._call("GET", "research/" + self._id(job_id))
        if result.get("job_id") != job_id:
            raise ValueError("job identity mismatch")
        if result.get("state") == "complete":
            if set(result) != {"job_id", "state", "artifact", "artifact_digest"}:
                raise ValueError("invalid result shape")
            artifact = Control._artifact(result["artifact"], command)
            if canonical_digest(artifact) != result["artifact_digest"]:
                raise ValueError("result digest mismatch")
        elif set(result) != {"job_id", "state"} or result["state"] not in {"pending", "running"}:
            raise ValueError("invalid pending shape")
        return result

    def research_ack(self, job_id, artifact_digest, rendered_fill_ids):
        from .research_control import Ack

        ack = Ack(artifact_digest=artifact_digest, rendered_fill_ids=rendered_fill_ids)
        return self._call("POST", "research/" + self._id(job_id) + "/ack", ack.model_dump())
