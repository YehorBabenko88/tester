import logging
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings, load_settings
from .db import connect
from .jobs import Conflict, JobQueue
from .maintenance import storage_status
from .models import (
    Candle,
    Coverage,
    DownloadRequest,
    Envelope,
    ErrorEnvelope,
    Footprint,
    Funding,
    Health,
    Instrument,
    Job,
    OpenInterest,
    Query,
    Source,
    Trade,
)
from .providers import ProviderUnavailable, UnavailableReader, load_factory


def error(status, code, message):
    return JSONResponse(
        status_code=status,
        content={"schema_version": "1.0", "error": {"code": code, "message": message}},
    )


def create_app(settings: Settings | None = None, provider=None) -> FastAPI:
    settings = settings or load_settings()
    reader = provider or load_factory(settings.provider_factory, UnavailableReader())
    queue = JobQueue(settings.dsn, settings.lease_seconds) if settings.dsn else None
    app = FastAPI(
        title="Tester local market API",
        version="1.0.0",
        responses={
            503: {"model": ErrorEnvelope},
            409: {"model": ErrorEnvelope},
            404: {"model": ErrorEnvelope},
            422: {"model": ErrorEnvelope},
        },
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_writes(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            allowed = {
                f"http://127.0.0.1:{settings.port}",
                f"http://localhost:{settings.port}",
                f"http://[::1]:{settings.port}",
            }
            if (origin is not None and origin not in allowed) or request.headers.get(
                "sec-fetch-site"
            ) == "cross-site":
                return error(403, "origin_denied", "Cross-origin writes are disabled")
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error(422, "validation_error", "Request does not match the API v1 schema")

    @app.exception_handler(ProviderUnavailable)
    async def provider_error(request, exc):
        return error(503, "provider_unavailable", "Requested provider is not connected")

    @app.exception_handler(psycopg.Error)
    async def database_error(request, exc):
        logging.getLogger("tester.api").error(
            "database_error", extra={"error_type": type(exc).__name__}
        )
        return error(503, "database_unavailable", "Application database is unavailable")

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return error(404, "not_found", "Download does not exist")

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return error(409, "invalid_transition", "Action is not allowed in the current state")

    def require_queue():
        if queue is None:
            raise ProviderUnavailable("database")
        return queue

    @app.get(
        "/api/health", response_model=Envelope[Health], responses={503: {"model": Envelope[Health]}}
    )
    def health():
        database, worker = "unavailable", "unavailable"
        if settings.dsn:
            try:
                with connect(settings.dsn) as conn:
                    conn.execute("SELECT version FROM tester_platform.migrations LIMIT 1")
                    database = "ok"
                    alive = conn.execute("""SELECT EXISTS(SELECT 1 FROM tester_platform.workers
                        WHERE heartbeat_at > now()-interval '30 seconds')""").fetchone()[0]
                    worker = "ok" if alive else "unavailable"
            except psycopg.Error:
                pass
        storage = storage_status(settings)
        ok = database == worker == storage["storage"] == "ok"
        body = Envelope(
            data=Health(
                status="ok" if ok else "degraded", database=database, worker=worker, **storage
            )
        )
        return JSONResponse(status_code=200 if ok else 503, content=body.model_dump(mode="json"))

    def validated_query(
        category: str = "linear",
        symbol: str | None = None,
        start_ms: int = 0,
        end_ms: int = 253402300799000,
        interval: str = "60",
        limit: int = 100,
        cursor: str | None = None,
    ):
        try:
            return Query(
                category=category,
                symbol=symbol,
                start_ms=start_ms,
                end_ms=end_ms,
                interval=interval,
                limit=limit,
                cursor=cursor,
            )
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc

    # Each operation has a concrete response model in OpenAPI, including provenance.
    for resource, model in [
        ("instruments", Instrument),
        ("sources", Source),
        ("coverage", Coverage),
        ("candles", Candle),
        ("trades", Trade),
        ("footprint", Footprint),
        ("open-interest", OpenInterest),
        ("funding", Funding),
    ]:

        def make_endpoint(name):
            def endpoint(query: Annotated[Query, Depends(validated_query)]):
                return {"schema_version": "1.0", **reader.read(name, query)}

            return endpoint

        app.add_api_route(
            "/api/" + resource,
            make_endpoint(resource),
            methods=["GET"],
            response_model=Envelope[list[model]],
            response_model_exclude_unset=True,
            operation_id="read_" + resource,
        )

    @app.post("/api/downloads", response_model=Envelope[Job], status_code=202)
    def downloads(body: DownloadRequest):
        storage = storage_status(settings)
        if storage["storage"] != "ok":
            return error(507, "storage_unavailable", "Storage quota or free disk limit reached")
        return Envelope(data=require_queue().create(body))

    @app.get("/api/downloads/{id}", response_model=Envelope[Job])
    def download(id: UUID):
        job = require_queue().get(id)
        if job is None:
            raise KeyError(id)
        return Envelope(data=job)

    for action in ["pause", "resume", "retry"]:

        def make_action(name):
            def endpoint(id: UUID):
                return Envelope(data=require_queue().transition(id, name))

            return endpoint

        app.add_api_route(
            "/api/downloads/{id}/" + action,
            make_action(action),
            methods=["POST"],
            response_model=Envelope[Job],
            operation_id=action + "_download",
        )

    if settings.frontend_dir.is_dir() and (settings.frontend_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    else:

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def index():
            return '<h1>Tester API</h1><p>Frontend not installed.</p><a href="/docs">API docs</a>'

    return app


def run():
    import uvicorn

    from .logging_setup import configure

    settings = load_settings()
    configure(settings.data_dir, "api")
    import threading

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            access_log=False,
            log_config=None,
        )
    )

    def watch_stop():
        import time

        while not server.should_exit:
            if (settings.data_dir / "stop.request").exists():
                server.should_exit = True
                return
            time.sleep(0.25)

    threading.Thread(target=watch_stop, daemon=True).start()
    server.run()


if __name__ == "__main__":
    run()
