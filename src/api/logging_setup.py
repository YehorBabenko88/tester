import json
import logging
import math
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from uuid import UUID

from src.ingest.public_access import public_endpoint_key

EVENTS = frozenset(
    {
        "database_error",
        "job_lease_revoked",
        "job_failed",
        "heartbeat_failed",
        "disk_alert",
        "worker_cycle_failed",
        "worker_stopped",
        "supervisor_started",
        "supervisor_stopped",
        "restart_limit_reached",
        "child_restarted",
        "record",
        "admission_rejected",
        "budget_exceeded",
        "guardian_transport",
    }
)
ERROR_TYPES = frozenset(
    {
        "RuntimeError",
        "ValueError",
        "OSError",
        "OperationalError",
        "ConnectionTimeout",
        "ProviderUnavailable",
        "LeaseLost",
        "ContractError",
        "TimeoutError",
        "PermissionError",
        "ConnectionError",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        # Only explicit safe fields; exception messages may contain DSNs and are not logged.
        result = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.msg
            if isinstance(record.msg, str) and record.msg in EVENTS
            else "unsafe_event",
        }
        # Never stringify arbitrary extras or exception/args text. Correlation identifiers
        # and numeric measurements have a bounded, closed representation.
        for key in ("job_id", "run_id"):
            value = getattr(record, key, None)
            try:
                if isinstance(value, str):
                    result[key] = str(UUID(value))
            except ValueError:
                pass
        trace = getattr(record, "trace_id", None)
        if isinstance(trace, str) and re.fullmatch("[a-f0-9]{32}", trace):
            result["trace_id"] = trace
        component = getattr(record, "component", None)
        if isinstance(component, str) and component in {"main", "api", "worker", "supervisor"}:
            result["component"] = component
        storage = getattr(record, "storage", None)
        if isinstance(storage, str) and storage in {
            "ok",
            "low_disk",
            "quota_exceeded",
            "unavailable",
        }:
            result["storage"] = storage
        error_type = getattr(record, "error_type", None)
        if error_type is not None:
            result["error_type"] = (
                error_type if isinstance(error_type, str) and error_type in ERROR_TYPES else "Error"
            )
        for key in ("free_bytes", "used_bytes"):
            value = getattr(record, key, None)
            if type(value) is int and value >= 0:
                result[key] = value
        if record.msg == "guardian_transport":
            kind = getattr(record, "observation_kind", None)
            if type(kind) is str and kind in {
                "http",
                "timeout",
                "connection_failure",
                "tls_failure",
            }:
                result["observation_kind"] = kind
            event = getattr(record, "guardian_event", None)
            if type(event) is dict and set(event) <= {
                "component",
                "environment",
                "scope",
                "signal",
                "evidence_ref",
                "ownership_verified",
                "retry_after_seconds",
                "attempts",
            }:
                try:
                    scope = event["scope"]
                    identity = scope.removeprefix("bybit/rest/")
                    valid = (
                        event["component"] == "exchange"
                        and event["environment"] == "tester_preview"
                        and scope == "bybit/rest/" + public_endpoint_key("https://" + identity)
                        and type(event["signal"]) is str
                        and event["signal"]
                        in {
                            "untrusted_observation",
                            "disabled",
                            "invalid_observation",
                            "http_403",
                            "http_429",
                            "tls_verification_failed",
                            "transient_timeout",
                            "retry_exhausted",
                            "http_200",
                            "clock_rollback",
                            "duplicate",
                            "circuit_halted",
                            "cooldown",
                            "malformed_response",
                            "source_stale",
                            "unqualified_http_429",
                        }
                        and type(event["evidence_ref"]) is str
                        and re.fullmatch("sha256:[a-f0-9]{64}", event["evidence_ref"])
                        and type(event["ownership_verified"]) is bool
                        and all(
                            type(event[k]) is int and 0 <= event[k] <= 3600
                            for k in ("retry_after_seconds", "attempts")
                            if k in event
                        )
                    )
                    if valid:
                        result["guardian_event"] = dict(event)
                except (ValueError, TypeError, KeyError, AttributeError):
                    pass
            status = getattr(record, "http_status", None)
            if type(status) is int and 100 <= status <= 599:
                result["http_status"] = status
            elapsed = getattr(record, "elapsed_ms", None)
            if type(elapsed) in (int, float) and 0 <= elapsed <= 1e15 and math.isfinite(elapsed):
                result["elapsed_ms"] = elapsed
        return json.dumps(result)


def configure(data_dir, component="platform"):
    if component not in {"platform", "api", "worker", "supervisor"}:
        raise ValueError("Unknown log component")
    directory = data_dir / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tester." + component)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            directory / (component + ".jsonl"), maxBytes=5_000_000, backupCount=4
        )
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        stream = logging.StreamHandler()
        stream.setFormatter(JsonFormatter())
        logger.addHandler(stream)
    return logger
