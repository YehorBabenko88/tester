"""Durable control-plane kernel. Public methods require a trusted Identity.

Worker methods are private-service interfaces, never agent/HTTP tools. Side effects
belong to bounded trusted providers; interrupted work is never automatically replayed.
"""
from contextlib import contextmanager
import json
import secrets
import sqlite3
import time
from uuid import uuid4

from .commands import ACTIONS, APPROVAL_REQUIRED
from .contracts import (VERSION, bounded_json, canonical, digest, identifier, integer,
                        validate_command, validate_overlay, validate_result)


class Conflict(RuntimeError): pass
class QuotaExceeded(RuntimeError): pass


class ControlPlane:
    def __init__(self, path, *, clock=time.time, max_active=8, per_actor_active=4,
                 plans_per_minute=60, simulation_gate=None, code_revision=None):
        if not integer(max_active, 1, 128) or not integer(per_actor_active, 1, 128) or not integer(plans_per_minute, 1, 1000): raise ValueError("invalid policy limits")
        self.path, self.clock = str(path), clock
        self.max_active, self.per_actor_active = max_active, per_actor_active
        self.plans_per_minute, self.simulation_gate = plans_per_minute, simulation_gate
        if code_revision is not None:
            from .contracts import REVISION
            if not isinstance(code_revision, str) or not REVISION.fullmatch(code_revision): raise ValueError("invalid build revision")
        self.code_revision = code_revision
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata(version INTEGER NOT NULL);
                INSERT INTO metadata SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM metadata);
                CREATE TABLE IF NOT EXISTS plans(
                    id TEXT PRIMARY KEY, actor TEXT NOT NULL, scope TEXT NOT NULL,
                    request_id TEXT NOT NULL, hash TEXT NOT NULL, command TEXT NOT NULL,
                    preview TEXT NOT NULL, evidence TEXT, approval_required INTEGER NOT NULL,
                    approved_by TEXT, approved_at REAL, created REAL NOT NULL, expires REAL NOT NULL,
                    UNIQUE(actor, request_id));
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, plan_id TEXT NOT NULL UNIQUE,
                    actor TEXT NOT NULL, scope TEXT NOT NULL, state TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0, result TEXT,
                    worker TEXT, lease_token TEXT, lease_until REAL, created REAL NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES plans(id));
                CREATE TABLE IF NOT EXISTS events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    kind TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, scope TEXT NOT NULL,
                    kind TEXT NOT NULL, ref TEXT NOT NULL, details TEXT NOT NULL,
                    created REAL NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS config(
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, value TEXT NOT NULL);
                INSERT OR IGNORE INTO config VALUES(1, 0, '{"max_workers":1,"max_downloads":1,"memory_mb":512}');
                CREATE INDEX IF NOT EXISTS active_jobs ON jobs(state,actor);
                CREATE INDEX IF NOT EXISTS event_job ON events(job_id,seq);
            """)
            if db.execute("SELECT version FROM metadata").fetchall()[0][0] != 1: raise Conflict("unsupported storage version")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            if db.in_transaction: db.execute("COMMIT")
        except BaseException:
            if db.in_transaction: db.execute("ROLLBACK")
            raise
        finally: db.close()

    def _audit(self, db, actor, scope, kind, ref, **details):
        previous = db.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        previous = previous[0] if previous else "0"*64
        created = self.clock()
        record = dict(actor=actor, scope=scope, kind=kind, ref=ref, details=details,
                      created=created, previous_hash=previous)
        db.execute("INSERT INTO audit(actor,scope,kind,ref,details,created,previous_hash,hash) VALUES(?,?,?,?,?,?,?,?)",
                   (actor, scope, kind, ref, canonical(details), created, previous, digest(record)))

    def _event(self, db, job_id, kind, **data):
        db.execute("INSERT INTO events(job_id,kind,data,created) VALUES(?,?,?,?)", (job_id, kind, canonical(data), self.clock()))

    def capabilities(self, identity):
        identity.check(self.clock())
        return {"schema_version":VERSION, "actions":sorted(identity.actions & ACTIONS),
                "scopes":sorted(identity.scopes), "limits":{"active_jobs":self.max_active,
                "actor_active_jobs":self.per_actor_active, "plans_per_minute":self.plans_per_minute,
                "page_size":500, "request_lifetime_seconds":900},
                "approval_actions":sorted((APPROVAL_REQUIRED | {"backtest.run"}) & identity.actions)}

    def _config(self, db):
        row = db.execute("SELECT revision,value FROM config WHERE id=1").fetchone()
        return {"revision":row["revision"], **json.loads(row["value"])}

    def config(self):
        """Trusted E provider interface; public reads go through a scoped plan."""
        with self._db() as db: return self._config(db)

    def _evidence(self, command):
        if command["action"] != "backtest.run": return None
        if self.simulation_gate is None: raise Conflict("strategy/data-quality provider unavailable")
        evidence = self.simulation_gate(json.loads(canonical(command)))
        bounded_json(evidence, 32768)
        expected = {"validation_id", "strategy_id", "strategy_revision", "snapshot_id", "coverage", "preview", "source_ids"}
        if not isinstance(evidence, dict) or set(evidence) != expected or evidence["coverage"] != "complete" or evidence["snapshot_id"] != command["snapshot_id"] or not evidence["preview"]:
            raise Conflict("strategy preview and complete pinned coverage required")
        if any(evidence[k] != command["params"][k] for k in ("validation_id", "strategy_id", "strategy_revision")) or not isinstance(evidence["source_ids"], list) or not evidence["source_ids"] or not all(identifier(s) for s in evidence["source_ids"]):
            raise Conflict("strategy evidence mismatch")
        return evidence

    def _plan_view(self, row):
        return {"plan_id":row["id"], "intent_hash":row["hash"],
                "command":json.loads(row["command"]), "preview":json.loads(row["preview"]),
                "approval_required":bool(row["approval_required"]),
                "approved_by":row["approved_by"], "expires_at":row["expires"]}

    def plan(self, identity, payload):
        command = validate_command(payload, self.clock())
        identity.check(self.clock(), actor=command["actor"], scope=command["scope"], action=command["action"])
        hash_ = digest(command)
        with self._db() as db:
            old = db.execute("SELECT * FROM plans WHERE actor=? AND request_id=?", (identity.actor, command["request_id"])).fetchone()
            if old:
                if old["hash"] != hash_: raise Conflict("request_id reused with different intent")
                return self._plan_view(old)
            count = db.execute("SELECT COUNT(*) FROM plans WHERE actor=? AND created>?", (identity.actor, self.clock()-60)).fetchone()[0]
            if count >= self.plans_per_minute: raise QuotaExceeded("plan rate limit")
        evidence = self._evidence(command)
        with self._db() as db:
            old = db.execute("SELECT * FROM plans WHERE actor=? AND request_id=?", (identity.actor, command["request_id"])).fetchone()
            if old:
                if old["hash"] != hash_: raise Conflict("request_id reused with different intent")
                return self._plan_view(old)
            count = db.execute("SELECT COUNT(*) FROM plans WHERE actor=? AND created>?", (identity.actor, self.clock()-60)).fetchone()[0]
            if count >= self.plans_per_minute: raise QuotaExceeded("plan rate limit")
            preview = {"action":command["action"], "snapshot_id":command["snapshot_id"],
                       "range_ms":[command["start_ms"], command["end_ms"]],
                       "assumptions":["historical data only", "trusted bounded provider required"]}
            if evidence is not None: preview["strategy"] = evidence
            if command["action"] in {"system.config.preview", "system.config.apply"}:
                current = self._config(db)
                before = {k:v for k,v in current.items() if k != "revision"}
                preview.update(before=before, after={**before, **command["params"].get("changes", {})}, revision=current["revision"])
            if command["action"] == "dev.change.request":
                preview.update(mode="isolated-worktree-draft-pr", auto_merge=False, live_patch=False)
            id_ = str(uuid4())
            db.execute("INSERT INTO plans(id,actor,scope,request_id,hash,command,preview,evidence,approval_required,created,expires) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (id_, identity.actor, command["scope"], command["request_id"], hash_, canonical(command), canonical(preview), canonical(evidence) if evidence is not None else None,
                        command["action"] in APPROVAL_REQUIRED or command["action"] == "backtest.run", self.clock(), min(command["expires_at"], identity.expires_at)))
            self._audit(db, identity.actor, command["scope"], "planned", id_, intent_hash=hash_, correlation_id=command["correlation_id"])
            return self._plan_view(db.execute("SELECT * FROM plans WHERE id=?", (id_,)).fetchone())

    def _owned_plan(self, db, identity, plan_id):
        row = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None: raise KeyError("plan not found")
        command = json.loads(row["command"])
        identity.check(self.clock(), actor=row["actor"], scope=row["scope"], action=command["action"])
        return row, command

    def approve(self, human, plan_id):
        """Only E's authenticated local human route may call this; never expose via MCP."""
        human.check(self.clock())
        if not human.human: raise PermissionError("local authenticated human required")
        with self._db() as db:
            row = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
            if row is None: raise KeyError("plan not found")
            command = json.loads(row["command"])
            human.check(self.clock(), scope=row["scope"], action=command["action"])
            if row["expires"] <= self.clock(): raise Conflict("plan expired")
            if db.execute("SELECT id FROM jobs WHERE plan_id=?", (plan_id,)).fetchone(): raise Conflict("plan already consumed")
            db.execute("UPDATE plans SET approved_by=?,approved_at=? WHERE id=?", (human.actor, self.clock(), plan_id))
            self._audit(db, row["actor"], row["scope"], "approved", plan_id, human=human.actor, intent_hash=row["hash"])
            return self._plan_view(db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone())

    def submit(self, identity, plan_id):
        # Fetch the pinned plan, then invoke trusted evidence outside the write lock.
        with self._db() as db:
            row, command = self._owned_plan(db, identity, plan_id)
            existing = db.execute("SELECT id FROM jobs WHERE plan_id=?", (plan_id,)).fetchone()
            if existing: return self._get(db, identity, existing[0])
        evidence = self._evidence(command)
        with self._db() as db:
            row, command = self._owned_plan(db, identity, plan_id)
            existing = db.execute("SELECT id FROM jobs WHERE plan_id=?", (plan_id,)).fetchone()
            if existing: return self._get(db, identity, existing[0])
            if row["expires"] <= self.clock(): raise Conflict("plan expired")
            if row["evidence"] != (canonical(evidence) if evidence is not None else None): raise Conflict("strategy/data evidence changed; new plan required")
            if row["approval_required"] and (row["approved_by"] is None or row["approved_at"] < self.clock()-300): raise PermissionError("fresh human approval required")
            action = command["action"]
            internal = action in {"system.config.preview", "system.config.apply"}
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
            actor_active = db.execute("SELECT COUNT(*) FROM jobs WHERE actor=? AND state IN ('queued','running')", (identity.actor,)).fetchone()[0]
            if not internal and (active >= self.max_active or actor_active >= self.per_actor_active): raise QuotaExceeded("active job quota")
            job_id = str(uuid4())
            result = None
            if internal:
                preview = json.loads(row["preview"])
                if action == "system.config.apply":
                    current = self._config(db)
                    if current["revision"] != command["params"]["expected_revision"] or current["revision"] != preview["revision"]: raise Conflict("configuration revision changed")
                    db.execute("UPDATE config SET revision=revision+1,value=? WHERE id=1", (canonical(preview["after"]),))
                config = self._config(db)
                result = canonical({"schema_version":"1.0", "data":{"config":config, "preview":preview},
                                    "provenance":{"snapshot_id":command["snapshot_id"],
                                    "source_ids":["control:config:"+str(config["revision"])],
                                    "coverage":"complete", "code_revision":self.code_revision}, "artifacts":[]})
            db.execute("INSERT INTO jobs(id,plan_id,actor,scope,state,result,progress,created) VALUES(?,?,?,?,?,?,?,?)", (job_id, plan_id, identity.actor, row["scope"], "succeeded" if internal else "queued", result, 100 if internal else 0, self.clock()))
            self._event(db, job_id, "succeeded" if internal else "queued")
            self._audit(db, identity.actor, row["scope"], "submitted", job_id, intent_hash=row["hash"], plan_id=plan_id, approved_by=row["approved_by"])
            if internal:
                self._audit(db, identity.actor, row["scope"], "succeeded", job_id,
                            result_hash=digest(json.loads(result)), provenance=json.loads(result)["provenance"])
            return self._get(db, identity, job_id)

    def _get(self, db, identity, job_id):
        row = db.execute("SELECT j.*,p.command,p.hash,p.approved_by FROM jobs j JOIN plans p ON j.plan_id=p.id WHERE j.id=?", (job_id,)).fetchone()
        if row is None: raise KeyError("job not found")
        command = json.loads(row["command"])
        identity.check(self.clock(), actor=row["actor"], scope=row["scope"], action=command["action"])
        return {"job_id":row["id"], "plan_id":row["plan_id"], "state":row["state"], "progress":row["progress"],
                "command":command, "intent_hash":row["hash"], "approved_by":row["approved_by"],
                "result":json.loads(row["result"]) if row["result"] is not None else None}

    def get(self, identity, job_id):
        with self._db() as db: return self._get(db, identity, job_id)

    def cancel(self, identity, job_id):
        with self._db() as db:
            job = self._get(db, identity, job_id)
            if job["state"] in {"queued", "running"}:
                db.execute("UPDATE jobs SET state='cancelled',lease_token=NULL,lease_until=NULL WHERE id=?", (job_id,))
                self._event(db, job_id, "cancelled")
                self._audit(db, identity.actor, job["command"]["scope"], "cancelled", job_id)
            return self._get(db, identity, job_id)

    def events(self, identity, job_id, *, after=0, limit=100):
        if not integer(after, 0, 2**63-1) or not integer(limit, 1, 500): raise ValueError("invalid pagination")
        with self._db() as db:
            self._get(db, identity, job_id)
            return [dict(seq=r["seq"], kind=r["kind"], data=json.loads(r["data"]), created=r["created"]) for r in db.execute("SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?", (job_id, after, limit))]

    def artifacts(self, identity, job_id):
        job = self.get(identity, job_id)
        return (job["result"] or {}).get("artifacts", [])

    def audit(self, identity, *, after=0, limit=100):
        identity.check(self.clock())
        if not integer(after, 0, 2**63-1) or not integer(limit, 1, 500): raise ValueError("invalid pagination")
        with self._db() as db:
            if not identity.scopes: return []
            placeholders = ",".join("?" for _ in identity.scopes)
            rows = db.execute("SELECT * FROM audit WHERE actor=? AND seq>? AND scope IN ("+placeholders+") ORDER BY seq LIMIT ?", (identity.actor, after, *sorted(identity.scopes), limit))
            return [{**dict(r), "details":json.loads(r["details"])} for r in rows]

    def claim(self, worker, *, lease_seconds=60):
        if not identifier(worker) or not integer(lease_seconds, 1, 300): raise ValueError("invalid worker lease")
        self.recover()
        with self._db() as db:
            row = db.execute("SELECT j.*,p.command FROM jobs j JOIN plans p ON j.plan_id=p.id WHERE j.state='queued' ORDER BY j.created,j.rowid LIMIT 1").fetchone()
            if row is None: return None
            token = secrets.token_hex(32)
            db.execute("UPDATE jobs SET state='running',worker=?,lease_token=?,lease_until=? WHERE id=?", (worker, token, self.clock()+lease_seconds, row["id"]))
            self._event(db, row["id"], "running", worker=worker)
            self._audit(db, row["actor"], row["scope"], "claimed", row["id"], worker=worker)
            return {"job_id":row["id"], "command":json.loads(row["command"]), "lease_token":token}

    def _lease(self, db, job_id, token):
        row = db.execute("SELECT j.*,p.command FROM jobs j JOIN plans p ON j.plan_id=p.id WHERE j.id=?", (job_id,)).fetchone()
        if row is None or row["state"] != "running" or not isinstance(token, str) or not secrets.compare_digest(row["lease_token"] or "", token) or row["lease_until"] <= self.clock(): raise Conflict("inactive or expired worker lease")
        return row

    def progress(self, job_id, token, percent, *, lease_seconds=60):
        if not integer(percent, 0, 99) or not integer(lease_seconds, 1, 300): raise ValueError("invalid progress")
        with self._db() as db:
            row = self._lease(db, job_id, token)
            if percent < row["progress"]: raise Conflict("progress cannot decrease")
            db.execute("UPDATE jobs SET progress=?,lease_until=? WHERE id=?", (percent, self.clock()+lease_seconds, job_id))
            # Heartbeats renew the lease but do not create an unbounded log.
            if percent != row["progress"]: self._event(db, job_id, "progress", percent=percent)

    def complete(self, job_id, token, result):
        with self._db() as db:
            row = self._lease(db, job_id, token)
            command = json.loads(row["command"])
            result = validate_result(result, command)
            if command["action"] == "chart.overlay":
                overlay = validate_overlay(result["data"])
                for key in ("category", "symbol", "start_ms", "end_ms"):
                    if overlay[key] != command[key]: raise ValueError("overlay command mismatch")
                for key in ("run_id", "revision", "interval"):
                    if overlay[key] != command["params"][key]: raise ValueError("overlay identity mismatch")
                if overlay["provenance"] != result["provenance"]: raise ValueError("overlay provenance mismatch")
            db.execute("UPDATE jobs SET state='succeeded',progress=100,result=?,lease_token=NULL,lease_until=NULL WHERE id=?", (canonical(result), job_id))
            self._event(db, job_id, "succeeded")
            self._audit(db, row["actor"], row["scope"], "succeeded", job_id, result_hash=digest(result), provenance=result["provenance"])

    def fail(self, job_id, token, code):
        # Provider exceptions/logs may contain credentials: accept only stable codes.
        if code not in {"provider_unavailable", "invalid_evidence", "provider_failed", "resource_limit"}: raise ValueError("invalid failure code")
        with self._db() as db:
            row = self._lease(db, job_id, token)
            db.execute("UPDATE jobs SET state='failed',lease_token=NULL,lease_until=NULL WHERE id=?", (job_id,))
            self._event(db, job_id, "failed", code=code)
            self._audit(db, row["actor"], row["scope"], "failed", job_id, code=code)

    def recover(self):
        with self._db() as db:
            rows = db.execute("SELECT j.id,j.actor,j.scope FROM jobs j JOIN plans p ON j.plan_id=p.id WHERE (j.state='running' AND j.lease_until<=?) OR (j.state='queued' AND p.expires<=?)", (self.clock(), self.clock())).fetchall()
            for row in rows:
                db.execute("UPDATE jobs SET state='interrupted',lease_token=NULL,lease_until=NULL WHERE id=?", (row["id"],))
                self._event(db, row["id"], "interrupted")
                self._audit(db, row["actor"], row["scope"], "interrupted", row["id"])
            return len(rows)
