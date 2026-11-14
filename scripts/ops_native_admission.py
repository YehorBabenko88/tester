"""Opt-in native Windows launcher *admission*, no process spawning.

A crash cannot be silently recovered from saved PID/JSON alone. This guard
makes create-only reservations and refuses unresolved old generations.
No PC4 operations, no SCM, no OpenProcess(pid), no kill/terminate.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Callable, Mapping

from scripts.ops_native_windows_witness import NativeRestartBlocked


@dataclass(frozen=True)
class NativePin:
    source_sha: str
    config_sha256: str
    root: str
    pg_system_identifier: str
    pg_postmaster_start: str

    def verify(self) -> None:
        if not isinstance(self.source_sha, str) or len(self.source_sha) != 40:
            raise NativeRestartBlocked("Full source SHA required")
        if not isinstance(self.config_sha256, str) or len(self.config_sha256) != 64:
            raise NativeRestartBlocked("Full configuration digest required")
        if not self.pg_system_identifier or not self.pg_postmaster_start:
            raise NativeRestartBlocked("PostgreSQL incarnation missing")
        if not self.root:
            raise NativeRestartBlocked("Pinned root required")


def _canonical(value: Mapping) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Mapping) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _read_exact(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_048_576:
        raise NativeRestartBlocked("Missing/unsafe lifecycle receipt")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NativeRestartBlocked("Invalid lifecycle receipt") from exc
    if not isinstance(value, dict):
        raise NativeRestartBlocked("Lifecycle receipt must be object")
    return value


def _new(path: Path, value: dict) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise NativeRestartBlocked("Symlink reservation path refused")
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write(_canonical(value).decode("utf-8"))
            file.flush()
            os.fsync(file.fileno())
    except FileExistsError as exc:
        raise NativeRestartBlocked("Reservation collision or reused evidence") from exc


class NativeAdmission:
    """Create-only reservation with strict previous generation proof.

    Durable crash recovery deliberately REFUSES if original OS handles were
    lost. Another controller cannot adopt a foreign/old PID from a receipt.
    """

    def __init__(self, root: Path, pin: NativePin):
        pin.verify()
        if root.is_symlink() or not root.is_dir():
            raise NativeRestartBlocked("Owned native root required")
        self.root = root.resolve(strict=True)
        if str(self.root) != pin.root:
            raise NativeRestartBlocked("Native root differs from pin")
        self.pin = pin
        self.generations = self.root / "native-launch-generations"
        if self.generations.is_symlink():
            raise NativeRestartBlocked("Symlink generation root refused")
        self.generations.mkdir(mode=0o700, exist_ok=True)

    def _names(self) -> list[int]:
        entries = list(self.generations.iterdir())
        if any(item.is_symlink() or not item.is_dir()
               or len(item.name) != 4 or not item.name.isdigit()
               for item in entries):
            raise NativeRestartBlocked("Unknown/native generation entry")
        numbers = sorted(int(item.name) for item in entries)
        if numbers != list(range(len(numbers))) or len(numbers) >= 32:
            raise NativeRestartBlocked("Native generation gap/limit")
        return numbers

    def next_generation(self, *, previous_exit_validator: Callable[[int], bool],
                        pg_probe: Callable[[], Mapping],
                        leases_probe: Callable[[], Mapping[str, str]]) -> int:
        numbers = self._names()
        if numbers:
            # Every generation must be closed and accepted by the still-live
            # original-handle observer. A restarted controller cannot invent it.
            for i in numbers:
                folder = self.generations / f"{i:04d}"
                if {child.name for child in folder.iterdir()} != {
                    "reservation.json", "closure.json"
                }:
                    raise NativeRestartBlocked("Incomplete native generation")
                reservation = _read_exact(folder / "reservation.json")
                closure = _read_exact(folder / "closure.json")
                if (reservation.get("schema") != "ops-native-reservation/1"
                        or reservation.get("generation") != i
                        or reservation.get("pin") != vars(self.pin)
                        or closure.get("schema") != "ops-native-closure-link/1"
                        or closure.get("generation") != i
                        or closure.get("reservation_sha256") != _digest(reservation)
                        or not isinstance(closure.get("native_exit_sha256"), str)
                        or len(closure["native_exit_sha256"]) != 64
                        or closure.get("pg_system_identifier") != self.pin.pg_system_identifier
                        or closure.get("pg_postmaster_start") != self.pin.pg_postmaster_start
                        or closure.get("restart_allowed") is not True
                        or not previous_exit_validator(i)):
                    raise NativeRestartBlocked("Unproven prior native closure")
        pg = dict(pg_probe())
        if (pg.get("pg_system_identifier") != self.pin.pg_system_identifier
                or pg.get("pg_postmaster_start") != self.pin.pg_postmaster_start):
            raise NativeRestartBlocked("PostgreSQL incarnation changed")
        states = dict(leases_probe())
        if any(state not in {"queued", "completed", "failed", "paused"} for state in states.values()):
            raise NativeRestartBlocked("Active/unverified PostgreSQL lease")
        return len(numbers)

    def reserve(self, generation: int, *, previous_exit_validator: Callable[[int], bool],
                pg_probe: Callable[[], Mapping],
                leases_probe: Callable[[], Mapping[str, str]]) -> Path:
        expected = self.next_generation(
            previous_exit_validator=previous_exit_validator,
            pg_probe=pg_probe,
            leases_probe=leases_probe,
        )
        if generation != expected:
            raise NativeRestartBlocked("Out-of-order native reservation")
        folder = self.generations / f"{generation:04d}"
        try:
            folder.mkdir(mode=0o700)  # atomic exclusive claim
        except FileExistsError as exc:
            raise NativeRestartBlocked("Concurrent native launcher reservation") from exc
        _new(folder / "reservation.json", {
            "schema": "ops-native-reservation/1",
            "generation": generation,
            "pin": vars(self.pin),
            "restart_permitted_without_witness": False,
        })
        return folder

    def seal_reservation(self, generation: int, *,
                         witness, receipt: dict,
                         pg_probe: Callable[[], Mapping],
                         leases_probe: Callable[[], Mapping[str, str]]) -> dict:
        """Require the *actual* original-handle witness, not caller booleans.

        This is still an opt-in fixture API. Original process handles are
        external capabilities which cannot be recreated from saved PID files.
        """
        folder = self.generations / f"{generation:04d}"
        record = _read_exact(folder / "reservation.json")
        if (record.get("schema") != "ops-native-reservation/1"
                or record.get("pin") != vars(self.pin)
                or record.get("generation") != generation
                or witness.generation != generation or witness.root != self.root):
            raise NativeRestartBlocked("Native reservation/witness identity differs")
        if {birth.role for birth, _ in witness.items} != {"api", "worker"}:
            raise NativeRestartBlocked("Missing native API/worker proof")
        if any(birth.config_sha256 != self.pin.config_sha256
               for birth, _ in witness.items):
            raise NativeRestartBlocked("Native child configuration differs")
        # Fail closed if original handles are gone, processes remain live,
        # or either sealed checkpoint was changed after native exit.
        witness.require_restart_allowed(receipt)
        pg = dict(pg_probe())
        if (pg.get("pg_system_identifier") != self.pin.pg_system_identifier
                or pg.get("pg_postmaster_start") != self.pin.pg_postmaster_start):
            raise NativeRestartBlocked("PostgreSQL incarnation changed after native exit")
        states = dict(leases_probe())
        if any(state not in {"queued", "completed", "failed", "paused"}
               for state in states.values()):
            raise NativeRestartBlocked("Unfenced lease after native exit")
        closure = {
            "schema": "ops-native-closure-link/1", "generation": generation,
            "reservation_sha256": _digest(record),
            "native_exit_sha256": _digest(receipt),
            "pg_system_identifier": self.pin.pg_system_identifier,
            "pg_postmaster_start": self.pin.pg_postmaster_start,
            "restart_allowed": True,
        }
        _new(folder / "closure.json", closure)
        return closure


    def inspect_crashed_controller(self, *,
                                   pg_probe: Callable[[], Mapping],
                                   leases_probe: Callable[[], Mapping[str, str]]) -> dict:
        """Read-only recovery inventory; NEVER adopt a persisted PID or restart.

        A new controller cannot inherit the original Windows child handles from
        a dead controller. Even hash-consistent JSON closure files are not an
        external OS exit witness. Inspect what can safely be reported, retain
        every generation, and require an independent persistent handle owner
        before any future automatic crash-recovery protocol is authorized.
        """
        result = {
            "schema": "ops-native-crash-inventory/1",
            "status": "BLOCKED",
            "restart_allowed": False,
            "pid_adoption_allowed": False,
            "independent_handle_witness": False,
            "generations": [],
            "incomplete_generations": [],
            "pg_incarnation_matches": None,
            "active_or_unknown_leases": None,
            "ledger_consistent": False,
        }
        try:
            numbers = self._names()
            for generation in numbers:
                folder = self.generations / f"{generation:04d}"
                names = {entry.name for entry in folder.iterdir()}
                if names not in ({"reservation.json"},
                                 {"reservation.json", "closure.json"}):
                    raise NativeRestartBlocked("Unexpected recovery files")
                reservation = _read_exact(folder / "reservation.json")
                if (reservation.get("schema") != "ops-native-reservation/1"
                        or reservation.get("generation") != generation
                        or reservation.get("pin") != vars(self.pin)):
                    raise NativeRestartBlocked("Wrong crash reservation identity")
                if "closure.json" in names:
                    closure = _read_exact(folder / "closure.json")
                    if (closure.get("schema") != "ops-native-closure-link/1"
                            or closure.get("generation") != generation
                            or closure.get("reservation_sha256") != _digest(reservation)
                            or closure.get("pg_system_identifier") != self.pin.pg_system_identifier
                            or closure.get("pg_postmaster_start") != self.pin.pg_postmaster_start
                            or closure.get("restart_allowed") is not True
                            or not isinstance(closure.get("native_exit_sha256"), str)
                            or len(closure["native_exit_sha256"]) != 64):
                        raise NativeRestartBlocked("Invalid retained crash closure")
                else:
                    result["incomplete_generations"].append(generation)
                result["generations"].append(generation)
            result["ledger_consistent"] = True
        except (OSError, ValueError, NativeRestartBlocked):
            result["status"] = "CORRUPT_OR_UNKNOWN_LEDGER"
            return result

        try:
            pg = dict(pg_probe())
            result["pg_incarnation_matches"] = (
                pg.get("pg_system_identifier") == self.pin.pg_system_identifier
                and pg.get("pg_postmaster_start") == self.pin.pg_postmaster_start
            )
            states = dict(leases_probe())
            result["active_or_unknown_leases"] = any(
                state not in {"queued", "completed", "failed", "paused"}
                for state in states.values()
            ) or pg.get("active_running", 0) != 0
        except Exception:
            # Missing database evidence is UNKNOWN, never a false zero.
            result["status"] = "PG_OR_LEASE_EVIDENCE_UNAVAILABLE"
        return result
