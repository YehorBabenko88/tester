"""Bounded service lifecycle, dependency-injected for tests. No native imports."""

import http.client
import json
import time

from .physical_scope import failure_budget, plain_path


def probe_health(expected_instance):
    connection = http.client.HTTPConnection("127.0.0.1", 18765, timeout=1)
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        raw = response.read(65537)
        if len(raw) > 65536 or response.status not in {200, 503}:
            return None
        data = json.loads(raw)
        if data.get("schema_version") != "1.0":
            return None
        result = data.get("data")
        if not isinstance(result, dict) or result.get("instance_id") != expected_instance:
            return None
        return result
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def supervise(
    runtime,
    stop,
    spawn,
    health,
    report,
    *,
    clock=time.monotonic,
    wait=time.sleep,
    start_timeout=60,
    stop_timeout=30,
):
    runtime = plain_path(runtime)
    failure_budget(runtime)
    child = None
    failed = None
    try:
        report("starting")
        child = spawn()
        deadline = clock() + start_timeout
        while not stop.is_set():
            if child.poll() is not None:
                failed = "supervisor_exited"
                raise RuntimeError(failed)
            report("starting")
            data = health()
            if isinstance(data, dict) and all(
                data.get(k) == "ok" for k in ("database", "worker", "storage")
            ):
                # Operational readiness only; provider availability is exposed separately.
                (runtime / "scm-readiness.json").write_text(json.dumps(data), encoding="utf-8")
                report("running")
                break
            if clock() >= deadline:
                failed = "readiness_timeout"
                raise RuntimeError(failed)
            wait(1)
        while not stop.is_set():
            if child.poll() is not None:
                failed = "supervisor_exited"
                raise RuntimeError(failed)
            wait(0.25)
    except Exception:
        if failed is None:
            failed = "bootstrap_failed"
        failure_budget(runtime, record=failed)
        raise
    finally:
        if child is not None:
            report("stopping")
            plain_path(runtime / "stop.request").touch()
            deadline = clock() + stop_timeout
            while child.poll() is None and clock() < deadline:
                report("stopping")
                wait(1)
            timed_out = child.poll() is None
            if timed_out:
                child.kill_tree()
            child.close()
            if timed_out and failed is None:
                failure_budget(runtime, record="shutdown_timeout")
                raise RuntimeError("shutdown_timeout")
