import json
import logging
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from uuid import UUID

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
