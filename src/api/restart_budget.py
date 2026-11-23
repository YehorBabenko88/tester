"""Crash-durable lifetime launch reservations; never an executor or supervisor.

The installer initializes this journal inside the protected runtime directory.
Every reservation is fsynced before launching a process. A partial append denies
future starts instead of guessing or resetting. No clock or reboot resets limits.
"""

import json
import os
import re
import stat
import time
import uuid
from contextlib import contextmanager

from .lab_install import LabViolation, plain

LIMITS = {"host": 3, "api": 6, "worker": 6}
ENV_KEYS = ("TESTER_RESTART_BUDGET_DIR", "TESTER_INSTALLATION_ID", "TESTER_SOURCE_SHA")


class BudgetDenied(ValueError):
    pass


class RestartBudget:
    def __init__(self, directory, installation_id, source_sha):
        try:
            if str(uuid.UUID(installation_id)) != installation_id or not re.fullmatch(
                "[0-9a-f]{40}", source_sha
            ):
                raise ValueError
            self.directory = plain(directory)
        except (ValueError, TypeError, AttributeError) as exc:
            raise BudgetDenied("Invalid restart budget identity/path") from exc
        self.path = self.directory / "restart-budget.jsonl"
        self.lock = self.directory / "restart-budget.lock"
        self.header = {
            "schema": "tester-restart-budget/v1",
            "installation_id": installation_id,
            "source_sha": source_sha,
            "limits": LIMITS,
        }

    @classmethod
    def from_environment(cls, environment):
        values = [environment.get(key) for key in ENV_KEYS]
        if not any(values):
            return None  # Legacy foreground development keeps its existing policy.
        if not all(values):
            raise BudgetDenied("Incomplete restart budget identity")
        return cls(*values)

    @staticmethod
    def _regular(stream):
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BudgetDenied("Restart budget requires a single regular file")

    @contextmanager
    def _locked(self, *, initialize=False):
        try:
            plain(self.directory)
            plain(self.lock)
            flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            if initialize:
                flags |= os.O_CREAT | os.O_EXCL
            with os.fdopen(os.open(self.lock, flags, 0o600), "r+b", buffering=0) as lock:
                self._regular(lock)
                if os.fstat(lock.fileno()).st_size == 0 and initialize:
                    lock.write(b"0")
                    os.fsync(lock.fileno())
                deadline = time.monotonic() + 5
                while True:
                    try:
                        lock.seek(0)
                        if os.name == "nt":
                            import msvcrt

                            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise BudgetDenied("Restart budget lock unavailable") from None
                        time.sleep(0.01)
                try:
                    if (self.directory / "restart.disabled").exists():
                        raise BudgetDenied("Restarts explicitly disabled")
                    plain(self.path)
                    yield
                finally:
                    lock.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except FileExistsError as exc:
            raise BudgetDenied("Restart budget already exists; reset forbidden") from exc
        except (OSError, LabViolation) as exc:
            raise BudgetDenied("Restart budget storage unavailable") from exc

    @staticmethod
    def _append(stream, value):
        data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        if stream.write(data) != len(data):
            raise BudgetDenied("Incomplete restart reservation")
        stream.flush()
        os.fsync(stream.fileno())

    def initialize(self):
        with self._locked(initialize=True):
            if self.path.exists():
                raise BudgetDenied("Restart budget already exists; reset forbidden")
            with self.path.open("xb", buffering=0) as stream:
                self._append(stream, self.header)

    def _read(self, stream):
        self._regular(stream)
        stream.seek(0)
        data = stream.read(16385)
        try:
            if not data.endswith(b"\n") or len(data) > 16384:
                raise ValueError
            rows = [json.loads(line) for line in data.splitlines()]
            if not rows or rows[0] != self.header:
                raise BudgetDenied("Restart budget identity mismatch")
            counts = dict.fromkeys(LIMITS, 0)
            for sequence, row in enumerate(rows[1:], 1):
                if (
                    not isinstance(row, dict)
                    or set(row) != {"sequence", "role"}
                    or type(row["sequence"]) is not int
                    or row["sequence"] != sequence
                    or row["role"] not in LIMITS
                ):
                    raise ValueError
                counts[row["role"]] += 1
                if counts[row["role"]] > LIMITS[row["role"]]:
                    raise ValueError
            return counts
        except (ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, BudgetDenied):
                raise
            raise BudgetDenied("Corrupt restart budget; automatic reset forbidden") from exc

    def counts(self):
        with self._locked():
            with self.path.open("rb") as stream:
                return self._read(stream)

    def admit(self, role):
        if type(role) is not str or role not in LIMITS:
            raise BudgetDenied("Unknown restart role")
        with self._locked():
            with self.path.open("r+b", buffering=0) as stream:
                counts = self._read(stream)
                if counts[role] >= LIMITS[role]:
                    raise BudgetDenied("Lifetime restart budget exhausted")
                stream.seek(0, os.SEEK_END)
                self._append(stream, {"sequence": sum(counts.values()) + 1, "role": role})
