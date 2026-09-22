"""FastAPI application entrypoint.

Wires the app: correlation-id middleware, unified error handlers, an unauthenticated
``/health`` route, and the Phase 2 routers (auth, enterprise, capabilities) under
``/api/v1``.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.middleware import CorrelationIdMiddleware
from app.api.routers import auth as auth_router
from app.api.routers import capabilities as capabilities_router
from app.api.routers import dashboard as dashboard_router
from app.api.routers import data_sources as data_sources_router
from app.api.routers import domain as domain_router
from app.api.routers import enterprise as enterprise_router
from app.api.routers import mappings as mappings_router
from app.api.routers import risks as risks_router
from app.core.config import get_settings
from app.core.errors import CORRELATION_ID_HEADER, register_exception_handlers


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Smart Procurement API",
        version=__version__,
        openapi_url="/openapi.json",
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,  # the SPA sends the httpOnly refresh cookie
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[CORRELATION_ID_HEADER],
    )
    register_exception_handlers(app)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "environment": settings.environment}

    prefix = settings.api_v1_prefix
    app.include_router(auth_router.router, prefix=prefix)
    app.include_router(auth_router.me_router, prefix=prefix)
    app.include_router(enterprise_router.router, prefix=prefix)
    app.include_router(capabilities_router.router, prefix=prefix)
    app.include_router(data_sources_router.router, prefix=prefix)
    app.include_router(mappings_router.router, prefix=prefix)
    app.include_router(domain_router.router, prefix=prefix)
    app.include_router(dashboard_router.router, prefix=prefix)
    app.include_router(risks_router.router, prefix=prefix)

    return app


app = create_app()
