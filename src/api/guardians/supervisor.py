"""Durable, fail-closed Guardian proposal supervisor.

SQLite is a dedicated local receipt store, never the application's PostgreSQL.
No execution adapter, shell, network, database repair or deployment exists here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from src.api.guardian_supervisor import GuardianSupervisor

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE = re.compile(r"^[a-zA-Z0-9_./:-]{1,200}$")
ROLES = frozenset(("code", "data", "storage", "exchange"))


@dataclass(frozen=True)
class Receipt:
    sequence: int
    event_key: str | None
    role: str
    status: str
    reason: str
    epoch: int | None = None


class DurableSupervisor:
    """Persist proposals and fenced reservations; never execute proposals.

    Open one instance per process. SQLite BEGIN IMMEDIATE serializes writers.
    A fresh process must pass the same dedicated SQLite file to retain history.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        enabled: bool | None = None,
        max_active: int = 2,
        restart_limit: int = 2,
        restart_window_seconds: int = 3600,
    ):
        if enabled is None:
            enabled = os.environ.get("GUARDIANS_ENABLED", "false").lower() == "true"
        self.enabled = enabled is True
        if type(max_active) is not int or not 1 <= max_active <= 16:
            raise ValueError("invalid max_active")
        if type(restart_limit) is not int or not 0 <= restart_limit <= 10:
            raise ValueError("invalid restart_limit")
        if type(restart_window_seconds) is not int or restart_window_seconds < 1:
            raise ValueError("invalid restart_window_seconds")
        self.max_active = max_active
        self.restart_limit = restart_limit
        self.restart_window_seconds = restart_window_seconds
        self.db = sqlite3.connect(str(db_path), timeout=10, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1), stopped INTEGER NOT NULL)"
        )
        self.db.execute("INSERT OR IGNORE INTO control VALUES (1,0)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS receipts (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT, role TEXT NOT NULL, status TEXT NOT NULL,
            reason TEXT NOT NULL, epoch INTEGER, at REAL NOT NULL,
            previous_hash TEXT NOT NULL, hash TEXT NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS leases (
            scope TEXT PRIMARY KEY, event_key TEXT NOT NULL, epoch INTEGER NOT NULL,
            expires REAL NOT NULL, owner TEXT NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS identities (
            event_key TEXT PRIMARY KEY, role TEXT NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS epochs (
            scope TEXT PRIMARY KEY, last_epoch INTEGER NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS restart_attempts (
            scope TEXT NOT NULL, at REAL NOT NULL)""")

        if not self.verify_chain():
            self.db.close()
            raise RuntimeError("guardian_receipt_chain_corrupt")

    def close(self) -> None:
        self.db.close()

    def _append(
        self, key: str | None, role: str, status: str, reason: str, epoch: int | None = None
    ) -> Receipt:
        # Store only fixed reason codes, never raw event payloads or credentials.
        prev = self.db.execute("SELECT hash FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
        prior = prev[0] if prev else "0" * 64
        at = time.time()
        payload = json.dumps([prior, key, role, status, reason, epoch, at], separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        cur = self.db.execute(
            "INSERT INTO receipts(event_key,role,status,reason,epoch,at,previous_hash,hash) VALUES (?,?,?,?,?,?,?,?)",
            (key, role, status, reason, epoch, at, prior, digest),
        )
        return Receipt(cur.lastrowid, key, role, status, reason, epoch)

    def _begin(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        if not self.verify_chain():
            self.db.execute("ROLLBACK")
            raise RuntimeError("guardian_receipt_chain_corrupt")

    def _stopped(self) -> bool:
        return bool(self.db.execute("SELECT stopped FROM control WHERE id=1").fetchone()[0])

    def kill(self) -> Receipt:
        """Persistently stop admission and invalidate all outstanding leases."""
        self._begin()
        try:
            self.db.execute("UPDATE control SET stopped=1 WHERE id=1")
            self.db.execute("DELETE FROM leases")
            result = self._append(None, "master", "STOPPED", "kill_switch")
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def submit(self, event: Mapping) -> Receipt:
        """Durably admit only a valid deterministic proposal; never act on it."""
        self._begin()
        try:
            if not self.enabled or self._stopped():
                result = self._append(None, "master", "DENIED", "disabled_or_killed")
            elif not isinstance(event, Mapping):
                result = self._append(None, "master", "DENIED", "invalid_event")
            else:
                role = event.get("component")
                scope = event.get("scope")
                evidence = event.get("evidence_ref")
                if (
                    type(role) is not str
                    or role not in ROLES
                    or type(scope) is not str
                    or not _SAFE.fullmatch(scope)
                    or type(evidence) is not str
                    or not _DIGEST.fullmatch(evidence)
                ):
                    result = self._append(None, "master", "DENIED", "invalid_identity")
                else:
                    proposal = GuardianSupervisor(enabled=True).propose(event)
                    if not proposal.allowed or not proposal.key:
                        result = self._append(None, role, "DENIED", proposal.reason)
                    elif self.db.execute(
                        "SELECT 1 FROM identities WHERE event_key=?", (proposal.key,)
                    ).fetchone():
                        result = self._append(proposal.key, role, "DENIED", "duplicate_event")
                    else:
                        self.db.execute("INSERT INTO identities VALUES (?,?)", (proposal.key, role))
                        result = self._append(proposal.key, role, "PROPOSED", proposal.action)
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def reserve(self, receipt: Receipt, *, scope: str, owner: str, ttl: int = 30) -> Receipt:
        """Reserve an unexpired proposal. This is NOT authorization to execute."""
        self._begin()
        try:
            now = time.time()
            if (
                not self.enabled
                or self._stopped()
                or receipt.status != "PROPOSED"
                or not receipt.event_key
                or type(scope) is not str
                or not _SAFE.fullmatch(scope)
                or type(owner) is not str
                or not _SAFE.fullmatch(owner)
                or type(ttl) is not int
                or not 1 <= ttl <= 300
            ):
                result = self._append(None, "master", "DENIED", "invalid_reservation")
            else:
                original = self.db.execute(
                    "SELECT role,status,reason FROM receipts WHERE seq=? AND event_key=?",
                    (receipt.sequence, receipt.event_key),
                ).fetchone()
                identity = self.db.execute(
                    "SELECT role FROM identities WHERE event_key=?", (receipt.event_key,)
                ).fetchone()
                active = self.db.execute(
                    "SELECT COUNT(*) FROM leases WHERE expires>?", (now,)
                ).fetchone()[0]
                existing = self.db.execute(
                    "SELECT expires FROM leases WHERE scope=?", (scope,)
                ).fetchone()
                is_restart = original == ("code", "PROPOSED", "PROPOSE_OWNED_RESTART")
                recent = self.db.execute(
                    "SELECT COUNT(*) FROM restart_attempts WHERE scope=? AND at>?",
                    (scope, now - self.restart_window_seconds),
                ).fetchone()[0]
                if (
                    original != (receipt.role, "PROPOSED", receipt.reason)
                    or identity != (receipt.role,)
                    or self.db.execute(
                        "SELECT 1 FROM receipts WHERE event_key=? AND status='RESERVED'",
                        (receipt.event_key,),
                    ).fetchone()
                ):
                    result = self._append(None, "master", "DENIED", "untrusted_receipt")
                elif existing and existing[0] > now:
                    result = self._append(receipt.event_key, receipt.role, "DENIED", "lease_busy")
                elif active >= self.max_active:
                    result = self._append(receipt.event_key, receipt.role, "DENIED", "backpressure")
                elif is_restart and recent >= self.restart_limit:
                    result = self._append(
                        receipt.event_key, receipt.role, "DENIED", "restart_budget"
                    )
                else:
                    old = self.db.execute(
                        "SELECT last_epoch FROM epochs WHERE scope=?", (scope,)
                    ).fetchone()
                    epoch = (old[0] if old else 0) + 1
                    self.db.execute(
                        "INSERT INTO epochs(scope,last_epoch) VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET last_epoch=excluded.last_epoch",
                        (scope, epoch),
                    )
                    self.db.execute(
                        "INSERT INTO leases(scope,event_key,epoch,expires,owner) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(scope) DO UPDATE SET event_key=excluded.event_key,epoch=excluded.epoch,expires=excluded.expires,owner=excluded.owner",
                        (scope, receipt.event_key, epoch, now + ttl, owner),
                    )
                    if is_restart:
                        self.db.execute("INSERT INTO restart_attempts VALUES (?,?)", (scope, now))
                    result = self._append(
                        receipt.event_key,
                        receipt.role,
                        "RESERVED",
                        "proposal_only_no_execution",
                        epoch,
                    )
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def finish(
        self, reservation: Receipt, *, scope: str, owner: str, verified: bool = False
    ) -> Receipt:
        """Close reservation; success requires independent verifier input.

        The supervisor does not assert that verification was independent.
        A separate verifier integration must establish that trust boundary.
        """
        self._begin()
        try:
            row = self.db.execute(
                "SELECT event_key,epoch,owner,expires FROM leases WHERE scope=?", (scope,)
            ).fetchone()
            valid = (
                self.enabled
                and not self._stopped()
                and reservation.status == "RESERVED"
                and self.db.execute(
                    "SELECT 1 FROM receipts WHERE seq=? AND event_key=? AND role=? "
                    "AND status='RESERVED' AND epoch=?",
                    (
                        reservation.sequence,
                        reservation.event_key,
                        reservation.role,
                        reservation.epoch,
                    ),
                ).fetchone()
                is not None
                and row is not None
                and row[0] == reservation.event_key
                and row[1] == reservation.epoch
                and row[2] == owner
                and row[3] > time.time()
            )
            if valid:
                self.db.execute("DELETE FROM leases WHERE scope=?", (scope,))
            result = self._append(
                reservation.event_key if valid else None,
                reservation.role if valid else "master",
                "CLOSED" if valid else "DENIED",
                "external_verification_reported"
                if valid and verified is True
                else "unverified_or_failed"
                if valid
                else "stale_or_invalid_fence",
                reservation.epoch if valid else None,
            )
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def verify_chain(self) -> bool:
        previous = "0" * 64
        expected_seq = 1
        for seq, key, role, status, reason, epoch, at, prev, digest in self.db.execute(
            "SELECT seq,event_key,role,status,reason,epoch,at,previous_hash,hash FROM receipts ORDER BY seq"
        ):
            payload = json.dumps(
                [previous, key, role, status, reason, epoch, at], separators=(",", ":")
            )
            if (
                seq != expected_seq
                or prev != previous
                or hashlib.sha256(payload.encode()).hexdigest() != digest
            ):
                return False
            expected_seq += 1
            previous = digest
        counter = self.db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='receipts'"
        ).fetchone()
        return (counter[0] if counter else 0) == expected_seq - 1
