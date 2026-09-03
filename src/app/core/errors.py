"""Unified error model and exception handlers (T008).

Every error response has the shape::

    {"error": {"code": str, "message": str, "details": dict | None, "correlation_id": str}}

External-integration failures are mapped into this same model by the modules that own them.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

CORRELATION_ID_HEADER = "X-Correlation-Id"


class AppError(Exception):
    """Base class for domain/application errors carrying a stable ``code``."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


class DomainRuleError(AppError):
    status_code = 422  # Unprocessable Content
    code = "domain_rule_violation"


class UpstreamError(AppError):
    """A connected external system (source connector / execution adapter) failed."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "upstream_error"


def _correlation_id(request: Request) -> str:
    cid = getattr(request.state, "correlation_id", None)
    return str(cid) if cid else request.headers.get(CORRELATION_ID_HEADER, "unknown")


def _payload(
    code: str, message: str, correlation_id: str, details: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "correlation_id": correlation_id,
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers that render every error through the unified model."""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.code, exc.message, _correlation_id(request), exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_payload(
                "validation_error",
                "Request validation failed.",
                _correlation_id(request),
                {"errors": exc.errors()},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "unauthorized",
            403: "permission_denied",
            404: "not_found",
            409: "conflict",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(code, str(exc.detail), _correlation_id(request), None),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload(
                "internal_error",
                "An unexpected error occurred.",
                _correlation_id(request),
                None,
            ),
        )
