"""Canonical PostgreSQL storage/auth for explicit isolated synthetic research only.

No default DSN, migration at app startup, SQLite, native database fallback or engine
execution. Operator-only publication replays the J verifier before persistence.
"""

import hashlib
import json
import secrets
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .research_control import (
    Command,
    Control,
    Denied,
    Identity,
    canonical_digest,
    create_control_app,
)


def validate_research_dsn(dsn: str):
    values = conninfo_to_dict(dsn)
    if (
        values.get("host") not in {"127.0.0.1", "::1"}
        or not values.get("port")
        or values["port"] == "5432"
        or not values.get("dbname", "").startswith("tester_research_")
        or values.get("service")
        or values.get("hostaddr")
        or values.get("options")
    ):
        raise ValueError("explicit isolated non-5432 loopback tester_research_* database required")
    return values


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def migrate_research(dsn: str):
    """Operator-only explicit migration; never called from app/controller construction."""
    ledger = PersistentResearchLedger(dsn)
    with ledger.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(725281347)")
        conn.execute("CREATE SCHEMA IF NOT EXISTS tester_research")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tester_research.migrations "
            "(version text PRIMARY KEY,checksum text NOT NULL)"
        )
        for path in sorted(Path(__file__).with_name("research_migrations").glob("*.sql")):
            content = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(content.encode()).hexdigest()
            row = conn.execute(
                "SELECT checksum FROM tester_research.migrations WHERE version=%s", (path.name,)
            ).fetchone()
            if row:
                if row["checksum"] != checksum:
                    raise ValueError("research migration checksum mismatch")
                continue
            conn.execute(content)
            conn.execute(
                "INSERT INTO tester_research.migrations VALUES(%s,%s)", (path.name, checksum)
            )


class PersistentResearchLedger:
    def __init__(self, dsn: str):
        validate_research_dsn(dsn)
        self.dsn = dsn

    def connection(self):
        endpoint = validate_research_dsn(self.dsn)
        return psycopg.connect(
            self.dsn,
            hostaddr=endpoint["host"],  # Override libpq PGHOSTADDR; never follow ambient redirects.
            connect_timeout=3,
            row_factory=dict_row,
            options="-c statement_timeout=10000 -c lock_timeout=5000",
        )

    @staticmethod
    def _audit(conn, actor, event, job_id=None, digest=None):
        conn.execute(
            "INSERT INTO tester_research.audit(actor,event,job_id,artifact_digest) VALUES(%s,%s,%s,%s)",
            (actor, event, job_id, digest),
        )

    @staticmethod
    def _credential(conn, actor):
        row = conn.execute(
            "SELECT actor FROM tester_research.credentials WHERE actor=%s "
            "AND NOT revoked AND expires_at>clock_timestamp() FOR SHARE",
            (actor,),
        ).fetchone()
        if not row:
            raise Denied("credential expired or revoked")

    def _acl(self, conn, actor, command):
        self._credential(conn, actor)
        row = conn.execute(
            "SELECT actor FROM tester_research.grants WHERE actor=%s AND artifact_digest=%s "
            "AND action=%s FOR SHARE",
            (actor, command.expected_artifact_digest, command.action),
        ).fetchone()
        if not row:
            raise Denied("capability denied")
        self._resolve(conn, command)

    @staticmethod
    def _stored(conn, digest):
        row = conn.execute(
            "SELECT * FROM tester_research.artifacts WHERE artifact_digest=%s", (digest,)
        ).fetchone()
        if not row:
            raise Denied("artifact denied")
        chart = json.loads(row["chart_text"])
        if canonical_digest(chart) != digest:
            raise ValueError("stored artifact digest mismatch")
        return chart

    def _resolve(self, conn, command):
        return Control._artifact(self._stored(conn, command.expected_artifact_digest), command)

    def publish_bundle(self, bundle: dict) -> str:
        """Operator/source path only, not exposed over HTTP. J replay verifies actual F."""
        from src.integration.strategy_lab import verify_research_bundle

        verify_research_bundle(bundle)
        chart = bundle["chart"]
        if not chart["synthetic"] or chart["execution_mode"] != "SIMULATED":
            raise ValueError("isolated synthetic research only")
        digest = canonical_digest(chart)
        if digest != bundle["chart_digest"]:
            raise ValueError("chart digest mismatch")
        chart_text, bundle_text = _canonical(chart), _canonical(bundle)
        if len(chart_text.encode()) > 1048576 or len(bundle_text.encode()) > 8388608:
            raise ValueError("research artifact size limit")
        with self.connection() as conn:
            row = conn.execute(
                "INSERT INTO tester_research.artifacts "
                "(artifact_digest,chart_text,bundle_digest,bundle_text) VALUES(%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING artifact_digest",
                (digest, chart_text, canonical_digest(bundle), bundle_text),
            ).fetchone()
            if row:
                self._audit(conn, "operator", "published", digest=digest)
            else:
                prior = conn.execute(
                    "SELECT bundle_digest FROM tester_research.artifacts WHERE artifact_digest=%s",
                    (digest,),
                ).fetchone()
                if prior["bundle_digest"] != canonical_digest(bundle):
                    raise ValueError("immutable bundle conflict")
        return digest

    def issue_credential(self, actor: str, artifact_digests: list[str], *, ttl_seconds=600):
        """Explicit operator-only issuance. Token returned once; only hash stored."""
        import re

        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,99}", actor)
            or not 1 <= ttl_seconds <= 3600
        ):
            raise ValueError("bounded actor/lifetime required")
        if not artifact_digests or len(artifact_digests) > 20:
            raise ValueError("exact artifact capabilities required")
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO tester_research.credentials(actor,token_hash,expires_at) "
                "VALUES(%s,%s,clock_timestamp()+(%s*interval '1 second'))",
                (actor, token_hash, ttl_seconds),
            )
            for digest in set(artifact_digests):
                self._stored(conn, digest)
                conn.execute(
                    "INSERT INTO tester_research.grants VALUES(%s,%s,%s)",
                    (actor, digest, "chart.overlay"),
                )
            self._audit(conn, actor, "credential_issued")
        return token

    def authenticate(self, authorization: str | None):
        if not authorization or not authorization.startswith("Bearer "):
            raise Denied("credential required")
        token = authorization[7:]
        if not 32 <= len(token) <= 512 or any(c.isspace() for c in token):
            raise Denied("invalid credential")
        with self.connection() as conn:
            row = conn.execute(
                "SELECT actor FROM tester_research.credentials WHERE token_hash=%s "
                "AND NOT revoked AND expires_at>clock_timestamp()",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
            if not row:
                raise Denied("credential expired or revoked")
            return Identity(actor=row["actor"])

    def authorize(self, actor, command):
        with self.connection() as conn:
            self._acl(conn, actor, command)

    def revoke(self, actor):
        with self.connection() as conn:
            conn.execute(
                "UPDATE tester_research.credentials SET revoked=true WHERE actor=%s", (actor,)
            )
            self._audit(conn, actor, "revoked")

    def submit(self, actor, command, digest):
        if digest != canonical_digest(command.model_dump(mode="json")):
            raise ValueError("command digest mismatch")
        with self.connection() as conn:
            self._acl(conn, actor, command)
            conn.execute("SELECT pg_advisory_xact_lock(725281348)")
            pending = conn.execute(
                "SELECT count(*) AS n FROM tester_research.jobs WHERE state IN ('pending','running')"
            ).fetchone()["n"]
            if (
                pending >= 256
                and not conn.execute(
                    "SELECT 1 FROM tester_research.jobs WHERE actor=%s AND request_id=%s",
                    (actor, command.request_id),
                ).fetchone()
            ):
                raise ValueError("research queue full")
            row = conn.execute(
                "INSERT INTO tester_research.jobs(job_id,actor,request_id,command,digest,state) "
                "VALUES(%s,%s,%s,%s,%s,'pending') ON CONFLICT(actor,request_id) DO NOTHING RETURNING *",
                (
                    uuid4(),
                    actor,
                    command.request_id,
                    Jsonb(command.model_dump(mode="json")),
                    digest,
                ),
            ).fetchone()
            if row:
                self._audit(
                    conn, actor, "submitted", row["job_id"], command.expected_artifact_digest
                )
            else:
                row = conn.execute(
                    "SELECT * FROM tester_research.jobs WHERE actor=%s AND request_id=%s",
                    (actor, command.request_id),
                ).fetchone()
                if row["digest"] != digest:
                    raise ValueError("idempotency conflict")
            return self._row(conn, row)

    def _locked_job(self, conn, actor, job_id):
        row = conn.execute(
            "SELECT * FROM tester_research.jobs WHERE actor=%s AND job_id=%s FOR UPDATE",
            (actor, job_id),
        ).fetchone()
        if not row:
            raise Denied("object denied")
        command = Command.model_validate(row["command"])
        if canonical_digest(row["command"]) != row["digest"]:
            raise ValueError("command digest mismatch")
        self._acl(conn, actor, command)
        return row, command

    def _row(self, conn, row):
        result = dict(row)
        result["job_id"] = str(result["job_id"])
        result["artifact"] = (
            self._stored(conn, row["artifact_digest"]) if row["artifact_digest"] else None
        )
        return result

    def read(self, actor, job_id):
        with self.connection() as conn:
            row, _ = self._locked_job(conn, actor, job_id)
            return self._row(conn, row)

    def pending_job_ids(self, actor: str, limit: int = 16):
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("bounded pending page required")
        with self.connection() as conn:
            self._credential(conn, actor)
            rows = conn.execute(
                "SELECT job_id FROM tester_research.jobs WHERE actor=%s "
                "AND (state='pending' OR (state='running' AND lease_until<clock_timestamp())) "
                "ORDER BY created_at,job_id LIMIT %s",
                (actor, limit),
            ).fetchall()
            return [str(row["job_id"]) for row in rows]

    def claim(self, actor, job_id):
        with self.connection() as conn:
            row, _ = self._locked_job(conn, actor, job_id)
            claimed = conn.execute(
                "UPDATE tester_research.jobs SET state='running',lease_token=%s,"
                "lease_until=clock_timestamp()+interval '30 seconds',attempts=attempts+1 WHERE job_id=%s "
                "AND (state='pending' OR (state='running' AND lease_until<clock_timestamp())) RETURNING *",
                (uuid4(), job_id),
            ).fetchone()
            if not claimed:
                return None
            self._audit(conn, actor, "claimed", job_id)
            return self._row(conn, claimed)

    def complete_leased(self, actor, job_id, token):
        with self.connection() as conn:
            row, command = self._locked_job(conn, actor, job_id)
            # Database clock is authoritative; old lease cannot publish after recovery.
            changed = conn.execute(
                "UPDATE tester_research.jobs SET state='complete',artifact_digest=%s,"
                "lease_token=NULL,lease_until=NULL WHERE job_id=%s AND actor=%s "
                "AND state='running' AND lease_token=%s AND lease_until>clock_timestamp() RETURNING *",
                (command.expected_artifact_digest, job_id, actor, token),
            ).fetchone()
            if not changed:
                raise ValueError("lease lost")
            self._audit(conn, actor, "completed", job_id, command.expected_artifact_digest)
            return self._row(conn, changed)

    def complete(self, *args):
        raise Denied("fenced completion required")

    def acknowledge(self, actor, job_id, digest, rendered_fill_ids):
        with self.connection() as conn:
            row, command = self._locked_job(conn, actor, job_id)
            if row["state"] != "complete" or row["artifact_digest"] != digest:
                raise ValueError("completed artifact digest required")
            chart = self._resolve(conn, command)
            expected = [m["fill_id"] for m in chart["markers"]]
            if len(rendered_fill_ids) != len(set(rendered_fill_ids)) or sorted(
                rendered_fill_ids
            ) != sorted(expected):
                raise ValueError("complete rendered fill set required")
            inserted = conn.execute(
                "INSERT INTO tester_research.acknowledgments "
                "(job_id,actor,artifact_digest,rendered_fill_ids) VALUES(%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING job_id",
                (job_id, actor, digest, Jsonb(sorted(rendered_fill_ids))),
            ).fetchone()
            if inserted:
                self._audit(conn, actor, "render_ack", job_id, digest)
            return self._row(conn, row)

    def audit(self, actor):
        with self.connection() as conn:
            self._credential(conn, actor)
            return conn.execute(
                "SELECT * FROM tester_research.audit WHERE actor=%s ORDER BY sequence", (actor,)
            ).fetchall()


class PersistentControl(Control):
    def dispatch(self, identity, job_id):
        lease = self.store.claim(identity.actor, job_id)
        if lease is not None:
            self.store.complete_leased(identity.actor, job_id, lease["lease_token"])
        return self.result(identity, job_id)

    def ack(self, identity, job_id, digest, rendered_fill_ids):
        result = self.result(identity, job_id)
        if result.get("artifact_digest") != digest:
            raise ValueError("artifact digest mismatch")
        self.store.acknowledge(identity.actor, job_id, digest, rendered_fill_ids)
        return {"job_id": job_id, "artifact_digest": digest, "acknowledged": True}


def create_persistent_control(dsn):
    store = PersistentResearchLedger(dsn)
    return PersistentControl(store=store, authorize=store.authorize, provider=None)


def create_persistent_control_app(dsn):
    control = create_persistent_control(dsn)
    app = create_control_app(control, control.store.authenticate)
    app.state.research_control = control
    return app
