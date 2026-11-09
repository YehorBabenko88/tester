"""In-process ASGI checks only; no listener, PostgreSQL, or installed runtime."""

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

from src.api.research_control import Denied, Identity, create_control_app


def test_composition_keeps_research_guards_before_static_and_both_lifespans():
    from src.integration.combined_routes import compose_research

    events = []

    @asynccontextmanager
    async def parent_life(app):
        events.append("market:start")
        yield
        events.append("market:stop")

    @asynccontextmanager
    async def child_life(app):
        events.append("research:start")
        yield
        events.append("research:stop")

    def authenticate(header):
        if header != "Bearer permitted":
            raise Denied("missing credential")
        return Identity("alice")

    market = FastAPI(lifespan=parent_life)
    static = FastAPI()

    @static.get("/{path:path}")
    def fallback(path):
        return PlainTextResponse("static")

    market.mount("/", static)
    research = create_control_app(None, authenticate)
    research.router.lifespan_context = child_life
    compose_research(market, research)
    with TestClient(market, base_url="http://127.0.0.1:8765") as client:
        path = "/api/control/research/" + str(uuid4())
        assert client.get("/index.html").text == "static"
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer permitted"}).status_code == 503
        assert client.get(path, headers={"Origin": "http://foreign.invalid"}).status_code == 403
        assert client.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.get(path, headers={"Host": "foreign.invalid"}).status_code == 400
        assert client.post("/api/control/research", json={}).status_code == 401
    assert events == ["market:start", "research:start", "research:stop", "market:stop"]


def test_child_startup_failure_unwinds_market_lifespan():
    import pytest

    from src.integration.combined_routes import compose_research

    events = []

    @asynccontextmanager
    async def parent_life(app):
        try:
            yield
        finally:
            events.append("market:stop")

    @asynccontextmanager
    async def child_life(app):
        raise RuntimeError("explicit startup failure")
        yield

    market = FastAPI(lifespan=parent_life)
    child = FastAPI(lifespan=child_life)
    compose_research(market, child)
    with pytest.raises(RuntimeError, match="explicit startup failure"), TestClient(market):
        pass
    assert events == ["market:stop"]
