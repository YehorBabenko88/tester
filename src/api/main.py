import logging
import os
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from contracts.v1.validate import ContractError

from .canonical import dataset_model
from .config import Settings, load_settings
from .db import connect
from .financial_query import (
    FinancialQueryError,
    HttpTime,
    PriceStep,
    is_derived,
    public_financial_error,
    read_derived,
)
from .jobs import AdmissionRejected, Conflict, JobQueue
from .maintenance import storage_status
from .models import (
    DownloadRequest,
    Envelope,
    ErrorEnvelope,
    Health,
    Job,
    Query,
)
from .providers import ProviderDataUnavailable, ProviderUnavailable, UnavailableReader, load_factory
from .scope import (
    CursorCodec,
    CursorError,
    ScopeError,
    SourceIntervalError,
    bound_response,
    resource_query,
)


def error(status, code, message):
    return JSONResponse(
        status_code=status,
        content={"schema_version": "1.0", "error": {"code": code, "message": message}},
    )


def create_app(settings: Settings | None = None, provider=None) -> FastAPI:
    settings = settings or load_settings()
    reader = provider or load_factory(settings.provider_factory, UnavailableReader())
    cursor_codec = CursorCodec()
    queue = (
        JobQueue(settings.dsn, settings.lease_seconds, settings.max_pending)
        if settings.dsn
        else None
    )
    catalog = None
    if settings.market_catalog_enabled:
        from .catalog_store import CatalogStore
        from .market_catalog import BybitCatalogSource
        from .persistent_market_catalog import PersistentCatalogService

        catalog = PersistentCatalogService(
            BybitCatalogSource(), CatalogStore(settings.market_catalog_dsn)
        )

    @asynccontextmanager
    async def lifespan(app):
        if catalog is not None:
            await catalog.start()
        try:
            yield
        finally:
            if catalog is not None:
                await catalog.stop()

    app = FastAPI(
        title="Tester local market API",
        version="1.0.0",
        lifespan=lifespan,
        responses={
            502: {"model": ErrorEnvelope},
            503: {"model": ErrorEnvelope},
            409: {"model": ErrorEnvelope},
            404: {"model": ErrorEnvelope},
            422: {"model": ErrorEnvelope, "description": "Unprocessable Content"},
        },
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    if settings.provider_factory == "src.integration.reference_prices:create_reader":
        from src.integration.reference_prices import ReferencePriceReader, create_price_router

        if not isinstance(reader, ReferencePriceReader):
            raise ValueError("Reference-price source profile requires its exact typed reader")
        app.include_router(create_price_router(reader))
    if catalog is not None:
        from .market_catalog import create_market_router

        app.include_router(create_market_router(catalog))
        app.state.market_catalog = catalog

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

    @app.get("/api/lab/identity", include_in_schema=False)
    def lab_identity():
        from .lab_identity import LabIdentityUnavailable, read_lab_identity

        try:
            return read_lab_identity(settings)
        except LabIdentityUnavailable:
            return error(
                503, "lab_identity_unavailable", "Installed lab identity cannot be verified"
            )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error(422, "validation_error", "Request does not match the API v1 schema")

    @app.exception_handler(FinancialQueryError)
    async def financial_query_error(request, exc):
        # Recheck at serialization: an owned provider can mutate exception attributes.
        code, status = public_financial_error(exc.code)
        return error(status, code, "Financial query cannot be served with these inputs")

    @app.exception_handler(ProviderUnavailable)
    async def provider_error(request, exc):
        return error(503, "provider_unavailable", "Requested provider is not connected")

    @app.exception_handler(ProviderDataUnavailable)
    async def provider_data_error(request, exc):
        # Never echo a provider exception/URL/run/SQL string into a public reason.
        message = ProviderDataUnavailable.MESSAGES.get(
            exc.reason_code, "Requested data could not be verified"
        )
        return error(503, "data_unavailable", message)

    @app.exception_handler(ContractError)
    async def contract_error(request, exc):
        return error(
            502, "provider_contract_error", "Provider response violates approved contract v1"
        )

    @app.exception_handler(ScopeError)
    async def scope_error(request, exc):
        return JSONResponse(
            status_code=502,
            content={
                "schema_version": "1.0",
                "error": {
                    "code": "provider_scope_mismatch",
                    "message": "Provider response does not match requested scope",
                    "context": exc.context,
                },
            },
        )

    @app.exception_handler(CursorError)
    async def cursor_error(request, exc):
        return error(
            422, "cursor_scope_mismatch", "Invalid, stale, or cross-scope continuation token"
        )

    @app.exception_handler(SourceIntervalError)
    async def source_interval_error(request, exc):
        return error(
            422, "source_interval_invalid", "Unsupported source interval for this resource"
        )

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

    @app.exception_handler(AdmissionRejected)
    async def admission_error(request, exc):
        response = error(
            503 if exc.code == "draining" else 429,
            exc.code,
            "Admission paused; retry after backpressure clears",
        )
        response.headers["Retry-After"] = "5"
        return response

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
        provider_status = "unavailable" if isinstance(reader, UnavailableReader) else "configured"
        ok = database == worker == storage["storage"] == "ok" and provider_status == "configured"
        body = Envelope(
            data=Health(
                status="ok" if ok else "degraded",
                database=database,
                worker=worker,
                provider=provider_status,
                instance_id=os.environ.get("TESTER_SERVICE_INSTANCE"),
                **storage,
            )
        )
        return JSONResponse(status_code=200 if ok else 503, content=body.model_dump(mode="json"))

    def validated_query(
        venue: str = "bybit",
        category: str = "linear",
        symbol: str | None = None,
        start_ms: HttpTime | None = None,
        end_ms: HttpTime | None = None,
        query_schema_version: Literal["1.0"] | None = None,
        information_cutoff_ms: HttpTime | None = None,
        price_step: PriceStep | None = None,
        price_step_mode: Literal["explicit", "instrument_tick"] | None = None,
        instrument_tick_ref: str | None = None,
        interval: str = "60",
        source_interval: str | None = None,
        candle_kind: str = "official_trade",
        limit: int = 100,
        cursor: str | None = None,
    ):
        try:
            return Query(
                venue=venue,
                category=category,
                symbol=symbol,
                **({"start_ms": start_ms} if start_ms is not None else {}),
                **({"end_ms": end_ms} if end_ms is not None else {}),
                query_schema_version=query_schema_version,
                information_cutoff_ms=information_cutoff_ms,
                price_step=price_step,
                price_step_mode=price_step_mode,
                instrument_tick_ref=instrument_tick_ref,
                interval=interval,
                source_interval=source_interval,
                candle_kind=candle_kind,
                limit=limit,
                cursor=cursor,
            )
        except ValidationError as exc:
            raise RequestValidationError(exc.errors()) from exc

    # Each operation has a concrete response model in OpenAPI, including provenance.
    for resource, kind in [
        ("instruments", "instrument"),
        ("sources", "source"),
        ("coverage", "coverage"),
        ("candles", "candle"),
        ("trades", "trade"),
        ("footprint", "footprint"),
        ("open-interest", "observation"),
        ("funding", "observation"),
    ]:

        def make_endpoint(name, record_kind):
            def endpoint(query: Annotated[Query, Depends(validated_query)]):
                # Providers must supply the complete envelope. Missing evidence is an error,
                # never repaired using invented availability/provenance or precision.
                derived = is_derived(name, query)
                source_query = resource_query(name, query)
                raw_cursor = cursor_codec.decode(name, source_query)
                provider_query = source_query.model_copy(update={"cursor": raw_cursor})
                value = deepcopy(
                    read_derived(reader, name, source_query, raw_cursor)
                    if derived
                    else reader.read(name, provider_query)
                )
                # The provider can normalize its private query copy. It must never
                # rewrite the original request used as the authority for binding.
                bound_response(name, record_kind, value, source_query)
                value["next_cursor"] = cursor_codec.encode(name, source_query, value["next_cursor"])
                return value

            return endpoint

        app.add_api_route(
            "/api/" + resource,
            make_endpoint(resource, kind),
            methods=["GET"],
            response_model=dataset_model(kind),
            operation_id="read_" + resource,
        )

    @app.get(
        "/api/capabilities",
        response_model=Envelope[dict],
        operation_id="read_capabilities",
        include_in_schema=False,  # Additive preview; preserve pinned public v1 OpenAPI.
    )
    def capabilities(query: Annotated[Query, Depends(validated_query)]):
        # Reader-owned endpoint announcements alone are not sufficient.
        # The bounded discovery path validates real source records, their
        # query scope, canonical provenance and price-only v1.1 distinction.
        from .capability_discovery import discover

        price_router_enabled = (
            settings.provider_factory == "src.integration.reference_prices:create_reader"
        )
        availability = discover(
            reader,
            query,
            authorized=bool(settings.provider_factory),
            price_route_enabled=price_router_enabled,
        )
        # Advertise optional, separately registered UI routes even when no
        # instrument was selected. Never force the browser to probe an absent
        # catalog router or a disconnected source list (404/503).
        availability["features"] = {
            "market_catalog": {
                "enabled": catalog is not None,
                "reason": (
                    "configured" if catalog is not None else "catalog_not_configured"
                ),
            },
            "sources": {
                "enabled": catalog is not None
                and not isinstance(reader, UnavailableReader),
                "reason": (
                    "catalog_and_provider_required"
                    if catalog is None or isinstance(reader, UnavailableReader)
                    else "source_discovery_may_be_attempted"
                ),
            },
        }
        return Envelope(data=availability)

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
