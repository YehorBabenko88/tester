import signal
import threading
from uuid import uuid4

from .config import load_settings
from .jobs import JobQueue, LeaseLost
from .logging_setup import configure
from .maintenance import storage_status
from .providers import ProviderUnavailable, load_factory


class UnavailableHandler:
    def run(self, request, checkpoint, report, stop, commit):
        raise ProviderUnavailable("ingestion")


def process_one(queue, handler, stop, logger):
    if stop.is_set():
        return False
    lease = queue.claim()
    if lease is None:
        return False
    if stop.is_set():
        queue.release(lease.id, lease.lease_token)
        return False
    cancelled = threading.Event()
    finished = threading.Event()

    def guard():
        while not finished.wait(0.25):
            try:
                if stop.is_set() or not queue.active(lease.id, lease.lease_token):
                    cancelled.set()
                    return
            except Exception:
                # Failure to verify ownership must stop side effects, not permit them.
                cancelled.set()
                return

    watcher = threading.Thread(target=guard, daemon=True)
    watcher.start()

    def report(checkpoint, progress):
        if stop.is_set() or cancelled.is_set():
            raise InterruptedError("shutdown")
        queue.checkpoint(lease.id, lease.lease_token, checkpoint, progress)

    def commit(write, checkpoint, progress):
        if stop.is_set() or cancelled.is_set():
            raise InterruptedError("job cancelled")
        queue.commit(lease.id, lease.lease_token, write, checkpoint, progress)

    try:
        handler.run(lease.request, lease.checkpoint, report, cancelled, commit)
        if stop.is_set() or cancelled.is_set():
            queue.release(lease.id, lease.lease_token)
        else:
            queue.finish(lease.id, lease.lease_token)
    except InterruptedError:
        queue.release(lease.id, lease.lease_token)
    except LeaseLost:
        logger.info("job_lease_revoked", extra={"job_id": str(lease.id)})
    except Exception as exc:
        # Never persist exception strings: upstream errors can contain credentials or URLs.
        code = "provider_unavailable" if isinstance(exc, ProviderUnavailable) else "handler_failed"
        try:
            queue.finish(lease.id, lease.lease_token, error=code)
        except LeaseLost:
            pass
        logger.error(
            "job_failed", extra={"job_id": str(lease.id), "error_type": type(exc).__name__}
        )
    finally:
        finished.set()
        watcher.join(timeout=4)
    return True


def run():
    settings = load_settings()
    logger = configure(settings.data_dir, "worker")
    if not settings.dsn:
        raise SystemExit("TESTER_DATABASE_URL is required")
    queue = JobQueue(settings.dsn, settings.lease_seconds)
    handler = load_factory(settings.handler_factory, UnavailableHandler())
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: stop.set())
    worker_id = uuid4()

    def heartbeat():
        while not stop.is_set():
            if (settings.data_dir / "stop.request").exists():
                stop.set()
                break
            try:
                queue.heartbeat(worker_id)
            except Exception as exc:
                logger.error("heartbeat_failed", extra={"error_type": type(exc).__name__})
            stop.wait(5)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        while not stop.is_set():
            if (settings.data_dir / "stop.request").exists():
                stop.set()
                break
            try:
                storage = storage_status(settings)
                if storage["storage"] != "ok":
                    logger.warning("disk_alert", extra=storage)
                    stop.wait(5)
                elif not process_one(queue, handler, stop, logger):
                    stop.wait(1)
            except Exception as exc:
                logger.error("worker_cycle_failed", extra={"error_type": type(exc).__name__})
                stop.wait(3)
    finally:
        stop.set()
        thread.join(timeout=4)
        logger.info("worker_stopped")


if __name__ == "__main__":
    run()
