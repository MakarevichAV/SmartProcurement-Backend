"""FastAPI application entrypoint.

Wires the app: correlation-id middleware, unified error handlers, an unauthenticated
``/health`` route, and the Phase 2 routers (auth, enterprise, capabilities) under
``/api/v1``.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.api.middleware import CorrelationIdMiddleware
from app.api.routers import auth as auth_router
from app.api.routers import capabilities as capabilities_router
from app.api.routers import enterprise as enterprise_router
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

    prefix = settings.api_v1_prefix
    app.include_router(auth_router.router, prefix=prefix)
    app.include_router(auth_router.me_router, prefix=prefix)
    app.include_router(enterprise_router.router, prefix=prefix)
    app.include_router(capabilities_router.router, prefix=prefix)

    return app


app = create_app()
