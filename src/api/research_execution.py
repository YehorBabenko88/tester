"""Opt-in operator-granted synthetic F execution; no default startup or migrations."""

import hashlib
import re
import secrets
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.types.json import Jsonb
from pydantic import Field, model_validator

from .research_control import Binding, Command, Denied, Strict, canonical_digest
from .research_ledger import PersistentResearchLedger


class ExecutionRequest(Strict):
    schema_version: Literal["tester.research-execution/1"] = "tester.research-execution/1"
    request_id: Annotated[str, Field(min_length=36, max_length=36)]
    input_digest: Annotated[str, Field(pattern="^[a-f0-9]{64}$")]

    @model_validator(mode="after")
    def canonical_id(self):
        if str(UUID(self.request_id)) != self.request_id:
            raise ValueError("canonical UUID required")
        return self


def prepare_input(strategy, qualified):
    """Operator boundary only; snapshot must originate from the server repository."""
    from src.backtest.quality import validate_snapshot
    from src.integration.strategy_lab import MAX_BARS, _bounded_copy, _synthetic_metadata
    from src.strategy.dsl import validate_strategy

    value = _bounded_copy(qualified)
    snapshot = value.get("snapshot")
    if (
        value.get("mode") != "SYNTHETIC"
        or value.get("admitted") is not True
        or not isinstance(snapshot, dict)
        or not 1 <= len(snapshot.get("candles", [])) <= MAX_BARS
        or value.get("snapshot_digest") != canonical_digest(snapshot)
        or value.get("snapshot_id") != "synthetic:" + canonical_digest(snapshot)
    ):
        raise ValueError("only admitted immutable synthetic snapshots supported")
    for name in ("dataset_revision", "evidence_digest"):
        if not isinstance(value.get(name), str) or not re.fullmatch("[a-f0-9]{64}", value[name]):
            raise ValueError("immutable snapshot evidence required")
    _synthetic_metadata(snapshot)
    strategy = validate_strategy(_bounded_copy(strategy))
    validate_snapshot(snapshot, strategy)
    return _bounded_copy(
        dict(schema_version="tester.execution-input/1", strategy=strategy, qualified=value)
    )


def execute_input(value):
    from src.integration.strategy_lab import run_synthetic_research

    validated = prepare_input(value["strategy"], value["qualified"])
    return run_synthetic_research(validated["strategy"], validated["qualified"]["snapshot"])


def chart_command(bundle, job_id):
    chart = bundle["chart"]
    return Command(
        **{k: chart[k] for k in Binding.model_fields},
        action="chart.overlay",
        request_id=str(uuid5(NAMESPACE_URL, "tester-execution-chart:" + job_id)),
        expected_artifact_digest=canonical_digest(chart),
    )


class ExecutionLedger(PersistentResearchLedger):
    """Separate execution grants; artifact read credentials alone cannot execute."""

    def register(self, strategy, snapshot_repository, snapshot_id):
        qualified = snapshot_repository.read(snapshot_id)
        value = prepare_input(strategy, qualified.as_dict())
        if value["qualified"]["snapshot_id"] != snapshot_id:
            raise ValueError("server snapshot identity mismatch")
        digest = canonical_digest(value)
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO tester_research.execution_inputs VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (digest, Jsonb(value)),
            )
            self._audit(conn, "operator", "execution_input_registered", digest=digest)
        return digest

    def issue_execution_credential(self, actor, input_digest, *, ttl_seconds=600):
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,99}", actor)
            or type(ttl_seconds) is not int
            or not 1 <= ttl_seconds <= 3600
        ):
            raise ValueError("bounded actor/lifetime required")
        token = secrets.token_urlsafe(32)
        with self.connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM tester_research.execution_inputs WHERE input_digest=%s",
                (input_digest,),
            ).fetchone():
                raise ValueError("registered input required")
            conn.execute(
                "INSERT INTO tester_research.credentials(actor,token_hash,expires_at) VALUES(%s,%s,clock_timestamp()+%s*interval '1 second')",
                (actor, hashlib.sha256(token.encode()).hexdigest(), ttl_seconds),
            )
            conn.execute(
                "INSERT INTO tester_research.execution_grants VALUES(%s,%s)", (actor, input_digest)
            )
            self._audit(conn, actor, "execution_credential_issued", digest=input_digest)
        return token

    def _allowed(self, conn, actor, digest):
        self._credential(conn, actor)
        if not conn.execute(
            "SELECT 1 FROM tester_research.execution_grants WHERE actor=%s AND input_digest=%s FOR SHARE",
            (actor, digest),
        ).fetchone():
            raise Denied("execution grant denied")

    def _job(self, conn, actor, job_id):
        row = conn.execute(
            "SELECT * FROM tester_research.execution_jobs WHERE actor=%s AND job_id=%s FOR UPDATE",
            (actor, job_id),
        ).fetchone()
        if not row:
            raise Denied("execution job denied")
        self._allowed(conn, actor, row["input_digest"])
        return row

    def submit_execution(self, actor, request):
        with self.connection() as conn:
            # One principal serializes admission; concurrent requests cannot exceed the quota.
            conn.execute(
                "SELECT actor FROM tester_research.credentials WHERE actor=%s FOR UPDATE", (actor,)
            )
            self._allowed(conn, actor, request.input_digest)
            old = conn.execute(
                "SELECT * FROM tester_research.execution_jobs WHERE actor=%s AND request_id=%s",
                (actor, request.request_id),
            ).fetchone()
            if old:
                if old["input_digest"] != request.input_digest:
                    raise ValueError("idempotency conflict")
                return self._public(old)
            count = conn.execute(
                "SELECT count(*) AS n FROM tester_research.execution_jobs WHERE actor=%s AND state IN ('pending','running')",
                (actor,),
            ).fetchone()["n"]
            if count >= 4:
                raise ExecutionBusy()
            row = conn.execute(
                "INSERT INTO tester_research.execution_jobs(job_id,actor,request_id,input_digest,state) VALUES(%s,%s,%s,%s,'pending') RETURNING *",
                (uuid4(), actor, request.request_id, request.input_digest),
            ).fetchone()
            self._audit(conn, actor, "execution_submitted", row["job_id"], request.input_digest)
            return self._public(row)

    @staticmethod
    def _public(row):
        result = dict(job_id=str(row["job_id"]), state=row["state"])
        if row["state"] == "complete":
            result.update(
                chart_command=row["chart_command"],
                certification=dict(scope="SYNTHETIC_ONLY", real_market_coverage="NOT_PROVEN"),
            )
        elif row["state"] == "failed":
            result["reason"] = row["reason"]
        return result

    def execution_result(self, actor, job_id):
        with self.connection() as conn:
            return self._public(self._job(conn, actor, job_id))

    def pending_executions(self, actor):
        with self.connection() as conn:
            self._credential(conn, actor)
            return [
                str(r["job_id"])
                for r in conn.execute(
                    "SELECT job_id FROM tester_research.execution_jobs WHERE actor=%s AND (state='pending' OR (state='running' AND lease_until<clock_timestamp())) ORDER BY created_at,job_id LIMIT 4",
                    (actor,),
                ).fetchall()
            ]

    def dispatch_execution(self, actor, job_id):
        token = uuid4()
        with self.connection() as conn:
            row = self._job(conn, actor, job_id)
            if row["state"] in ("complete", "failed"):
                return self._public(row)
            claimed = conn.execute(
                "UPDATE tester_research.execution_jobs SET state='running',lease_token=%s,lease_until=clock_timestamp()+interval '60 seconds',attempts=attempts+1 WHERE job_id=%s AND attempts<3 AND (state='pending' OR lease_until<clock_timestamp()) RETURNING *",
                (token, job_id),
            ).fetchone()
            if not claimed:
                if row["attempts"] >= 3:
                    failed = conn.execute(
                        "UPDATE tester_research.execution_jobs SET state='failed',reason='ATTEMPT_LIMIT',lease_token=NULL,lease_until=NULL WHERE job_id=%s AND lease_until<clock_timestamp() RETURNING *",
                        (job_id,),
                    ).fetchone()
                    if failed:
                        self._audit(conn, actor, "execution_failed", job_id)
                        return self._public(failed)
                return self._public(row)
            value = conn.execute(
                "SELECT value FROM tester_research.execution_inputs WHERE input_digest=%s",
                (row["input_digest"],),
            ).fetchone()["value"]
            self._audit(conn, actor, "execution_claimed", job_id, row["input_digest"])
        # F/J execution/publication is deterministic and idempotent. No caller code is evaluated.
        if canonical_digest(value) != row["input_digest"]:
            raise ValueError("stored execution input hash mismatch")
        bundle = execute_input(value)
        digest = self.publish_bundle(bundle)
        command = chart_command(bundle, str(job_id)).model_dump(mode="json")
        with self.connection() as conn:
            row = self._job(conn, actor, job_id)
            changed = conn.execute(
                "UPDATE tester_research.execution_jobs SET state='complete',chart_command=%s,lease_token=NULL,lease_until=NULL WHERE job_id=%s AND state='running' AND lease_token=%s AND lease_until>clock_timestamp() RETURNING *",
                (Jsonb(command), job_id, token),
            ).fetchone()
            if not changed:
                raise ValueError("execution lease lost")
            conn.execute(
                "INSERT INTO tester_research.grants VALUES(%s,%s,'chart.overlay') ON CONFLICT DO NOTHING",
                (actor, digest),
            )
            self._audit(conn, actor, "execution_completed", job_id, digest)
            return self._public(changed)


class ExecutionBusy(Exception):
    pass


def create_execution_app(dsn):
    """Opt-in; caller separately migrates and owns worker polling/lifecycle."""
    from fastapi import Depends, Header, HTTPException

    from .research_ledger import create_persistent_control_app

    ledger = ExecutionLedger(dsn)
    # Reuse the reviewed Host/Origin middleware; no insecure root-mounted router.
    app = create_persistent_control_app(dsn)
    app.state.execution_ledger = ledger

    def identity(authorization: str | None = Header(default=None)):
        try:
            return ledger.authenticate(authorization)
        except Denied:
            raise HTTPException(401, "authentication required") from None
        except Exception:
            raise HTTPException(503, "authentication unavailable") from None

    def invoke(operation):
        try:
            return operation()
        except Denied:
            raise HTTPException(403, "permission denied") from None
        except ExecutionBusy:
            raise HTTPException(429, "execution capacity unavailable") from None
        except ValueError:
            raise HTTPException(409, "execution contract or replay conflict") from None
        except Exception:
            raise HTTPException(503, "execution unavailable") from None

    @app.post("/api/control/research-executions", status_code=202)
    def submit(request: ExecutionRequest, actor=Depends(identity)):
        return invoke(lambda: ledger.submit_execution(actor.actor, request))

    @app.get("/api/control/research-executions/{job_id}")
    def result(job_id: UUID, actor=Depends(identity)):
        return invoke(lambda: ledger.execution_result(actor.actor, str(job_id)))

    return app


def compose_execution(market, execution):
    """Explicit opt-in route composition, preserving child security middleware."""
    from starlette.routing import Route

    from src.integration.combined_routes import compose_research

    market = compose_research(market, execution)
    market.router.routes[0:0] = [
        Route("/api/control/research-executions", endpoint=execution),
        Route("/api/control/research-executions/{rest:path}", endpoint=execution),
    ]
    return market
