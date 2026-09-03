"""FastAPI application entrypoint.

Phase 1 wires the app skeleton: correlation-id middleware, the unified error handlers, and a
single unauthenticated ``/health`` route. Routers, auth, and DB wiring arrive in Phase 2+.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.api.middleware import CorrelationIdMiddleware
from app.core.config import get_settings
from app.core.errors import register_exception_handlers


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Smart Procurement API",
        version=__version__,
        openapi_url="/openapi.json",
    )
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "environment": settings.environment}

    return app


app = create_app()
