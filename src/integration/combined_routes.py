"""Explicit ASGI composition for the pinned integration candidate.

This does not provision databases, publish strategy bundles, install services, or
start a worker. The caller must supply the reviewed persistent research app and
its lifecycle. Normal product startup remains unchanged and default-off.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.routing import Route


def compose_research(market: FastAPI, research: FastAPI) -> FastAPI:
    """Preserve the child ASGI security stack and both lifespans.

    A prefix Mount strips the path needed by existing research routes, whereas
    include_router drops child middleware. ASGI Route passes the original scope.
    Prepending also avoids the market frontend's catch-all mount.
    """
    if getattr(market.state, "research_composed", False):
        raise ValueError("Research application already composed")
    market_life = market.router.lifespan_context
    research_life = research.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with market_life(app) as parent_state:
            async with research_life(research) as child_state:
                yield {**(parent_state or {}), **(child_state or {})}

    market.router.routes[0:0] = [
        Route("/api/control/research", endpoint=research),
        Route("/api/control/research/{rest:path}", endpoint=research),
    ]
    market.router.lifespan_context = lifespan
    market.state.research_composed = True
    market.state.research_control = (
        research.state.research_control if hasattr(research.state, "research_control") else None
    )
    return market
