import json
import logging
import threading

from src.api.logging_setup import JsonFormatter
from src.api.worker import process_one


def test_no_claim_after_stop():
    class Queue:
        def claim(self):
            raise AssertionError("claim occurred after stop")

    stop = threading.Event()
    stop.set()
    assert process_one(Queue(), None, stop, logging.getLogger("test")) is False


def test_formatter_never_emits_arbitrary_message_or_sensitive_extras():
    record = logging.LogRecord(
        "tester",
        logging.ERROR,
        "",
        0,
        "postgresql://user:SECRET@localhost/db password=SECRET",
        (),
        None,
    )
    record.job_id = "SECRET"
    record.error_type = "password=SECRET"
    record.trace_id = "a" * 32
    result = json.loads(JsonFormatter().format(record))
    assert "SECRET" not in json.dumps(result)
    assert result["event"] == "unsafe_event"
    assert result["trace_id"] == "a" * 32
