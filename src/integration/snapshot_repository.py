"""Operator-owned immutable PostgreSQL registry for qualified F inputs.

No startup DDL, arbitrary JSON publication, fixture relabeling, credentials or
fallback database. The real-market reader is separate and remains fail-closed.
"""

import json
import re

from psycopg.types.json import Jsonb

from src.api.research_ledger import PersistentResearchLedger

from .qualified_snapshot import (
    MAX_BYTES,
    QualifiedSnapshot,
    certified_fixture_snapshot,
    certified_terminal_fixture_snapshot,
    verify_qualified_snapshot,
)


class QualifiedSnapshotRepository(PersistentResearchLedger):
    """Same explicit dedicated loopback/non-5432 DSN policy as research ledger."""

    @staticmethod
    def _identity(snapshot_id):
        if not isinstance(snapshot_id, str) or not re.fullmatch(
            r"synthetic:[a-f0-9]{64}", snapshot_id
        ):
            raise ValueError("invalid_qualified_snapshot_id")

    def migrate(self):
        """Explicit operator call only; construction/read never create schema."""
        with self.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(725281490)")
            conn.execute("CREATE SCHEMA IF NOT EXISTS tester_snapshots")
            conn.execute("""CREATE TABLE IF NOT EXISTS tester_snapshots.qualified (
                snapshot_id text PRIMARY KEY CHECK (snapshot_id ~ '^synthetic:[a-f0-9]{64}$'),
                document jsonb NOT NULL CHECK (jsonb_typeof(document)='object'
                    AND octet_length(document::text)<=16777216),
                created_at timestamptz NOT NULL DEFAULT clock_timestamp()
            )""")

    @staticmethod
    def _read(conn, snapshot_id) -> QualifiedSnapshot:
        # Bound serialized transfer even if a later operator changes the table
        # constraint; a NULL sentinel never transfers an oversized document.
        row = conn.execute(
            "SELECT CASE WHEN octet_length(document::text)<=%s THEN document::text "
            "ELSE NULL END AS encoded FROM tester_snapshots.qualified WHERE snapshot_id=%s LIMIT 1",
            (MAX_BYTES, snapshot_id),
        ).fetchone()
        if not row or row["encoded"] is None:
            raise ValueError("qualified_snapshot_missing_or_oversized")
        encoded = row["encoded"]
        if not isinstance(encoded, str) or len(encoded.encode()) > MAX_BYTES:
            raise ValueError("qualified_snapshot_bytes_limit")
        value = verify_qualified_snapshot(json.loads(encoded))
        if value.snapshot_id != snapshot_id:
            raise ValueError("qualified_snapshot_identity_mismatch")
        return value

    def read(self, snapshot_id) -> QualifiedSnapshot:
        self._identity(snapshot_id)
        with self.connection() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            return self._read(conn, snapshot_id)

    def publish_fixture(self) -> QualifiedSnapshot:
        """Only exact producer-pinned SYNTHETIC input; idempotent, never UPDATE.

        Existing conflicting content is rejected inside the same transaction;
        no caller-supplied completeness, timestamps or certification are accepted.
        """
        return self._publish_verified(certified_fixture_snapshot())

    def publish_terminal_fixture(self) -> QualifiedSnapshot:
        """Explicit fixed LINEAR variant; original spot publication stays unchanged."""
        return self._publish_verified(certified_terminal_fixture_snapshot())

    def _publish_verified(self, value) -> QualifiedSnapshot:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO tester_snapshots.qualified(snapshot_id,document) VALUES (%s,%s) "
                "ON CONFLICT (snapshot_id) DO NOTHING",
                (value.snapshot_id, Jsonb(value.as_dict())),
            )
            return self._read(conn, value.snapshot_id)
