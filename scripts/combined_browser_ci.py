"""Hosted-only synthetic integration fixture; never a native runtime entrypoint."""

import argparse
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from src.api.research_control import Binding, Command, canonical_digest
from src.api.research_ledger import PersistentResearchLedger, validate_research_dsn

ACTOR = "combined-ci-synthetic"
ORIGIN = "http://127.0.0.1:18768"


def require_ci(env):
    if (
        env.get("GITHUB_ACTIONS") != "true"
        or env.get("TESTER_COMBINED_CI") != "synthetic-disposable"
    ):
        raise ValueError("Hosted disposable CI opt-in required")
    dsn = env.get("TEST_RESEARCH_DATABASE_URL", "")
    endpoint = validate_research_dsn(dsn)
    if (
        endpoint.get("host") != "127.0.0.1"
        or endpoint.get("port") != "55472"
        or endpoint.get("dbname") != "tester_research_combined_ci"
    ):
        raise ValueError("Exact disposable CI endpoint required")
    return dsn


def command_for(bundle):
    chart = bundle["chart"]
    return Command(
        **{k: chart[k] for k in Binding.model_fields},
        request_id="b04a434d-5fc6-4dc5-9b21-f7d8f905d500",
        action="chart.overlay",
        expected_artifact_digest=canonical_digest(chart),
    )


def prepare(dsn, path):
    from src.api.db import migrate
    from src.api.research_ledger import migrate_research
    from src.integration.strategy_lab import run_certified_fixture

    # Explicit CI setup, never called by either application factory.
    migrate(dsn)
    migrate_research(dsn)
    bundle = run_certified_fixture()
    store = PersistentResearchLedger(dsn)
    digest = store.publish_bundle(bundle)
    token = store.issue_credential(ACTOR, [digest], ttl_seconds=1200)
    fixture = dict(token=token, command=command_for(bundle).model_dump(mode="json"))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(fixture, stream)


def application(dsn, fixture):
    from src.api.config import Settings
    from src.api.main import create_app
    from src.api.research_ledger import create_persistent_control_app
    from src.integration.combined_routes import compose_research

    market = create_app(Settings(database_url=dsn, port=18768, market_catalog_enabled=False))
    research = create_persistent_control_app(dsn)
    control = research.state.research_control
    actor = control.store.authenticate("Bearer " + fixture["token"])
    if actor.actor != ACTOR:
        raise ValueError("CI actor mismatch")

    async def worker():
        # Each failure terminates the fixture rather than silently hiding a broken dispatch.
        while True:
            ids = await asyncio.to_thread(control.store.pending_job_ids, actor.actor)
            for job in ids:
                await asyncio.to_thread(control.dispatch, actor, job)
            await asyncio.sleep(0.1)

    @asynccontextmanager
    async def lifespan(_):
        task = asyncio.create_task(worker())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    research.router.lifespan_context = lifespan
    return compose_research(market, research)


def gateway(fixture, path):
    from src.api.research_g_client import ResearchGatewayClient

    client = ResearchGatewayClient(ORIGIN, fixture["token"])
    command = Command.model_validate(fixture["command"])
    deadline = time.monotonic() + 30
    while True:
        try:
            job = client.research_submit(command, actor=ACTOR)
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise RuntimeError("CI gateway readiness deadline") from None
            time.sleep(0.2)
    assert client.research_submit(command, actor=ACTOR)["job_id"] == job["job_id"]
    while time.monotonic() < deadline:
        result = client.research_result(job["job_id"], command)
        if result["state"] == "complete":
            fixture["job_id"] = job["job_id"]
            Path(path).write_text(json.dumps(fixture), encoding="utf-8")
            return
        time.sleep(0.1)
    raise RuntimeError("CI dispatch deadline")


def verify(dsn, fixture):
    store = PersistentResearchLedger(dsn)
    command = Command.model_validate(fixture["command"])
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT artifact_digest,rendered_fill_ids FROM tester_research.acknowledgments WHERE actor=%s AND job_id=%s",
            (ACTOR, fixture["job_id"]),
        ).fetchall()
    result = store.read(ACTOR, fixture["job_id"])
    assert result["state"] == "complete" and len(rows) == 1
    assert rows[0]["artifact_digest"] == command.expected_artifact_digest
    assert sorted(rows[0]["rendered_fill_ids"]) == sorted(
        m["fill_id"] for m in result["artifact"]["markers"]
    )
    print(
        "PASS same-job F publication -> PostgreSQL -> authenticated G -> D canvas ACK; synthetic only"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "serve", "gateway", "verify"])
    parser.add_argument("fixture")
    args = parser.parse_args()
    dsn = require_ci(os.environ)
    if args.action == "prepare":
        return prepare(dsn, args.fixture)
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    if args.action == "serve":
        import uvicorn

        uvicorn.run(application(dsn, fixture), host="127.0.0.1", port=18768, access_log=False)
    elif args.action == "gateway":
        gateway(fixture, args.fixture)
    else:
        verify(dsn, fixture)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Connection errors may contain credentials; expose finite class only.
        raise SystemExit("Combined CI fixture failed: " + type(error).__name__) from None
