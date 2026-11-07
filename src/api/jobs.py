from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .db import connect
from .models import DownloadRequest, Job, Lease


class Conflict(Exception):
    pass


class LeaseLost(Exception):
    pass


class JobQueue:
    def __init__(self, dsn: str, lease_seconds: int = 60):
        self.dsn = dsn
        self.lease_seconds = lease_seconds

    def _job(self, row, leased=False):
        if row is None:
            return None
        return (Lease if leased else Job).model_validate(row)

    def create(self, request: DownloadRequest) -> Job:
        with connect(self.dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """INSERT INTO tester_platform.jobs(id,request) VALUES (%s,%s)
                           RETURNING *""",
                (uuid4(), Jsonb(request.model_dump())),
            )
            return self._job(cur.fetchone())

    def get(self, id: UUID) -> Job | None:
        with connect(self.dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM tester_platform.jobs WHERE id=%s", (id,))
            return self._job(cur.fetchone())

    def transition(self, id: UUID, action: str) -> Job:
        allowed, target = {
            "pause": (["queued", "running"], "paused"),
            "resume": (["paused"], "queued"),
            "retry": (["failed"], "queued"),
        }[action]
        with connect(self.dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """UPDATE tester_platform.jobs SET state=%s, lease_token=NULL,
                lease_until=NULL, error=NULL, updated_at=now()
                WHERE id=%s AND state=ANY(%s) RETURNING *""",
                (target, id, allowed),
            )
            row = cur.fetchone()
            if row is None:
                if self.get(id) is None:
                    raise KeyError(id)
                raise Conflict(action)
            return self._job(row)

    def claim(self) -> Lease | None:
        with connect(self.dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """WITH candidate AS (
                SELECT id FROM tester_platform.jobs
                WHERE state='queued' OR (state='running' AND lease_until < now())
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
            ) UPDATE tester_platform.jobs j SET state='running', attempts=attempts+1,
                lease_token=%s, lease_until=now()+(%s * interval '1 second'), updated_at=now()
                FROM candidate c WHERE j.id=c.id RETURNING j.*""",
                (uuid4(), self.lease_seconds),
            )
            return self._job(cur.fetchone(), leased=True)

    def checkpoint(self, id, token, checkpoint: dict, progress: float):
        if not 0 <= progress <= 1:
            raise ValueError("progress out of range")
        with connect(self.dsn) as conn:
            result = conn.execute(
                """UPDATE tester_platform.jobs SET checkpoint=%s, progress=%s,
                lease_until=now()+(%s * interval '1 second'), updated_at=now()
                WHERE id=%s AND state='running' AND lease_token=%s AND lease_until>now()""",
                (Jsonb(checkpoint), progress, self.lease_seconds, id, token),
            )
            if result.rowcount != 1:
                raise LeaseLost(id)

    def active(self, id, token) -> bool:
        with connect(self.dsn) as conn:
            return bool(
                conn.execute(
                    """SELECT 1 FROM tester_platform.jobs WHERE id=%s
                AND lease_token=%s AND state='running' AND lease_until>now()""",
                    (id, token),
                ).fetchone()
            )

    def commit(self, id, token, write, checkpoint: dict, progress: float):
        """Fence provider writes and checkpoint in ONE transaction with a locked job row.

        write(conn) must use this connection without commit/rollback and must be bounded.
        External files must be staged immutably; only publish their references in write().
        """
        if not 0 <= progress <= 1:
            raise ValueError("progress out of range")
        with connect(self.dsn) as conn:
            row = conn.execute(
                """SELECT 1 FROM tester_platform.jobs WHERE id=%s
                AND lease_token=%s AND state='running' AND lease_until>now() FOR UPDATE""",
                (id, token),
            ).fetchone()
            if not row:
                raise LeaseLost(id)
            write(conn)
            conn.execute(
                """UPDATE tester_platform.jobs SET checkpoint=%s, progress=%s,
                lease_until=now()+(%s*interval '1 second'), updated_at=now() WHERE id=%s""",
                (Jsonb(checkpoint), progress, self.lease_seconds, id),
            )

    def finish(self, id, token, error: str | None = None):
        with connect(self.dsn) as conn:
            result = conn.execute(
                """UPDATE tester_platform.jobs
                SET state=%s, error=%s, progress=CASE WHEN %s THEN progress ELSE 1 END,
                    lease_token=NULL, lease_until=NULL, updated_at=now()
                WHERE id=%s AND state='running' AND lease_token=%s AND lease_until>now()""",
                ("failed" if error else "completed", error, bool(error), id, token),
            )
            if result.rowcount != 1:
                raise LeaseLost(id)

    def release(self, id, token):
        with connect(self.dsn) as conn:
            conn.execute(
                """UPDATE tester_platform.jobs SET state='queued', lease_token=NULL,
                lease_until=NULL, updated_at=now() WHERE id=%s AND lease_token=%s
                AND state='running' """,
                (id, token),
            )

    def heartbeat(self, worker_id):
        with connect(self.dsn) as conn:
            conn.execute(
                """INSERT INTO tester_platform.workers(id) VALUES(%s)
                ON CONFLICT(id) DO UPDATE SET heartbeat_at=now()""",
                (worker_id,),
            )
