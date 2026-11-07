"""Foreground supervisor. A stop.request file triggers graceful child shutdown."""

import json
import os
import signal
import subprocess
import sys
import threading
import time

from .config import load_settings
from .jobs import JobQueue
from .logging_setup import configure


def run():
    settings = load_settings()
    logger = configure(settings.data_dir, "supervisor")
    if not settings.dsn:
        raise SystemExit("TESTER_DATABASE_URL is required")
    lock_path = settings.data_dir / "supervisor.lock"
    lock_path.touch(exist_ok=True)
    lock = lock_path.open("r+b")
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("Another supervisor is running") from None
    stop_file = settings.data_dir / "stop.request"
    queue = JobQueue(settings.dsn, settings.lease_seconds, settings.max_pending)
    queue.configure_limits(settings.max_pending, settings.max_running)
    queue.set_draining(False)
    stop_file.unlink(missing_ok=True)
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    children, restarts = {}, {"api": [], "worker": []}
    code = 0

    def launch(component):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        return subprocess.Popen(
            [sys.executable, "-m", "src.api." + component], creationflags=flags
        )  # trusted executable, fixed module names

    try:
        children = {name: launch(name) for name in ("main", "worker")}
        restarts = {name: [] for name in children}
        (settings.data_dir / "supervisor.json").write_text(json.dumps({"pid": os.getpid()}))
        logger.info("supervisor_started")
        while not stopped.wait(0.25) and not stop_file.exists():
            for name, child in list(children.items()):
                if child.poll() is not None:
                    now = time.monotonic()
                    restarts[name] = [t for t in restarts[name] if now - t < 60]
                    if len(restarts[name]) >= 5:
                        logger.error("restart_limit_reached", extra={"component": name})
                        code = 1
                        stopped.set()
                        break
                    logger.warning("child_restarted", extra={"component": name})
                    restarts[name].append(now)
                    if stopped.wait(min(2 ** len(restarts[name]), 8)) or stop_file.exists():
                        break
                    children[name] = launch(name)
    finally:
        # Linearizes shutdown against both API admission and worker claims.
        try:
            queue.set_draining(True)
        except Exception:
            logger.error("database_error")
        stop_file.touch()
        deadline = time.monotonic() + 20
        for child in children.values():
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        (settings.data_dir / "supervisor.json").unlink(missing_ok=True)
        lock.close()
        logger.info("supervisor_stopped")
    return code


if __name__ == "__main__":
    raise SystemExit(run())
