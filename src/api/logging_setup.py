import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler


class JsonFormatter(logging.Formatter):
    def format(self, record):
        # Only explicit safe fields; exception messages may contain DSNs and are not logged.
        result = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for key in ("job_id", "error_type", "component", "storage", "free_bytes", "used_bytes"):
            if hasattr(record, key):
                result[key] = getattr(record, key)
        return json.dumps(result)


def configure(data_dir, component="platform"):
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
